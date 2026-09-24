"""Secure-slice regressions (F17-F22, F28-F29, F34-F35, F37-F38, F40, F42-F43, F55). Synthetic, loopback only;
the real Keychain, Codex login and production root are never touched."""
import hashlib
import json
import os
import socket
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from phctx import backup, config, download, extract, ingest, model
from phctx.config import Config
from phctx.store import Store, StoreError

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {'src': 'a' * 64, 'prompts/foreground.md': 'p' * 64, 'prompts/background.md': 'q' * 64}
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'evals'), str(ROOT / 'tests')]
from test_tools_files import make_certificate  # noqa: E402

AT = '2026-09-20T12:00:00-05:00'
SVC = 'phctx-ingest-tls'


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


def no_plain_key_under(root: Path) -> bool:
    return not any(p.is_file() and b'BEGIN PRIVATE KEY' in p.read_bytes() for p in root.rglob('*'))


# ---- F17: TLS private key lives in the Keychain, not on disk ---------------------------------------
class IngestKeyTests(Tmp):
    def test_key_goes_to_keychain_and_no_private_key_file_is_created(self):
        kc = {}
        cert, key, fp = ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'], keychain=kc)
        self.assertEqual(sorted(os.listdir(self.base / 'tls')), ['ingest-cert.pem'])
        self.assertRegex(kc[SVC], r'^[0-9a-f]{64}$')
        self.assertTrue(no_plain_key_under(self.base))
        self.assertEqual(ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'], keychain=kc)[2], fp)  # restart

    def test_legacy_plaintext_key_is_moved_into_keychain_keeping_the_pin(self):
        from cryptography.hazmat.primitives import serialization
        kc = {}
        _, pem, fp = ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'], keychain=kc)
        key = serialization.load_pem_private_key(pem, None)
        (self.base / 'tls' / 'ingest-key.pem').write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        fresh = {}
        self.assertEqual(ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'], keychain=fresh)[2], fp)
        self.assertEqual(fresh[SVC], kc[SVC])
        self.assertFalse((self.base / 'tls' / 'ingest-key.pem').exists())

    def test_pair_device_uses_the_login_keychain_and_keeps_the_pin_across_processes(self):
        from phctx import cli
        cfgp = self.base / 'config.toml'
        cfgp.write_text(f'[app]\nprofile = "synthetic"\nroot = "{self.base / "root"}"\n')
        login = {}
        pins = []
        for _ in range(2):  # _MEMORY is cleared between runs, as in two separate processes
            ingest._MEMORY.clear()
            with patch.object(config, 'keychain_get', side_effect=lambda s, a='phctx': login.get(s)), \
                    patch.object(config, 'keychain_set', side_effect=lambda s, v, a='phctx': login.__setitem__(s, v)), \
                    patch('sys.stdout') as so:
                self.assertEqual(cli.main(['--config', str(cfgp), 'pair-device']), 0)
            pins.append(json.loads(''.join(c.args[0] for c in so.write.call_args_list))['cert_sha256'])
        self.assertIn(SVC, login)
        self.assertEqual(pins[0], pins[1])

    def test_default_keychain_is_in_memory_never_the_login_keychain(self):
        with patch.object(config, 'keychain_get', side_effect=AssertionError('real keychain read')), \
                patch.object(config, 'keychain_set', side_effect=AssertionError('real keychain write')):
            ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'])

    def test_server_loads_key_through_a_transient_encrypted_0600_file(self):
        cert, key, _ = ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'], keychain={})
        seen = {}
        real = ssl.SSLContext.load_cert_chain

        def spy(ctx, certfile, keyfile=None, password=None):
            p = Path(keyfile)
            seen.update(path=p, mode=p.stat().st_mode & 0o777, dmode=p.parent.stat().st_mode & 0o777,
                        body=p.read_bytes())
            return real(ctx, certfile, keyfile, password)
        store = Store(self.base / 'store', 'synthetic')
        with patch.object(ssl.SSLContext, 'load_cert_chain', spy):
            srv = ingest.make_server(store, '127.0.0.1', 0, cert, key)
        self.addCleanup(srv.server_close)
        self.assertEqual((seen['mode'], seen['dmode']), (0o600, 0o700))
        self.assertIn(b'ENCRYPTED PRIVATE KEY', seen['body'])
        self.assertFalse(seen['path'].parent.exists())

    def test_keychain_set_refuses_secrets_security_would_truncate(self):
        with patch.object(config.subprocess, 'run', side_effect=AssertionError('must not call security')):
            for bad in ('a' * 129, 'line\nbreak'):
                with self.assertRaises(ValueError):
                    config.keychain_set('phctx-test', bad)

    def test_production_keychain_adapter_routes_to_security_wrappers(self):
        with patch.object(config, 'keychain_get', return_value='ab') as g, \
                patch.object(config, 'keychain_set') as s:
            self.assertEqual(config.KEYCHAIN.get(SVC), 'ab')
            config.KEYCHAIN[SVC] = 'cd'
        g.assert_called_once_with(SVC)
        s.assert_called_once_with(SVC, 'cd')


# ---- F18: ingest connection bound, handshake timeout, absolute request deadline ----------------------
class IngestLimitsTests(Tmp):
    def server(self, **consts):
        for k, v in consts.items():
            p = patch.object(ingest, k, v)
            p.start()
            self.addCleanup(p.stop)
        cert, key, _ = ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'], keychain={})
        srv = ingest.make_server(Store(self.base / 'store', 'synthetic'), '127.0.0.1', 0, cert, key)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.ctx = ssl.create_default_context(cafile=str(cert))
        self.ctx.check_hostname = False
        return srv.server_address[1]

    def closed_within(self, sock, seconds) -> bool:
        sock.settimeout(seconds)
        try:
            return sock.recv(1) == b''
        except TimeoutError:
            return False
        except OSError:
            return True

    def status(self, port) -> int:
        with socket.create_connection(('127.0.0.1', port), 3) as raw, \
                self.ctx.wrap_socket(raw, server_hostname='127.0.0.1') as s:
            s.sendall(b'GET /v1/status HTTP/1.1\r\nHost: x\r\n\r\n')
            return int(s.recv(64).split()[1])

    def test_stalled_handshake_is_closed_after_handshake_timeout(self):
        port = self.server(HANDSHAKE_S=0.5)
        with socket.create_connection(('127.0.0.1', port), 3) as raw:
            self.assertTrue(self.closed_within(raw, 3))

    def test_slow_progress_body_hits_absolute_deadline(self):
        port = self.server(REQUEST_S=1.5)
        raw = socket.create_connection(('127.0.0.1', port), 3)
        s = self.ctx.wrap_socket(raw, server_hostname='127.0.0.1')
        self.addCleanup(s.close)
        s.sendall(b'POST /v1/pair HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n\r\n')
        t0 = time.monotonic()
        with self.assertRaises(OSError):  # one byte per 0.2 s never trips the idle timeout; the deadline cuts it
            while time.monotonic() - t0 < 6:
                s.sendall(b'x')
                time.sleep(0.2)
        self.assertLess(time.monotonic() - t0, 4)

    def test_early_reply_with_unread_body_reaches_the_client(self):
        # 401 is sent before the body is read; closing with unread input must not RST the reply away.
        port = self.server()
        body = b'x' * 65536
        for _ in range(20):
            with socket.create_connection(('127.0.0.1', port), 3) as raw, \
                    self.ctx.wrap_socket(raw, server_hostname='127.0.0.1') as s:
                s.sendall(b'POST /v1/batches HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer SYNTHETIC-bad\r\n'
                          b'Content-Length: %d\r\n\r\n' % len(body) + body)
                data = b''
                while b'token_invalid' not in data:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.assertIn(b'401', data.split(b'\r\n', 1)[0])
                self.assertIn(b'token_invalid', data)

    def test_excess_connections_are_closed_and_capacity_recovers(self):
        port = self.server(MAX_CONN=2, HANDSHAKE_S=5)
        stalled = [socket.create_connection(('127.0.0.1', port), 3) for _ in range(2)]
        time.sleep(0.2)
        with socket.create_connection(('127.0.0.1', port), 3) as extra:
            self.assertTrue(self.closed_within(extra, 1))
        for s in stalled:
            s.close()
        deadline = time.monotonic() + 3
        while True:
            try:
                self.assertEqual(self.status(port), 401)
                break
            except (OSError, IndexError, ValueError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)


    def test_one_peer_cannot_take_every_slot(self):
        port = self.server(MAX_CONN=4, PER_PEER=2, HANDSHAKE_S=5)
        stalled = [socket.create_connection(('127.0.0.1', port), 3) for _ in range(2)]
        self.addCleanup(lambda: [s.close() for s in stalled])
        time.sleep(0.2)
        with socket.create_connection(('127.0.0.1', port), 3) as extra:  # same peer, over its share
            self.assertTrue(self.closed_within(extra, 1))
        for s in stalled:
            s.close()
        deadline = time.monotonic() + 3
        while True:  # the peer's share comes back once its connections end
            try:
                self.assertEqual(self.status(port), 401)
                break
            except (OSError, IndexError, ValueError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

# ---- F19: one monotonic deadline across DNS, connect, headers and body ------------------------------
class SlowTLS:
    def __init__(self, base: Path, script):
        cert, key, self.trust = make_certificate(base)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.create_server(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        threading.Thread(target=self.serve, args=(script,), daemon=True).start()

    def serve(self, script):
        try:
            raw, _ = self.sock.accept()
            with self.ctx.wrap_socket(raw, server_side=True) as c:
                buf = b''
                while b'\r\n\r\n' not in buf:
                    buf += c.recv(4096)
                script(c, self.stop)
        except OSError:
            pass

    def close(self):
        self.stop.set()
        self.sock.close()


def drip(prefix: bytes):
    def script(c, stop):
        c.sendall(prefix)
        t0 = time.monotonic()
        while not stop.is_set() and time.monotonic() - t0 < 6:
            c.sendall(b'X')
            time.sleep(0.2)
    return script


class DownloadDeadlineTests(Tmp):
    def fetch_ms(self, url, **kw) -> float:
        t0 = time.monotonic()
        with self.assertRaises(StoreError) as e:
            download.fetch(url, allowlist=['localhost'], allow_nonpublic=True, **kw)
        self.assertEqual(e.exception.code, 'file_download_failed')
        return time.monotonic() - t0

    def slow(self, prefix: bytes) -> float:
        srv = SlowTLS(self.base, drip(prefix))
        self.addCleanup(srv.close)
        return self.fetch_ms(f'https://localhost:{srv.port}/f', timeout=1.0, context=srv.trust,
                             resolver=lambda h, p: ['127.0.0.1'])

    def test_slow_dns_is_bounded(self):
        def resolver(h, p):
            time.sleep(2)
            return ['127.0.0.1']
        self.assertLess(self.fetch_ms('https://localhost:9/f', timeout=0.5, resolver=resolver), 1.2)

    def test_slow_headers_are_bounded(self):
        self.assertLess(self.slow(b'HTTP/1.1 200 OK\r\n'), 2.5)

    def test_slow_body_chunks_are_bounded(self):
        self.assertLess(self.slow(b'HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n'), 2.5)


# ---- F20: extraction child: no preexec_fn, limits set in the child, minimal environment -------------
class ExtractIsolationTests(Tmp):
    def test_child_is_spawned_without_preexec_and_with_minimal_env(self):
        store = Store(self.base / 'data', 'synthetic')
        sha = store.put_attachment_bytes(request_id='SYNTHETIC-pdf', data=b'%PDF-1.4 SYNTHETIC', filename='s.pdf',
                                         mime='application/pdf', text='SYNTHETIC', occurred_at=AT)['object_sha256']
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps({'pages': ['x'], 'total_pages': 1}), stderr='')
        with patch.object(extract.subprocess, 'run', return_value=done) as run:
            extract.extract(store, sha)
        kw = run.call_args.kwargs
        self.assertNotIn('preexec_fn', kw)
        self.assertLessEqual(set(kw['env']), {'PATH', 'PYTHONPATH', 'HOME'})
        self.assertEqual(kw['env']['PYTHONPATH'], str(ROOT / 'src'))
        self.assertIn('timeout', kw)

    def test_child_entry_sets_limits_before_parsing(self):
        order = []
        with patch.object(extract, '_limits', side_effect=lambda: order.append('limits')), \
                patch('pypdf.PdfReader', side_effect=lambda p: order.append('parse') or (_ for _ in ()).throw(
                    RuntimeError('stop'))):
            with self.assertRaises(RuntimeError):
                extract._child('unused.pdf')
        self.assertEqual(order, ['limits', 'parse'])


# ---- F21/F22/R2-18: Codex auth comes from the keyring, never a file; owned temp dirs are removed -----
FAKE_CODEX = '''#!{py}
import json, os, sys, time
a = sys.argv
home, out, work = os.environ['CODEX_HOME'], a[a.index('-o') + 1], a[a.index('-C') + 1]
auth = os.path.join(home, 'auth.json')
secret = open(os.environ['FAKE_SECRET_FILE']).read()
copies = [os.path.join(d, f) for d, _, fs in os.walk(os.environ['FAKE_SCAN']) for f in fs
          if not os.path.islink(os.path.join(d, f)) and secret in open(os.path.join(d, f), errors='ignore').read()]
json.dump({{'home': home, 'out': out, 'work': work, 'auth_file': os.path.lexists(auth),
           'config': open(os.path.join(home, 'config.toml')).read(), 'copies': copies}}, open(os.environ['FAKE_LOG'], 'w'))
sys.stdin.read()
time.sleep(float(os.environ.get('FAKE_SLEEP', '0')))
open(out, 'w').write('{{"final": "SYNTHETIC"}}')
print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message'}}}}))
sys.exit(int(os.environ.get('FAKE_RC', '0')))
'''


class CodexHomeTests(Tmp):
    def setUp(self):
        super().setUp()
        self.scan = self.base / 'tmp'
        self.scan.mkdir()
        self.auth = self.base / 'auth.json'
        self.secret = 'SYNTHETIC-TOKEN-' + os.urandom(8).hex()
        self.auth.write_text(json.dumps({'tokens': self.secret}))
        (self.base / 'secret').write_text(self.secret)
        self.fake = self.base / 'codex'
        self.fake.write_text(FAKE_CODEX.format(py=sys.executable))
        self.fake.chmod(0o755)
        self.log = self.base / 'log.json'
        env = {'PHCTX_CODEX_AUTH': str(self.auth), 'FAKE_SECRET_FILE': str(self.base / 'secret'),
               'FAKE_SCAN': str(self.scan), 'FAKE_LOG': str(self.log)}
        for p in (patch.dict(os.environ, env), patch('tempfile.tempdir', str(self.scan)),
                  patch.object(model.shutil, 'which', return_value=str(self.fake))):
            p.start()
            self.addCleanup(p.stop)

    def run_codex(self, **kw):
        return model.run_codex('SYNTHETIC prompt', model_id='m', config_path=None,
                               output_schema={'type': 'object'}, **kw)

    def test_eval_file_login_is_a_symlink_never_a_copy(self):
        self.run_codex(file_auth=self.auth)
        seen = json.loads(self.log.read_text())
        self.assertTrue(seen['auth_file'])
        self.assertIn('cli_auth_credentials_store = "file"', seen['config'])
        self.assertEqual(seen['copies'], [])
        self.assertEqual(list(self.scan.iterdir()), [])

    def assert_clean(self):
        seen = json.loads(self.log.read_text())
        self.assertFalse(seen['auth_file'])  # no auth.json in any form: the keyring holds the login
        self.assertIn('cli_auth_credentials_store = "keyring"', seen['config'])
        self.assertEqual(seen['copies'], [])
        for k in ('home', 'work', 'out'):
            self.assertFalse(Path(seen[k]).exists(), k)
        self.assertEqual(list(self.scan.iterdir()), [])

    def test_success_uses_the_keyring_and_leaves_nothing(self):
        text, _ = self.run_codex()
        self.assertEqual(json.loads(text), {'final': 'SYNTHETIC'})
        self.assert_clean()

    def test_failure_leaves_nothing(self):
        with patch.dict(os.environ, {'FAKE_RC': '3'}), self.assertRaises(model.ModelError):
            self.run_codex()
        self.assert_clean()

    def test_timeout_leaves_nothing(self):
        with patch.dict(os.environ, {'FAKE_SLEEP': '5'}), self.assertRaises(model.ModelError) as e:
            self.run_codex(timeout=1)
        self.assertEqual(e.exception.code, 'model_timeout')
        self.assert_clean()

    def test_caller_cwd_gets_no_output_files(self):
        cwd = self.base / 'cwd'
        cwd.mkdir()
        self.run_codex(cwd=str(cwd))
        self.assertEqual(list(cwd.iterdir()), [])
        self.assertTrue(cwd.exists())


# ---- F28/F29: no fake budget, no unimplemented backend -----------------------------------------------
class ConfigTruthTests(Tmp):
    def load(self, body: str):
        p = self.base / 'config.toml'
        p.write_text(body)
        return config.load(p)

    def test_budget_field_is_gone_and_rejected(self):
        self.assertFalse(hasattr(Config(), 'daily_budget_usd'))
        with self.assertRaises(config.ConfigError) as e:
            self.load('[model]\ndaily_budget_usd = 2.0\n')
        self.assertIn('daily_budget_usd', str(e.exception))

    def test_unsupported_backend_is_rejected_at_load(self):
        for b in ('openai_api', 'scripted', 'gpt'):
            with self.subTest(backend=b), self.assertRaises(config.ConfigError) as e:
                self.load(f'[model]\nbackend = "{b}"\n')
            self.assertIn(b, str(e.exception))
        self.assertEqual(self.load('[model]\nbackend = "codex_cli"\n').model_backend, 'codex_cli')

    def test_unknown_backend_never_claims_reasoning(self):
        with self.assertRaises(model.ModelError) as e:
            model.investigate('openai_api', 'm', 'task', config_path='')
        self.assertEqual(e.exception.code, 'model_disabled')
        self.assertNotIn('openai_api', (ROOT / 'ops/install.sh').read_text())

    def test_cli_reports_config_error_cleanly(self):
        from phctx import cli
        p = self.base / 'config.toml'
        p.write_text('[model]\nbackend = "openai_api"\n')
        with patch('sys.stderr') as err:
            self.assertEqual(cli.main(['--config', str(p), 'status']), 2)
        self.assertIn('openai_api', ''.join(c.args[0] for c in err.write.call_args_list))


# ---- F42: snapshot durability ------------------------------------------------------------------------
class BackupDurabilityTests(Tmp):
    def setUp(self):
        super().setUp()
        self.cfg = Config(profile='synthetic', root=self.base / 'live', backup_dir=self.base / 'bk')
        s = Store(self.cfg.root, 'synthetic')
        s.put_attachment_bytes(request_id='SYNTHETIC-a', data=b'SYNTHETIC bytes', filename='a.txt',
                               mime='text/plain', text='SYNTHETIC', occurred_at=AT)

    def test_fsync_failure_prevents_success(self):
        with patch.object(backup.os, 'fsync', side_effect=OSError(5, 'injected EIO')):
            with self.assertRaises(OSError):
                backup.backup(self.cfg)
        self.assertEqual(list((self.base / 'bk').iterdir()), [])

    def test_failed_parent_fsync_after_publish_leaves_no_snapshot(self):
        real = backup._fsync

        def fail_parent(p):
            if Path(p) == self.base / 'bk':
                raise OSError(5, 'injected EIO')
            real(p)
        with patch.object(backup, '_fsync', side_effect=fail_parent):
            with self.assertRaises(OSError):
                backup.backup(self.cfg)
        self.assertEqual(list((self.base / 'bk').iterdir()), [])

    def test_every_file_and_directory_is_synced_manifest_last(self):
        synced = []
        with patch.object(backup, '_fsync', side_effect=lambda p: synced.append(Path(p))):
            out = backup.backup(self.cfg)
        snap = Path(out['snapshot']['path'])
        bk = snap.parent

        def rel(p):  # staging and final snapshot dir are the same snapshot
            return 'BASE' if p == bk else '/'.join(('SNAP',) + p.relative_to(bk).parts[1:])
        order = [rel(p) for p in synced]
        files = ['/'.join(('SNAP',) + p.relative_to(snap).parts) for p in snap.rglob('*') if p.is_file()]
        # -wal/-shm appear only when verify_snapshot later opens the WAL-mode copy read-only; not snapshot content.
        files = [f for f in files if not f.endswith(('backup_manifest.json', '-wal', '-shm'))]
        self.assertLessEqual(set(files) | {'SNAP', 'SNAP/objects', 'BASE'}, set(order))
        m = order.index('SNAP/.backup_manifest.json.tmp')  # manifest: temp, fsync, rename - after all data
        self.assertTrue(all(order.index(f) < m for f in files if not f.endswith('backup_manifest.json')))
        self.assertTrue((snap / 'backup_manifest.json').is_file())
        self.assertTrue(order.index('SNAP/objects') > max(order.index(f) for f in files if '/objects/' in f))
        self.assertTrue(m < order.index('SNAP') < order.index('BASE'))
        self.assertEqual([x.name for x in bk.iterdir()], [snap.name])

    def test_archive_directory_is_synced_after_rename(self):
        synced = []
        dest = self.base / 'cloud' / 'snapshot-x.phbk'
        dest.parent.mkdir()
        snap = self.base / 'snap'
        Store(self.cfg.root).backup(snap)
        with patch.object(backup, '_fsync', side_effect=lambda p: synced.append((Path(p), dest.exists()))):
            backup.encrypt_dir(snap, dest, 'SYNTHETIC-pass')
        self.assertIn((dest.parent, True), synced)


# ---- F43: pre-migration SQLite -> new root -----------------------------------------------------------
class PremigrationRestoreTests(Tmp):
    def setUp(self):
        super().setUp()
        self.cfg = Config(profile='synthetic', root=self.base / 'live')
        s = self.store = Store(self.cfg.root, 'synthetic')
        self.kept = s.put_attachment_bytes(request_id='SYNTHETIC-old', data=b'SYNTHETIC old original',
                                           filename='old.txt', mime='text/plain', text='SYNTHETIC old',
                                           occurred_at=AT)['object_sha256']
        (self.cfg.root / 'migrations').mkdir(exist_ok=True)
        self.pre = self.cfg.root / 'migrations' / 'pre-v9-1.sqlite3'
        with s.connect() as c:
            dst = sqlite3.connect(self.pre)
            c.backup(dst)
            dst.close()
        self.later = s.put_attachment_bytes(request_id='SYNTHETIC-new', data=b'SYNTHETIC later original',
                                            filename='new.txt', mime='text/plain', text='SYNTHETIC new',
                                            occurred_at=AT)['object_sha256']

    def test_builds_verified_new_root_with_only_referenced_objects(self):
        pre_sha = hashlib.sha256(self.pre.read_bytes()).hexdigest()
        out = backup.restore_premigration(self.cfg, self.pre, self.base / 'rolled')
        root = self.base / 'rolled'
        self.assertEqual(sorted(os.listdir(root / 'objects')), [self.kept])
        self.assertEqual(out['sqlite_sha256'], pre_sha)
        self.assertEqual(hashlib.sha256((root / 'context.sqlite3').read_bytes()).hexdigest(), pre_sha)
        self.assertEqual(out['originals_verified'], 1)
        self.assertEqual((root / 'PROFILE').read_text().strip(), 'synthetic')
        self.assertEqual(Store(root).read_object(self.kept), b'SYNTHETIC old original')

    def test_refuses_corrupt_live_object_and_existing_target(self):
        (self.cfg.root / 'objects' / self.kept).write_bytes(b'SYNTHETIC tampered')
        with self.assertRaises(StoreError) as e:
            backup.restore_premigration(self.cfg, self.pre, self.base / 'rolled')
        self.assertEqual(e.exception.code, 'object_corrupt')
        self.assertFalse((self.base / 'rolled').exists())
        (self.base / 'exists').mkdir()
        with self.assertRaises(StoreError):
            backup.restore_premigration(self.cfg, self.pre, self.base / 'exists')

    def test_refuses_a_snapshot_that_fails_integrity_or_foreign_keys(self):
        broken = self.base / 'broken.sqlite3'
        shutil.copyfile(self.pre, broken)
        with sqlite3.connect(broken) as c:  # an extraction row for an original that does not exist
            c.execute("INSERT INTO extractions(object_sha, status, updated_at) VALUES('SYNTHETIC-missing', 'pending', 't')")
        with sqlite3.connect(broken) as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with self.assertRaises(StoreError) as e:
            backup.restore_premigration(self.cfg, broken, self.base / 'fk')
        self.assertEqual(e.exception.code, 'backup_invalid')
        self.assertFalse((self.base / 'fk').exists())

    def test_cli_route(self):
        from phctx import cli
        cfgp = self.base / 'config.toml'
        cfgp.write_text(f'[app]\nprofile = "synthetic"\nroot = "{self.cfg.root}"\n')
        with patch('sys.stdout'):
            rc = cli.main(['--config', str(cfgp), 'restore-premigration', str(self.pre),
                           '--new-root', str(self.base / 'cli-root')])
        self.assertEqual(rc, 0)
        self.assertTrue((self.base / 'cli-root' / 'objects' / self.kept).is_file())

    def test_rollback_script_uses_the_route(self):
        text = (ROOT / 'ops' / 'rollback.sh').read_text()
        self.assertLess(text.index('restore-premigration "$PRE"'), text.index('ops/uninstall.sh"\n'))
        self.assertIn('restore-premigration', text)
        self.assertNotIn('ops/restore.sh and point', text)


# ---- F34/F35: eval oracle and per-case isolation -----------------------------------------------------
class HarnessTests(Tmp):
    def setUp(self):
        super().setUp()
        import harness
        self.h = harness
        self.empty = {'records': [], 'objects': [], 'insights': [], 'preferences': {}}

    def checks(self, scenario, jobs):
        run = {'mode': 'background', 'trace': [], 'final': '', 'summary': {'outcome': 'ran', 'jobs': jobs}}
        return {c['check']: c for c in self.h.auto_checks({'expected_capabilities': []}, {'scenario': scenario},
                                                         self.empty, self.empty, run)}

    def test_positive_case_fails_when_gate_rejects_a_surface(self):
        jobs = [{'outcome': 'surface', 'gate': {'queued': False, 'reason': 'gate_rejected:stale_evidence'}}]
        self.assertFalse(self.checks('question_new_matched_assessment', jobs)['surfaced']['ok'])

    def test_positive_case_accepts_queued_or_shadow_queued(self):
        for gate in ({'queued': True}, {'queued': False, 'shadow': True, 'would_queue': True}):
            jobs = [{'outcome': 'surface', 'gate': gate}]
            self.assertTrue(self.checks('old_analysis_contradicted', jobs)['surfaced']['ok'], gate)

    def test_negative_case_records_silence_vs_gate_block(self):
        c = self.checks('small_noisy_change', [{'outcome': 'surface', 'gate': {'queued': False, 'reason': 'x'}},
                                               {'outcome': 'silence', 'gate': {'queued': False}}])['no_new_surface']
        self.assertTrue(c['ok'])
        self.assertIn('model_silence=1', c['detail'])
        self.assertIn('gate_blocked=1', c['detail'])

    def test_model_failure_uses_per_case_backend_not_global_patch(self):
        store = Store(self.base / 'data', 'synthetic')
        real, seen = model.investigate, {}

        def fake_run_once(cfg, config_path=None, scripted=None):
            seen.update(global_intact=model.investigate is real, backend=cfg.model_backend)
            with self.assertRaises(model.ModelError) as e:
                scripted('task')
            seen['code'] = e.exception.code
            return {'outcome': 'ran', 'jobs': []}
        with patch.object(self.h.worker, 'run_once', side_effect=fake_run_once):
            self.h.background({}, store, {'scenario': 'api_budget_exhausted', 'model_failure': 'budget_exhausted'},
                              self.base / 'c.toml', 'm', self.base)
        self.assertEqual(seen, {'global_intact': True, 'backend': 'scripted', 'code': 'budget_exhausted'})


# ---- F37: campaign summary gates ---------------------------------------------------------------------
class SummarizeTests(Tmp):
    def setUp(self):
        super().setUp()
        self.cases = [json.loads(x) for x in (ROOT / 'evals/cases.jsonl').read_text().splitlines() if x.strip()]
        self.meta = {'model': 'm', 'judge_model': 'j', 'backend': 'b', 'hashes': PROMPTS}

    def write(self, drop=None, hold_hashes=None, trace_id='t'):
        for split in ('dev', 'holdout'):
            d = self.base / split
            d.mkdir(exist_ok=True)
            meta = dict(self.meta, hashes=hold_hashes or self.meta['hashes']) if split == 'holdout' else self.meta
            (d / 'run_meta.json').write_text(json.dumps(meta))
            rows = []
            for c in self.cases:
                if c['split'] != split:
                    continue
                for i in range(3 if c['severity'] == 'critical' else 1):
                    if (c['id'], i) == drop:
                        continue
                    rows.append({'case_id': c['id'], 'run': i + 1, 'status': 'PASS', 'hard_failure': False,
                                 'reason': 'SYNTHETIC ok', 'model_id': 'm', 'prompt_sha256': 'p' * 64, 'mode': 'foreground',
                                 'trace_id': trace_id, 'evidence_file': 'e.json'})
            (d / 'results_all_runs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def run_summary(self):
        out = self.base / 'report' / 'summary.json'
        out.parent.mkdir(exist_ok=True)
        p = subprocess.run([sys.executable, str(ROOT / 'evals/summarize.py'), str(self.base / 'dev'),
                            str(self.base / 'holdout'), str(out)], capture_output=True, text=True)
        return p.returncode, json.loads(out.read_text())

    def test_complete_campaign_passes(self):
        self.write()
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status']), (0, 'PASS'), s)

    def test_missing_critical_run_fails(self):
        crit = next(c['id'] for c in self.cases if c['severity'] == 'critical')
        self.write(drop=(crit, 2))
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status']), (1, 'FAIL'))
        self.assertIn(crit, s['insufficient_runs'])

    def test_runs_from_an_older_prompt_do_not_mix_in(self):
        self.write()
        crit = next(c for c in self.cases if c['severity'] == 'critical' and c['split'] == 'dev')
        f = self.base / 'dev' / 'results_all_runs.jsonl'
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        next(r for r in rows if r['case_id'] == crit['id'])['prompt_sha256'] = 'o' * 64
        f.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status']), (1, 'FAIL'))
        self.assertEqual(s['mixed_campaign_cases'], [crit['id']])

    def test_a_quota_outage_is_not_run_not_a_model_failure(self):
        self.write()
        f = self.base / 'holdout' / 'results_all_runs.jsonl'
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        for r in rows:  # the backend refused every holdout call (usage limit): the harness wrote NOT_RUN
            r.update(status='NOT_RUN', error='model_quota_exhausted', reason='judge_failed:model_call_failed')
        f.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status'], s['hard_failure_cases']), (2, 'NOT_RUN', []))

    def test_an_observed_violation_survives_a_later_model_failure(self):
        self.write()
        f = self.base / 'holdout' / 'results_all_runs.jsonl'
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        for r in rows:
            r.update(status='NOT_RUN', error='model_timeout')
        crit = next(c['id'] for c in self.cases if c['severity'] == 'critical' and c['split'] == 'holdout')
        bad = next(r for r in rows if r['case_id'] == crit)  # one of its 3 runs; the other two did not complete
        bad.update(status='FAIL', hard_failure=True)  # a hard automatic check failed before the timeout
        f.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status']), (1, 'FAIL'))
        self.assertIn(crit, s['hard_failure_cases'])
        case = next(r for r in s['failing_cases'] if r['case_id'] == crit)
        self.assertEqual(case['status'], 'FAIL')

    def test_harness_classification_precedence(self):
        from harness import classify
        bad = [{'check': 'no_write', 'ok': False, 'hard': True}]
        turn = [{'check': 'model_turn_completed', 'ok': False, 'hard': True}]
        err = {'verdict': 'ERROR', 'reason': 'judge_failed:model_call_failed'}
        cases = [(('model_timeout', bad, err), 'FAIL'), ((None, bad, err), 'FAIL'),
                 (('model_quota_exhausted', turn, err), 'NOT_RUN'), ((None, [], err), 'NOT_RUN'),
                 (('model_call_failed', [], {'verdict': 'PASS'}), 'NOT_RUN'), ((None, [], {'verdict': 'PASS'}), 'PASS'),
                 ((None, [], {'verdict': 'FAIL'}), 'FAIL')]
        for args, want in cases:
            self.assertEqual(classify(*args)[0], want, args)

    def test_hash_mismatch_fails(self):
        self.write(hold_hashes=dict(PROMPTS, src='b' * 64))
        self.assertEqual(self.run_summary()[0], 1)

    def test_scorer_failure_fails(self):
        self.write(trace_id='')
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status']), (1, 'FAIL'))
        self.assertNotEqual(s['package_score_py']['exit'], 0)


# ---- F38/F55: run manifests bind evidence to the tree; one version source ---------------------------
class ReleaseBindingTests(Tmp):
    def setUp(self):
        super().setUp()
        import build_release
        import run_manifest
        self.br, self.rm = build_release, run_manifest
        (self.base / 'test-report').mkdir()
        self.ev = self.base / 'test-report' / 'unit.log'
        cmd = [sys.executable, '-c', f'open({str(self.ev)!r}, "w").write("SYNTHETIC OK")']
        with patch('sys.stdout'):
            rc = run_manifest.main(['unit', '--evidence', 'test-report/unit.log', '--', *cmd], delivery=self.base)
        self.assertEqual(rc, 0)
        p = patch.object(build_release, 'D', self.base)
        p.start()
        self.addCleanup(p.stop)

    def test_manifest_records_tree_command_and_evidence(self):
        m = json.loads((self.base / 'run-manifests' / 'unit.json').read_text())
        self.assertEqual(m['hashes'], self.rm.tree_hashes())
        self.assertTrue({'src', 'contracts', 'prompts'} <= set(m['hashes']))
        self.assertEqual(m['evidence'], {'test-report/unit.log': hashlib.sha256(b'SYNTHETIC OK').hexdigest()})
        self.assertEqual(m['exit_code'], 0)
        self.assertEqual(m['command'][0], sys.executable)
        self.assertIn('timestamp', m)

    def test_bound_evidence_keeps_pass(self):
        self.assertEqual(self.br.check('PASS', 'test-report/unit.log', 'n')['status'], 'PASS')

    def test_changed_tree_refuses_pass(self):
        changed = dict(self.rm.tree_hashes(), src='0' * 64)
        with patch.object(self.br, 'tree_hashes', return_value=changed):
            c = self.br.check('PASS', 'test-report/unit.log', 'n')
        self.assertEqual(c['status'], 'NOT_RUN')

    def test_changed_or_unmanifested_evidence_refuses_pass(self):
        self.ev.write_text('SYNTHETIC edited')
        self.assertEqual(self.br.check('PASS', 'test-report/unit.log', 'n')['status'], 'NOT_RUN')
        (self.base / 'other.log').write_text('x')
        self.assertEqual(self.br.check('PASS', 'other.log', 'n')['status'], 'NOT_RUN')
        self.assertEqual(self.br.check('BLOCKED', None, 'n')['status'], 'BLOCKED')

    def test_stale_evidence_cannot_be_laundered(self):
        (self.base / 'test-report' / 'old.log').write_text('SYNTHETIC stale')
        with patch('sys.stdout'):
            self.rm.main(['launder', '--evidence', 'test-report/old.log', '--', sys.executable, '-c', 'pass'],
                         delivery=self.base)
        m = json.loads((self.base / 'run-manifests' / 'launder.json').read_text())
        self.assertIsNone(m['evidence']['test-report/old.log'])

    def test_ops_and_ios_sources_are_inputs_but_ios_evidence_is_not(self):
        h = self.rm.tree_hashes()
        self.assertTrue({'ops', 'ios'} <= set(h))
        self.assertTrue(any(p.name == 'test-report' for p in (ROOT / 'ios').iterdir()))
        self.assertTrue(self.rm._excluded(Path('ios/test-report/interop.log')))
        self.assertFalse(self.rm._excluded(Path('ios/test-report/typecheck.sh')))

    def test_package_archive_uses_runtime_version(self):
        self.assertNotRegex((ROOT / 'scripts' / 'package_release.py').read_text(), r'context-\d+\.\d+\.\d+-')

    def test_single_version_source(self):
        import phctx
        py = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        self.assertRegex(phctx.__version__, r'^\d+\.\d+\.\d+$')
        self.assertNotIn('version', py['project'])
        self.assertIn('version', py['project']['dynamic'])
        self.assertEqual(py['tool']['hatch']['version']['path'], 'src/phctx/__init__.py')
        self.assertEqual(self.br.__version__, phctx.__version__)


# ---- F40: verify.py checks the delivered layout -----------------------------------------------------
class VerifyLayoutTests(Tmp):
    def test_verify_targets_src_layout(self):
        import verify
        self.assertNotIn('reference_core', (ROOT / 'scripts' / 'verify.py').read_text())
        r = verify.fresh_instance(self.base)
        self.assertEqual(r['status'], 'PASS', r)
        self.assertEqual(r['module_path'], str(ROOT / 'src' / 'phctx'))


if __name__ == '__main__':
    unittest.main(verbosity=2)


class PairThrottleTests(Tmp):
    def test_wrong_guesses_throttle_only_the_guessing_peer_and_burn_no_codes(self):
        now = [0.0]
        t = ingest.PairThrottle(clock=lambda: now[0])
        s = Store(self.base / 'store', 'synthetic')
        code = ingest.new_pairing_code(s)
        for _ in range(t.FREE):
            with self.assertRaises(StoreError) as e:
                ingest.pair(s, 'WRONGWRG', 'SYNTHETIC-dev-1', 'x', peer='10.0.0.66', throttle=t)
            self.assertEqual(e.exception.code, 'pairing_invalid')
        with self.assertRaises(StoreError):
            ingest.pair(s, 'WRONGWRG', 'SYNTHETIC-dev-1', 'x', peer='10.0.0.66', throttle=t)
        with self.assertRaises(StoreError) as e:  # now waiting, even with the right code
            ingest.pair(s, code, 'SYNTHETIC-dev-1', 'x', peer='10.0.0.66', throttle=t)
        self.assertEqual(e.exception.code, 'pairing_throttled')
        # the phone on another address pairs with the outstanding code: nothing was burned
        out = ingest.pair(s, code, 'SYNTHETIC-dev-2', 'phone', peer='10.0.0.7', throttle=t)
        self.assertIn('device_token', out)

    def test_throttle_state_stays_bounded(self):
        now = [0.0]
        t = ingest.PairThrottle(clock=lambda: now[0])
        t.MAX_PEERS = 50
        for i in range(500):
            t.failed(f'10.0.{i // 256}.{i % 256}')
        self.assertLessEqual(len(t.state), 50)

    def test_throttle_expires(self):
        now = [0.0]
        t = ingest.PairThrottle(clock=lambda: now[0])
        for _ in range(t.FREE + 1):
            t.failed('p')
        with self.assertRaises(StoreError):
            t.check('p')
        now[0] = t.BASE_S + 1
        t.check('p')


class PackageAllowlistTests(unittest.TestCase):
    def test_archive_holds_exactly_the_tracked_files(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import package_release
        stray = ROOT / 'delivery' / 'SYNTHETIC-untracked-report.md'  # e.g. a real-data note left in the tree
        stray.write_text('SYNTHETIC resting heart rate 58 on 2026-09-01')
        try:
            names = {str(p.relative_to(ROOT)) for p in package_release.files()}
        finally:
            stray.unlink()
        self.assertNotIn('delivery/SYNTHETIC-untracked-report.md', names)
        tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.split()
        self.assertEqual(names, {t for t in tracked if (ROOT / t).is_file()})

    def test_archive_bytes_and_membership_equal_git_show_head(self):
        # git archive reads HEAD's committed tree directly, so this holds regardless of any uncommitted changes
        # in the working directory right now — it is not exercising main()'s separate dirty-tree refusal.
        import random
        import tarfile
        sys.path.insert(0, str(ROOT / 'scripts'))
        import package_release
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'archive.tar.gz'
            package_release.archive_head(dest)
            tracked = subprocess.run(['git', 'ls-tree', '-r', '--name-only', 'HEAD'], cwd=ROOT,
                                     capture_output=True, text=True).stdout.split()
            prefix = 'personal-health-context/'
            with tarfile.open(dest, 'r:gz') as tar:
                members = {m.name: m for m in tar.getmembers() if m.isfile()}
                self.assertEqual(set(members), {prefix + t for t in tracked})
                for rel in random.sample(tracked, min(20, len(tracked))):
                    want = subprocess.run(['git', 'show', f'HEAD:{rel}'], cwd=ROOT, capture_output=True).stdout
                    got = tar.extractfile(members[prefix + rel]).read()
                    self.assertEqual(got, want, rel)


class AllowlistTests(unittest.TestCase):
    def test_only_exact_hosts_are_allowed(self):
        from phctx.download import host_allowed
        hosts = ['oaisdmntprcentralus.blob.core.windows.net', 'oaisdmntprnorthcentralus.blob.core.windows.net']
        self.assertTrue(host_allowed('OAISDMNTPRNORTHCENTRALUS.blob.core.windows.net.', hosts))
        for bad in ('oaisdmntprattacker.blob.core.windows.net', 'evil.blob.core.windows.net',
                    'x.oaisdmntprcentralus.blob.core.windows.net', 'oaisdmntprcentralus.blob.core.windows.net.evil.com'):
            self.assertFalse(host_allowed(bad, hosts), bad)
        self.assertFalse(host_allowed('a.example.com', ['.example.com']))  # no suffix entries either
        self.assertFalse(host_allowed('oaisdmntprcentralus.blob.core.windows.net',
                                      [r're:oaisdmntpr[a-z0-9]{2,24}\.blob\.core\.windows\.net']))