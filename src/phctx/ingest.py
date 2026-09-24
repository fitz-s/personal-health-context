"""iPhone → Mac ingest (contracts/ingest_protocol.md). Dedicated device channel; no read/query rights.

TLS with a locally generated self-signed certificate that the phone pins by SHA-256. Device tokens are
stored hashed. Batches commit observations + stream sequence + receipt in one transaction.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import secrets
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jsonschema

from .apple_export import origin_key
from .store import Store, StoreError, dump, utc_in, utcnow

log = logging.getLogger('phctx.ingest')
SCHEMA = json.loads((Path(__file__).resolve().parents[2] / 'contracts' / 'apple_batch.schema.json').read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
MAX_BODY = 8 * 1024 * 1024
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def sha(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


# ---- pairing / devices (operator + protocol) ------------------------------------------------
def new_pairing_code(store: Store, minutes: int = 10) -> str:
    code = ''.join(secrets.choice(ALPHABET) for _ in range(8))
    with store.transaction() as c:
        c.execute('INSERT INTO pairing_codes VALUES(?,?,NULL)', (sha(code), utc_in(minutes * 60)))
    return code


def pair(store: Store, code: str, installation_id: str, device_name: str) -> dict:
    if not (isinstance(installation_id, str) and 8 <= len(installation_id) <= 100 and installation_id.isascii()
            and all(ch.isalnum() or ch == '-' for ch in installation_id)):
        raise StoreError('invalid_request', 'installation_id must be a UUID-like string.')
    name = (device_name or 'iPhone')[:100]
    token = secrets.token_urlsafe(32)
    source_id = f'apple_health:{installation_id}'
    digest = sha((code or '').upper())
    with store.transaction() as c:
        row = c.execute('SELECT expires_at, used_at FROM pairing_codes WHERE code_sha256=?', (digest,)).fetchone()
        valid = bool(row) and not row['used_at'] and row['expires_at'] >= utcnow()
        if not valid:
            # Brute-force brake: 10 wrong attempts burn every outstanding code (operator issues a new one).
            fails = int((c.execute("SELECT value FROM meta WHERE key='pairing_failures'").fetchone() or ['0'])[0]) + 1
            if fails >= 10:
                c.execute('UPDATE pairing_codes SET used_at=? WHERE used_at IS NULL', (utcnow(),))
                fails = 0
            c.execute("INSERT INTO meta VALUES('pairing_failures', ?) ON CONFLICT(key) DO UPDATE SET "
                      'value=excluded.value', (str(fails),))
        else:
            c.execute('UPDATE pairing_codes SET used_at=? WHERE code_sha256=?', (utcnow(), digest))
            c.execute("DELETE FROM meta WHERE key='pairing_failures'")
            c.execute("INSERT INTO sources(id,label,policy,state) VALUES(?,?,'durable','paired') ON CONFLICT(id) DO "
                      "UPDATE SET state='paired'", (source_id, f'Apple Health via {name}'))
            c.execute('INSERT INTO devices VALUES(?,?,?,?,?,NULL) ON CONFLICT(installation_id) DO UPDATE SET '
                      'device_name=excluded.device_name, token_sha256=excluded.token_sha256, revoked_at=NULL, '
                      'paired_at=excluded.paired_at', (installation_id, name, sha(token), source_id, utcnow()))
    if not valid:
        raise StoreError('pairing_invalid', 'Pairing code is wrong, expired or already used.')
    return {'device_token': token, 'installation_id': installation_id, 'source_id': source_id}


def revoke(store: Store, installation_id: str) -> None:
    with store.transaction() as c:
        c.execute('UPDATE devices SET revoked_at=? WHERE installation_id=?', (utcnow(), installation_id))


def device_for(store: Store, token: str) -> dict | None:
    if not token:
        return None
    with store.connect() as c:
        row = c.execute('SELECT * FROM devices WHERE token_sha256=? AND revoked_at IS NULL', (sha(token),)).fetchone()
    return dict(row) if row else None


# ---- batches --------------------------------------------------------------------------------
class ProtocolError(Exception):
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body


def accept_batch(store: Store, device: dict, batch: dict) -> dict:
    errors = sorted(VALIDATOR.iter_errors(batch), key=lambda e: list(e.path))
    if errors:
        raise ProtocolError(422, {'error': 'invalid_batch', 'detail': errors[0].message[:300]})
    if batch['installation_id'] != device['installation_id']:
        raise ProtocolError(403, {'error': 'installation_mismatch'})
    stream, seq, bid = batch['stream'], batch['sequence'], batch['batch_id']
    request_id = f'hk:{device["installation_id"]}:{bid}'
    body_hash = hashlib.sha256(dump(batch).encode()).hexdigest()
    prior = store.receipt(request_id)
    if prior is not None:
        if prior.get('batch_body_sha256') != body_hash:
            raise ProtocolError(409, {'error': 'batch_conflict'})
        return prior
    with store.connect() as c:
        last = c.execute('SELECT last_sequence, last_batch_id FROM sync_streams WHERE installation_id=? AND stream=?',
                         (device['installation_id'], stream)).fetchone()
    exp_seq = last['last_sequence'] + 1 if last else 0
    exp_prev = last['last_batch_id'] if last else None
    if seq != exp_seq or batch['previous_batch_id'] != exp_prev:
        raise ProtocolError(409, {'error': 'sequence_gap', 'expected_sequence': exp_seq,
                                  'expected_previous_batch_id': exp_prev})
    samples = []
    for s in batch['samples']:
        x = dict(s)
        x['origin_key'] = origin_key(s['metric'], s['start_at'], s['end_at'], s.get('value_num'),
                                     s.get('value_text'), s.get('source_name'))
        samples.append(x)

    def advance(c) -> dict:
        cur = c.execute('SELECT last_sequence FROM sync_streams WHERE installation_id=? AND stream=?',
                        (device['installation_id'], stream)).fetchone()
        if (cur['last_sequence'] + 1 if cur else 0) != seq:
            raise StoreError('sequence_race', 'Concurrent batch for this stream; retry.')
        c.execute('INSERT INTO sync_streams VALUES(?,?,?,?,?) ON CONFLICT(installation_id, stream) DO UPDATE SET '
                  'last_sequence=excluded.last_sequence, last_batch_id=excluded.last_batch_id, '
                  'updated_at=excluded.updated_at', (device['installation_id'], stream, seq, bid, utcnow()))
        return {'batch_id': bid, 'batch_body_sha256': body_hash, 'committed': True, 'server_time': utcnow()}
    try:
        out = store.ingest_batch(request_id=request_id, source_id=device['source_id'], samples=samples,
                                 deleted_ids=batch['deleted_ids'], cursor=f'{stream}:{seq}',
                                 coverage={'stream': stream, 'query_completed_at': batch['query_completed_at'],
                                           **batch['coverage']}, _extra=advance)
    except StoreError as e:
        if e.code in {'storage_busy', 'storage_unavailable', 'sequence_race'}:
            raise ProtocolError(503, {'error': 'storage_unavailable'}) from e
        raise ProtocolError(422, {'error': 'invalid_batch', 'detail': e.code}) from e
    return out


def stream_status(store: Store, device: dict) -> dict:
    with store.connect() as c:
        rows = c.execute('SELECT stream, last_sequence, last_batch_id FROM sync_streams WHERE installation_id=?',
                         (device['installation_id'],)).fetchall()
    return {'server_time': utcnow(),
            'streams': {r['stream']: {'last_sequence': r['last_sequence'], 'last_batch_id': r['last_batch_id']}
                        for r in rows}}


# ---- TLS certificate ------------------------------------------------------------------------
def ensure_cert(directory: Path, hosts: list[str]) -> tuple[Path, Path, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert_p, key_p = directory / 'ingest-cert.pem', directory / 'ingest-key.pem'
    if not cert_p.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'phctx-ingest')])
        sans: list[x509.GeneralName] = []
        for h in hosts:
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(h)))
            except ValueError:
                sans.append(x509.DNSName(h))
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                # Apple TLS trust rejects server certs without serverAuth EKU (-67609), even when pinned.
                .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                             data_encipherment=False, key_agreement=True, key_cert_sign=False,
                                             crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
        key_p.chmod(0o600)
        cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    der = ssl.PEM_cert_to_DER_cert(cert_p.read_text())
    return cert_p, key_p, hashlib.sha256(der).hexdigest()


def lan_addresses() -> list[str]:
    out = {'127.0.0.1'}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))  # no packet sent; selects the LAN interface address
        out.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(out)


# ---- HTTP server ----------------------------------------------------------------------------
def make_server(store: Store, host: str, port: int, cert: Path, key: Path) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = 'phctx-ingest/1'
        sys_version = ''
        timeout = 20  # per-connection socket timeout: bounds slow bodies and stalled TLS handshakes

        def log_message(self, fmt, *args):  # redacted access log: method/path/status only
            log.info('%s %s', self.command, self.path.split('?')[0])

        def reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self) -> dict:
            raw = self.headers.get('Content-Length') or ''
            if not raw.isdigit() or int(raw) == 0:
                raise ProtocolError(411 if not raw else 400, {'error': 'length_required'})
            n = int(raw)
            if n > MAX_BODY:
                raise ProtocolError(413, {'error': 'too_large'})
            try:
                return json.loads(self.rfile.read(n) or b'{}')
            except ValueError as e:
                raise ProtocolError(400, {'error': 'malformed_json'}) from e

        def device(self) -> dict:
            auth = self.headers.get('Authorization', '')
            token = auth[7:] if auth.startswith('Bearer ') else ''
            d = device_for(store, token)
            if not d:
                raise ProtocolError(401, {'error': 'token_invalid'})
            return d

        def do_POST(self):  # noqa: N802
            try:
                if self.path == '/v1/pair':
                    b = self.body()
                    try:
                        self.reply(200, pair(store, b.get('pairing_code', ''), b.get('installation_id', ''),
                                             b.get('device_name', '')))
                    except StoreError as e:
                        self.reply(403 if e.code == 'pairing_invalid' else 400, {'error': e.code})
                elif self.path == '/v1/batches':
                    d = self.device()
                    self.reply(200, accept_batch(store, d, self.body()))
                else:
                    self.reply(404, {'error': 'not_found'})
            except ProtocolError as e:
                self.reply(e.status, e.body)
            except Exception:  # never leak internals to the device
                log.exception('ingest_error')
                self.reply(503, {'error': 'storage_unavailable'})

        def do_GET(self):  # noqa: N802
            try:
                if self.path == '/v1/status':
                    self.reply(200, stream_status(store, self.device()))
                else:
                    self.reply(404, {'error': 'not_found'})
            except ProtocolError as e:
                self.reply(e.status, e.body)

    srv = ThreadingHTTPServer((host, port), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    # Handshake lazily in the per-connection thread (first read, under `timeout`), not inside accept().
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    return srv


def serve_background(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t

