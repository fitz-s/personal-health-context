"""iPhone → Mac ingest (contracts/ingest_protocol.md). Dedicated device channel; no read/query rights.

TLS with a locally generated self-signed certificate that the phone pins by SHA-256; its private key lives in the
Keychain, never in a persistent file. Device tokens are stored hashed. Batches commit observations + stream sequence + receipt in one transaction.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import shutil
import socket
import ssl
import tempfile
import threading
import time
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
                                     s.get('value_text'), unit=s.get('unit'), source_name=s.get('source_name'))
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
KEY_SERVICE = 'phctx-ingest-tls'
_MEMORY: dict[str, str] = {}  # default keychain: process-local, so tests and scripts never reach the login Keychain


def ensure_cert(directory: Path, hosts: list[str], keychain=None) -> tuple[Path, bytes, str]:
    """Public cert on disk; the P-256 private scalar (64 hex chars) in `keychain` (production: config.KEYCHAIN).

    Returns (cert_path, private_key_pem_in_memory, cert_sha256). A pre-Keychain `ingest-key.pem` that matches the
    cert is moved into the keychain and deleted, so the phone's pin survives. A cert whose key is missing or does
    not match is replaced (the phone must re-pair).
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    kc = _MEMORY if keychain is None else keychain
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert_p, legacy = directory / 'ingest-cert.pem', directory / 'ingest-key.pem'
    pub = lambda k: k.public_key().public_bytes(serialization.Encoding.DER,  # noqa: E731
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    cert = x509.load_pem_x509_certificate(cert_p.read_bytes()) if cert_p.exists() else None
    key = None
    if cert and legacy.exists():
        old = serialization.load_pem_private_key(legacy.read_bytes(), None)
        if isinstance(old, ec.EllipticCurvePrivateKey) and pub(old) == pub(cert):
            kc[KEY_SERVICE] = format(old.private_numbers().private_value, '064x')
    stored = kc.get(KEY_SERVICE)
    if cert and stored:
        key = ec.derive_private_key(int(stored, 16), ec.SECP256R1())
        if pub(key) != pub(cert):
            key = None
    if key is None:
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
        kc[KEY_SERVICE] = format(key.private_numbers().private_value, '064x')  # before the cert: never orphan it
        cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    legacy.unlink(missing_ok=True)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return cert_p, pem, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def _load_key(ctx: ssl.SSLContext, cert: Path, key_pem: bytes) -> None:
    """Python's ssl cannot load a key from memory. Residual exposure: for the duration of load_cert_chain the key
    sits in a 0600 file inside a private 0700 mkdtemp dir, PKCS#8-encrypted under a random in-memory password;
    the dir is removed right after the load."""
    from cryptography.hazmat.primitives import serialization
    pw = secrets.token_bytes(32)
    enc = serialization.load_pem_private_key(key_pem, None).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(pw))
    d = tempfile.mkdtemp(prefix='phctx-tls-')
    try:
        path = os.path.join(d, 'k.pem')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(enc)
        ctx.load_cert_chain(cert, path, pw)
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
MAX_CONN = 16      # concurrent connections; excess ones are closed at accept
PER_PEER = 4       # per source address, so one stalling device cannot hold every slot
HANDSHAKE_S = 10   # TLS handshake bound
REQUEST_S = 60     # absolute bound from handshake completion to the full request body being read
IDLE_S = 20        # per-read socket timeout


LINGER_S = 1.0     # drain window after the reply, bounded


def _linger_close(sock: socket.socket) -> None:
    """Close without RST: an early reply (401/413 before the body is read) would otherwise be discarded by the
    client when close() with unread input sends RST. FIN first, then drain raw bytes for at most LINGER_S."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_WR)
        end = time.monotonic() + LINGER_S
        while (left := end - time.monotonic()) > 0:
            socket.socket.settimeout(sock, left)
            if not socket.socket.recv(sock, 65536):
                break
    except OSError:
        pass
    finally:
        sock.close()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, ctx: ssl.SSLContext):
        super().__init__(addr, handler)
        self.ctx, self.slots = ctx, threading.BoundedSemaphore(MAX_CONN)
        self.peers: collections.Counter[str] = collections.Counter()
        self.peer_lock = threading.Lock()

    def _take(self, peer: str) -> bool:
        with self.peer_lock:
            if self.peers[peer] >= PER_PEER or not self.slots.acquire(blocking=False):
                return False
            self.peers[peer] += 1
            return True

    def _give(self, peer: str) -> None:
        with self.peer_lock:
            self.peers[peer] -= 1
            if not self.peers[peer]:
                del self.peers[peer]
            self.slots.release()

    def process_request(self, request, client_address):
        if not self._take(client_address[0]):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._give(client_address[0])
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._give(client_address[0])

    def finish_request(self, request, client_address):
        request.settimeout(HANDSHAKE_S)
        try:
            tls = self.ctx.wrap_socket(request, server_side=True)
        except OSError:
            return
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            _linger_close(tls)

    def handle_error(self, request, client_address):
        log.debug('ingest_connection_error', exc_info=True)


def make_server(store: Store, host: str, port: int, cert: Path, key_pem: bytes) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = 'phctx-ingest/1'
        sys_version = ''
        timeout = IDLE_S

        def handle(self):
            # Absolute deadline: a client trickling bytes resets no idle timer; the socket is cut at REQUEST_S.
            self.timer = threading.Timer(REQUEST_S, self.cut)
            self.timer.daemon = True
            self.timer.start()
            try:
                super().handle()
            except OSError:
                pass
            finally:
                self.timer.cancel()

        def cut(self):
            try:
                socket.socket.shutdown(self.connection, socket.SHUT_RDWR)  # unblocks the reader; bypasses TLS state
            except OSError:
                pass

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
            data = self.rfile.read(n)
            if len(data) != n:
                raise OSError('request body incomplete before deadline')
            self.timer.cancel()
            try:
                return json.loads(data)
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
            except OSError:
                raise
            except Exception:  # never leak internals to the device
                log.exception('ingest_error')
                self.reply(503, {'error': 'storage_unavailable'})

        def do_GET(self):  # noqa: N802
            self.timer.cancel()
            try:
                if self.path == '/v1/status':
                    self.reply(200, stream_status(store, self.device()))
                else:
                    self.reply(404, {'error': 'not_found'})
            except ProtocolError as e:
                self.reply(e.status, e.body)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    _load_key(ctx, cert, key_pem)
    return _Server((host, port), Handler, ctx)  # TLS handshake runs per connection thread, under HANDSHAKE_S


def serve_background(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t

