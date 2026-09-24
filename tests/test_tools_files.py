"""Tools, HTTPS file capture and stdio MCP integration tests with synthetic fixtures."""
import asyncio
import hashlib
import http.server
import io
import logging
import os
import ssl
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from phctx import download
from phctx.store import Store
from phctx.tools import ToolContext, Tools
from phctx import mcp_server

AT = '2026-09-20T12:00:00-05:00'
ROOT = Path(__file__).resolve().parents[1]


def make_certificate(directory, hostname='localhost'):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / 'localhost.pem', directory / 'localhost-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    trust = ssl.create_default_context(cafile=str(cert_path))
    return cert_path, key_path, trust


def text_pdf(pages=2):
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    font_ref = writer._add_object(font)
    for index in range(1, pages + 1):
        page = writer.add_blank_page(width=612, height=792)
        content = DecodedStreamObject()
        content.set_data(f'BT /F1 12 Tf 72 720 Td (SYNTHETIC page {index}) Tj ET'.encode())
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
            NameObject('/F1'): font_ref})})
        page[NameObject('/Contents')] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class FileHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    data = b'SYNTHETIC local HTTPS payload'
    status = 200
    headers = {}
    redirect = None
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        if type(self).redirect:
            self.send_response(302)
            self.send_header('Location', type(self).redirect)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.send_response(type(self).status)
        for key, value in type(self).headers.items():
            self.send_header(key, value)
        if 'Content-Length' not in type(self).headers:
            self.send_header('Content-Length', str(len(type(self).data)))
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()
        if type(self).status == 200:
            self.wfile.write(type(self).data)

    def log_message(self, *args):
        pass


class ToolsFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'data', 'synthetic')
        self.tools = Tools(ToolContext(store=self.store))

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_image_renders_any_pdf_page_for_a_fresh_conversation(self):
        sha = self.store.put_attachment_bytes(request_id='SYNTHETIC-render', data=text_pdf(3), filename='s.pdf',
                                              mime='application/pdf', text='SYNTHETIC scan',
                                              occurred_at=AT)['object_sha256']
        out = self.tools.call('context_read_original', {'object_sha256': sha, 'mode': 'page_image', 'start_page': 2})
        self.assertFalse(out.is_error, out.data)
        jpg, mime = out.image
        self.assertEqual((mime, jpg[:3]), ('image/jpeg', b'\xff\xd8\xff'))
        self.assertEqual((out.data['page'], out.data['total_pages']), (2, 3))
        bad = self.tools.call('context_read_original', {'object_sha256': sha, 'mode': 'page_image', 'start_page': 9})
        self.assertEqual(bad.data['error'], 'page_out_of_range')
        txt = self.store.put_attachment_bytes(request_id='SYNTHETIC-txt', data=b'SYNTHETIC', filename='s.txt',
                                              mime='text/plain', text='SYNTHETIC', occurred_at=AT)['object_sha256']
        self.assertEqual(self.tools.call('context_read_original', {'object_sha256': txt, 'mode': 'page_image'})
                         .data['error'], 'not_renderable')

    def test_invalid_arguments_return_invalid_arguments(self):
        result = self.tools.call('context_capture', {'request_id': 'x'})
        self.assertTrue(result.is_error)
        self.assertEqual(result.data['error'], 'invalid_arguments')

    def test_unknown_tool_returns_unknown_tool(self):
        self.assertEqual(self.tools.call('SYNTHETIC_unknown', {}).data['error'], 'unknown_tool')

    def test_readonly_profile_hides_writers_and_rejects_capture(self):
        tools = Tools(ToolContext(store=self.store, profile='readonly'))
        self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in tools.listed()))
        result = tools.call('context_capture', dict(request_id='readonly', kind='note',
                                                   text='SYNTHETIC blocked', occurred_at=AT))
        self.assertEqual(result.data['error'], 'write_disabled_in_profile')
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0], 0)

    def _https_server(self, *, data=None, status=200, headers=None, redirect=None):
        cert, key, trust = make_certificate(self.base)
        FileHandler.data = data if data is not None else b'SYNTHETIC local HTTPS payload'
        FileHandler.status, FileHandler.headers, FileHandler.redirect = status, headers or {}, redirect
        FileHandler.hits = 0
        server = QuietServer(('127.0.0.1', 0), FileHandler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, trust

    def _file_call(self, server, trust, request='capture', **kwargs):
        tools = Tools(ToolContext(
            store=self.store, allowed_download_hosts=['localhost'], max_file_bytes=kwargs.pop('max_file_bytes', 1024 * 1024),
            fetch_options={'allow_nonpublic': True, 'context': trust,
                           'resolver': lambda host, port: ['127.0.0.1']}, **kwargs))
        return tools.call('context_capture_file', {
            'request_id': request,
            'file': {'download_url': f'https://localhost:{server.server_address[1]}/download',
                     'file_id': 'SYNTHETIC-file-id', 'mime_type': 'application/pdf', 'file_name': 'synthetic.pdf'},
            'text': 'SYNTHETIC uploaded document', 'occurred_at': AT,
        })

    def test_https_file_capture_saves_hash_bytes_and_pdf_page_evidence(self):
        body = text_pdf(2)
        server, trust = self._https_server(data=body, headers={'Content-Type': 'application/pdf'})
        result = self._file_call(server, trust)
        self.assertFalse(result.is_error, result.data)
        receipt = result.data
        self.assertEqual(receipt['object_sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(self.store.read_object(receipt['object_sha256']), body)
        self.assertEqual(receipt['extraction_status'], 'done')
        self.assertEqual(receipt['pages'], 2)
        pages = self.store.read_pages(receipt['object_sha256'], 1, 2)
        self.assertEqual([page['text'].strip() for page in pages['pages']],
                         ['SYNTHETIC page 1', 'SYNTHETIC page 2'])
        self.assertEqual(pages['evidence_ids'], [f"obj:{receipt['object_sha256']}#p1",
                                                  f"obj:{receipt['object_sha256']}#p2"])

    def test_same_file_request_id_skips_a_second_https_fetch(self):
        server, trust = self._https_server()
        first = self._file_call(server, trust, request='SYNTHETIC-idempotent')
        hits_after_first = FileHandler.hits
        second = self._file_call(server, trust, request='SYNTHETIC-idempotent')
        self.assertEqual(first.data, second.data)
        self.assertEqual(FileHandler.hits, hits_after_first)
        self.assertEqual(hits_after_first, 1)

    def test_capture_refuses_host_outside_allowlist_without_persisting(self):
        server, trust = self._https_server()
        tools = Tools(ToolContext(store=self.store, allowed_download_hosts=['elsewhere.test'], fetch_options={
            'allow_nonpublic': True, 'context': trust, 'resolver': lambda h, p: ['127.0.0.1']}))
        result = tools.call('context_capture_file', {'request_id': 'SYNTHETIC-refuse-host',
            'file': {'download_url': f'https://localhost:{server.server_address[1]}/', 'file_id': 'synthetic'},
            'text': 'SYNTHETIC refusal', 'occurred_at': AT})
        self.assertEqual(result.data['error'], 'file_host_not_allowlisted')
        self._assert_no_objects()

    def test_capture_refuses_http_url_without_persisting(self):
        self._assert_fetch_refused('http://localhost/file', ['localhost'], 'file_url_invalid')

    def test_capture_refuses_url_credentials_without_persisting(self):
        self._assert_fetch_refused('https://user:pass@localhost/file', ['localhost'], 'file_url_invalid')

    def _assert_fetch_refused(self, url, allowlist, expected, *, resolver=None, allow_nonpublic=False):
        tools = Tools(ToolContext(store=self.store, allowed_download_hosts=allowlist,
                                  fetch_options={'allow_nonpublic': allow_nonpublic,
                                                 'context': ssl.create_default_context(),
                                                 'resolver': resolver or (lambda h, p: ['8.8.8.8'])}))
        result = tools.call('context_capture_file', {'request_id': 'SYNTHETIC-refused-' + expected,
            'file': {'download_url': url, 'file_id': 'SYNTHETIC-id'}, 'text': 'SYNTHETIC refused',
            'occurred_at': AT})
        self.assertEqual(result.data['error'], expected)
        self._assert_no_objects()

    def test_capture_refuses_private_loopback_link_local_and_ipv6_loopback_resolutions(self):
        for address in ('10.0.0.5', '127.0.0.1', '169.254.169.254', '::1'):
            with self.subTest(address=address):
                self._assert_fetch_refused('https://localhost/file', ['localhost'], 'file_url_blocked',
                                           resolver=lambda h, p, ip=address: [ip])

    def test_capture_revalidates_redirected_host(self):
        server, trust = self._https_server(redirect='https://unlisted.test/private')
        tools = Tools(ToolContext(store=self.store, allowed_download_hosts=['localhost'], fetch_options={
            'allow_nonpublic': True, 'context': trust, 'resolver': lambda h, p: ['127.0.0.1']}))
        result = tools.call('context_capture_file', {'request_id': 'SYNTHETIC-redirect',
            'file': {'download_url': f'https://localhost:{server.server_address[1]}/', 'file_id': 'SYNTHETIC'},
            'text': 'SYNTHETIC redirected', 'occurred_at': AT})
        self.assertEqual(result.data['error'], 'file_host_not_allowlisted')
        self._assert_no_objects()

    def test_capture_maps_forbidden_file_to_expired(self):
        server, trust = self._https_server(status=403)
        result = self._file_call(server, trust, request='SYNTHETIC-403')
        self.assertEqual(result.data['error'], 'file_url_expired')
        self._assert_no_objects()

    def test_capture_refuses_body_over_size_cap(self):
        server, trust = self._https_server(data=b'SYNTHETIC oversize')
        result = self._file_call(server, trust, request='SYNTHETIC-size', max_file_bytes=4)
        self.assertEqual(result.data['error'], 'file_size')
        self._assert_no_objects()

    def test_capture_refuses_content_length_larger_than_body(self):
        server, trust = self._https_server(data=b'SYNTHETIC short', headers={'Content-Length': '100'})
        result = self._file_call(server, trust, request='SYNTHETIC-truncated')
        self.assertEqual(result.data['error'], 'file_download_failed')
        self._assert_no_objects()

    def _assert_no_objects(self):
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM objects').fetchone()[0], 0)

    def test_download_public_ip_excludes_nonpublic_and_mapped_ranges(self):
        for address in ('10.0.0.1', '127.0.0.1', '169.254.0.1', '100.64.0.1', '224.0.0.1',
                        '::ffff:127.0.0.1'):
            with self.subTest(address=address):
                self.assertFalse(download.public_ip(address))
        self.assertTrue(download.public_ip('8.8.8.8'))

    def test_malformed_pdf_keeps_original_and_reports_extraction_failure(self):
        body = b'%PDF-SYNTHETIC not a parseable PDF'
        server, trust = self._https_server(data=body, headers={'Content-Type': 'application/pdf'})
        result = self._file_call(server, trust, request='SYNTHETIC-bad-pdf')
        self.assertTrue(result.data['original_saved'])
        self.assertEqual(self.store.read_object(result.data['object_sha256']), body)
        self.assertEqual(result.data['extraction_status'], 'failed')

    def test_magic_sniffing_overrides_declared_image_mime(self):
        body = b'SYNTHETIC ordinary text despite jpeg declaration'
        result = self.store.put_attachment_bytes(request_id='SYNTHETIC-mime', data=body, filename='sample.jpg',
                                                mime='image/jpeg', text='SYNTHETIC mismatch', occurred_at=AT)
        self.assertEqual(result['detected_mime'], 'text/plain')

    def test_log_redactor_removes_signed_url_and_bearer_token(self):
        record = logging.LogRecord('synthetic', logging.INFO, __file__, 1,
                                   'SYNTHETIC https://files.test/download?signature=secret Bearer xyz', (), None)
        self.assertTrue(mcp_server.Redactor().filter(record))
        self.assertNotIn('https://', record.getMessage())
        self.assertNotIn('signature=secret', record.getMessage())
        self.assertNotIn('Bearer xyz', record.getMessage())

    def test_official_mcp_stdio_tools_capture_and_readonly_profile(self):
        config = self.base / 'config.toml'
        config.write_text(f'[app]\nprofile="synthetic"\nroot="{self.base / "mcp-data"}"\n')
        env = {**os.environ, 'PHCTX_CONFIG': str(config), 'PYTHONPATH': str(ROOT / 'src')}

        async def session_call(profile, tool_name, args):
            params = StdioServerParameters(command=str(ROOT / '.venv' / 'bin' / 'python'),
                                           args=['-m', 'phctx', 'mcp', '--profile', profile], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    result = await session.call_tool(tool_name, args)
                    return listed, result

        listed, _ = asyncio.run(session_call('full', 'context_capture', {
            'request_id': 'SYNTHETIC-stdio', 'kind': 'note', 'text': 'SYNTHETIC captured via MCP', 'occurred_at': AT}))
        self.assertEqual(len(listed.tools), 11)
        file_tool = next(tool for tool in listed.tools if tool.name == 'context_capture_file')
        self.assertEqual(file_tool.meta['openai/fileParams'], ['file'])
        _, found = asyncio.run(session_call('full', 'context_search', {'query': 'SYNTHETIC captured via MCP'}))
        self.assertIn('SYNTHETIC captured via MCP', found.structured_content['records'][0]['text'])
        readonly_tools, _ = asyncio.run(session_call('readonly', 'context_search', {'query': 'SYNTHETIC'}))
        self.assertTrue(all(tool.annotations.read_only_hint for tool in readonly_tools.tools))


if __name__ == '__main__':
    unittest.main(verbosity=2)
