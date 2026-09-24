"""Consistent snapshot → optional AES-256-GCM encrypted archive (key from Keychain) → optional cloud folder.

The live root never syncs. A snapshot counts as recoverable only after its manifest verifies; an encrypted
archive counts only after decrypt + verify + restore into a new root succeeds.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
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


def _fsync(path: Path | str) -> None:
    """fsync a file or directory (directory entries become durable only once the directory is synced)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _seal(stage: Path, final: Path) -> None:
    """Make a fully written snapshot dir durable, then publish it by rename. Manifest goes last via temp+fsync+rename;
    until the directory rename lands, no `snapshot-*` name exists, so a crash leaves nothing that looks complete."""
    m, tmp = stage / 'backup_manifest.json', stage / '.backup_manifest.json.tmp'
    if m.exists():
        os.replace(m, tmp)
    for f in sorted(p for p in stage.rglob('*') if p.is_file() and p != tmp):
        _fsync(f)
    for d in sorted((p for p in stage.rglob('*') if p.is_dir()), reverse=True):
        _fsync(d)
    if tmp.exists():
        _fsync(tmp)
        os.replace(tmp, m)
    _fsync(stage)
    os.rename(stage, final)
    try:
        _fsync(final.parent)
    except OSError:  # the name is not durable: never leave a complete-looking directory behind a raised call
        shutil.rmtree(final, ignore_errors=True)
        raise


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
    _fsync(dest_file.parent)
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
            ts = datetime.strptime(p.name.split('-', 1)[1][:15], '%Y%m%dT%H%M%S')  # snapshot-YYYYmmddTHHMMSS[ffffff]Z
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
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')  # microseconds: two runs in one second never collide
    base = dest or cfg.backup_dir
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    snap, stage = base / f'snapshot-{stamp}', base / f'.snapshot-{stamp}.partial'
    try:
        made = store.backup(stage)
        _seal(stage, snap)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    result = {'snapshot': {**made, 'path': str(snap)}}
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



def _digest(p: Path) -> str:
    """Streamed SHA-256: a multi-GB database never sits in memory."""
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def restore_premigration(cfg: Config, sqlite: Path, new_root: Path) -> dict:
    """Build a NEW root from a pre-migration SQLite file (root/migrations/pre-v*.sqlite3) plus the live objects
    it references, each hash-verified. Verified WITHOUT opening it as a Store, which would migrate it forward again:
    the point is a root the rolled-back program version can open."""
    live = cfg.root.expanduser().resolve()
    src, dest = Path(sqlite).expanduser().resolve(), Path(new_root).expanduser().resolve()
    if not src.is_file():
        raise StoreError('invalid_restore_path', 'Pre-migration SQLite file not found.')
    if dest.exists() or dest == live or live in dest.parents:
        raise StoreError('invalid_restore_path', 'Restore into a new directory outside the live root.')
    stage = dest.parent / f'.{dest.name}.partial'
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage.mkdir(mode=0o700)
    try:
        db = stage / 'context.sqlite3'
        want = _digest(src)
        shutil.copyfile(src, db)
        os.chmod(db, 0o600)
        if _digest(db) != want:
            raise StoreError('restore_mismatch', 'SQLite copy changed in transit.')
        c = sqlite3.connect(db)
        try:
            if (c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                    or c.execute('PRAGMA foreign_key_check').fetchone() is not None):
                raise StoreError('backup_invalid', 'Pre-migration SQLite failed its integrity check.')
            schema = int(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
            objects = c.execute('SELECT sha256, size FROM objects').fetchall()
            counts = {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                      for t in ('records', 'observations', 'objects')}
        except sqlite3.DatabaseError as e:
            raise StoreError('backup_invalid', 'Not a phctx SQLite database.') from e
        finally:
            c.close()
        (stage / 'objects').mkdir(mode=0o700)
        for sha, size in objects:
            try:
                data = (live / 'objects' / sha).read_bytes()
            except OSError as e:
                raise StoreError('object_missing', f'Referenced original {sha[:12]} is missing from the live root.') from e
            if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                raise StoreError('object_corrupt', f'Referenced original {sha[:12]} failed verification.')
            fd = os.open(stage / 'objects' / sha, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
        marker = live / 'PROFILE'
        profile = marker.read_text().strip() if marker.exists() else cfg.profile
        (stage / 'PROFILE').write_text(profile + '\n')
        os.chmod(stage / 'PROFILE', 0o600)
        _seal(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    for sha, size in objects:  # re-read from the published root
        data = (dest / 'objects' / sha).read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
            raise StoreError('object_corrupt', 'Restored original failed verification.')
    return {'restored_root': str(dest), 'schema_version': schema, 'counts': counts,
            'originals_verified': len(objects), 'profile': profile, 'from': str(src), 'sqlite_sha256': want}
