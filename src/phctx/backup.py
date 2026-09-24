"""Consistent snapshot → optional AES-256-GCM encrypted archive (key from Keychain) → optional cloud folder.

The live root never syncs. A snapshot counts as recoverable only after its manifest verifies; an encrypted
archive counts only after decrypt + verify + restore into a new root succeeds.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .config import Config, keychain_get, keychain_set
from .store import Store, StoreError

MAGIC = b'PHCTXBK1'
CHUNK = 4 * 1024 * 1024
KEEP_DAILY, KEEP_WEEKLY = 7, 4


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def passphrase(service: str, create: bool = False) -> str:
    p = keychain_get(service)
    if p is None and create:
        p = secrets.token_urlsafe(32)
        keychain_set(service, p)
    if p is None:
        raise StoreError('backup_key_missing', f'No backup passphrase in Keychain service {service}.')
    return p


def encrypt_dir(src: Path, dest_file: Path, secret: str) -> dict:
    """tar to a temp file, then seal it in 4 MiB AES-GCM chunks (nonce = prefix||counter, last chunk tagged).

    Streams through disk so memory stays bounded for large archives.
    """
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(4)
    aes = AESGCM(_key(secret, salt))
    tmp = dest_file.with_suffix('.partial')
    digest = hashlib.sha256()
    with tempfile.TemporaryFile(dir=dest_file.parent) as plain:
        with tarfile.open(fileobj=plain, mode='w') as tar:
            tar.add(src, arcname='snapshot')
        size = plain.tell()
        plain.seek(0)
        with open(tmp, 'wb') as f:
            head = MAGIC + salt + prefix
            f.write(head)
            digest.update(head)
            n, done = 0, 0
            while done < size:
                chunk = plain.read(CHUNK)
                done += len(chunk)
                ct = aes.encrypt(prefix + n.to_bytes(8, 'big'), chunk, b'last' if done >= size else b'more')
                frame = len(ct).to_bytes(4, 'big') + ct
                f.write(frame)
                digest.update(frame)
                n += 1
            f.flush()
            os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest_file)
    return {'path': str(dest_file), 'bytes': dest_file.stat().st_size, 'chunks': n, 'sha256': digest.hexdigest()}


def decrypt_to(archive: Path, dest_dir: Path, secret: str) -> Path:
    with open(archive, 'rb') as f, tempfile.TemporaryFile() as plain:
        head = f.read(28)
        if not head.startswith(MAGIC):
            raise StoreError('backup_invalid', 'Not a phctx encrypted backup.')
        salt, prefix = head[8:24], head[24:28]
        aes = AESGCM(_key(secret, salt))
        total = archive.stat().st_size
        n, done = 0, False
        try:
            while f.tell() < total:
                size = int.from_bytes(f.read(4), 'big')
                ct = f.read(size)
                if len(ct) != size:
                    raise StoreError('backup_invalid', 'Archive is truncated.')
                last = f.tell() >= total
                plain.write(aes.decrypt(prefix + n.to_bytes(8, 'big'), ct, b'last' if last else b'more'))
                done = last
                n += 1
        except StoreError:
            raise
        except Exception as e:
            raise StoreError('backup_invalid', 'Decryption failed (wrong key or tampered archive).') from e
        if not done:
            raise StoreError('backup_invalid', 'Archive is truncated.')
        plain.seek(0)
        dest_dir.mkdir(parents=True, mode=0o700)
        with tarfile.open(fileobj=plain, mode='r') as tar:
            tar.extractall(dest_dir, filter='data')
    return dest_dir / 'snapshot'


def rotate(directory: Path) -> list[str]:
    """Keep the newest KEEP_DAILY snapshots plus one per ISO week for KEEP_WEEKLY weeks."""
    snaps = sorted([p for p in directory.glob('snapshot-*') if (p / 'backup_manifest.json').exists()
                    or p.suffix == '.phbk'], reverse=True)
    keep, weeks = set(snaps[:KEEP_DAILY]), {}
    for p in snaps:
        try:
            ts = datetime.strptime(p.name.split('-', 1)[1][:15], '%Y%m%dT%H%M%S')  # snapshot-YYYYmmddTHHMMSSZ
        except ValueError:
            keep.add(p)
            continue
        wk = ts.isocalendar()[:2]
        if wk not in weeks and len(weeks) < KEEP_WEEKLY:
            weeks[wk] = p
    keep |= set(weeks.values())
    removed = []
    for p in snaps:
        if p not in keep:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            removed.append(p.name)
    return removed


def backup(cfg: Config, dest: Path | None = None, encrypt: bool = False) -> dict:
    store = Store(cfg.root, cfg.profile)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    base = dest or cfg.backup_dir
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    snap = base / f'snapshot-{stamp}'
    result = {'snapshot': store.backup(snap)}
    result['verified'] = {k: v for k, v in Store.verify_snapshot(snap).items() if k != 'manifest'}
    if encrypt:
        if not cfg.cloud_backup_dir:
            raise StoreError('backup_config', 'Set [backup] cloud_dir to write the encrypted archive.')
        cfg.cloud_backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        secret = passphrase(cfg.backup_keychain_service, create=True)
        result['encrypted'] = encrypt_dir(snap, cfg.cloud_backup_dir / f'snapshot-{stamp}.phbk', secret)
        result['cloud_rotation_removed'] = rotate(cfg.cloud_backup_dir)
    result['local_rotation_removed'] = rotate(base) if dest is None else []
    return result


def restore(cfg: Config, snapshot: Path, new_root: Path, keychain_service: str | None = None) -> dict:
    """Restore a plain snapshot dir or an encrypted .phbk archive into a NEW root, then verify by reading."""
    work = None
    src = snapshot
    if snapshot.suffix == '.phbk':
        work = Path(tempfile.mkdtemp(prefix='phctx-restore-'))
        src = decrypt_to(snapshot, work / 'x', passphrase(keychain_service or cfg.backup_keychain_service))
    try:
        info = Store.verify_snapshot(src)
        restored = Store.restore(src, new_root)
        with restored.connect() as c:
            counts = {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                      for t in ('records', 'observations', 'objects')}
            shas = [r[0] for r in c.execute('SELECT sha256 FROM objects')]
        for sha in shas:
            restored.read_object(sha)
        if counts != info['counts']:
            raise StoreError('restore_mismatch', 'Restored counts differ from the snapshot.')
        return {'restored_root': str(restored.root), 'counts': counts, 'originals_verified': len(shas),
                'profile': restored.profile, 'from': str(snapshot)}
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)

