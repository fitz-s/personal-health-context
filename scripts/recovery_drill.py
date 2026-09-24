#!/usr/bin/env python3
"""Real failure/recovery drill on an isolated SYNTHETIC root (never the production root).

Each step prints PASS/FAIL with the observation. Writes a JSON report. Steps that need things this machine
cannot do (phone lock screen, Mac sleep) are recorded NOT_RUN with the reason.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from phctx import backup as bk  # noqa: E402
from phctx.config import Config  # noqa: E402
from phctx.store import Store, StoreError  # noqa: E402

AT = '2026-09-23T09:00:00-05:00'
PY = str(ROOT / '.venv' / 'bin' / 'python')
results: list[dict] = []


def step(name: str, ok: bool | None, detail: str) -> None:
    status = 'NOT_RUN' if ok is None else ('PASS' if ok else 'FAIL')
    results.append({'step': name, 'status': status, 'detail': detail})
    print(f'[{status}] {name}: {detail}', flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--cloud-dir', type=Path, help='real cloud folder for the encrypted archive (e.g. iCloud Drive)')
    ap.add_argument('--keychain-service', default='phctx-backup-drill')
    a = ap.parse_args()
    base = Path(tempfile.mkdtemp(prefix='phctx-drill-'))
    live = base / 'live'
    s = Store(live, 'synthetic')
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}

    # 1. clean process restart keeps data
    r = s.put_record(request_id='d-1', kind='note', text='SYNTHETIC drill note', occurred_at=AT)
    out = subprocess.run([PY, '-c', f'from phctx.store import Store; s=Store({str(live)!r}); '
                          f'print(len(s.get_records([{r["record_id"]!r}])["records"]))'],
                         env=env, capture_output=True, text=True)
    step('restart_new_process_reads', out.stdout.strip() == '1', f'new process read count={out.stdout.strip()}')

    # 2. SIGKILL during a burst of writes: no partial rows, integrity ok, receipts == records written
    script = (f'from phctx.store import Store\ns=Store({str(live)!r})\n'
              'import itertools\nfor i in itertools.count():\n'
              '    s.put_record(request_id=f"burst-{i}", kind="note", text=f"SYNTHETIC burst {i}", '
              f'occurred_at={AT!r})\n')
    p = subprocess.Popen([PY, '-c', script], env=env)
    time.sleep(1.5)
    p.send_signal(signal.SIGKILL)
    p.wait()
    with sqlite3.connect(live / 'context.sqlite3') as c:
        integ = c.execute('PRAGMA integrity_check').fetchone()[0]
        n_rec = c.execute("SELECT count(*) FROM records WHERE text LIKE 'SYNTHETIC burst %'").fetchone()[0]
        n_rcp = c.execute("SELECT count(*) FROM receipts WHERE request_id LIKE 'burst-%'").fetchone()[0]
    step('sigkill_mid_write_consistent', integ == 'ok' and n_rec == n_rcp and n_rec > 0,
         f'integrity={integ} records={n_rec} receipts={n_rcp}')
    # retry of the in-flight key after crash: same key replays or commits once
    again = s.put_record(request_id=f'burst-{n_rec}', kind='note', text=f'SYNTHETIC burst {n_rec}', occurred_at=AT)
    again2 = s.put_record(request_id=f'burst-{n_rec}', kind='note', text=f'SYNTHETIC burst {n_rec}', occurred_at=AT)
    step('retry_after_crash_idempotent', again == again2, 'same receipt on retry')

    # 3. DB busy: a long writer holds the lock; a second writer gets a bounded, retryable error or waits
    holder = sqlite3.connect(live / 'context.sqlite3', timeout=1, isolation_level=None)
    holder.execute('BEGIN IMMEDIATE')
    t0 = time.time()
    code = None
    import phctx.store as st
    orig = st.Store.connect

    def short(self):  # shorten busy timeout for the drill
        from contextlib import contextmanager

        @contextmanager
        def cm():
            c = sqlite3.connect(self.db, timeout=0.5, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA busy_timeout=500')
            try:
                yield c
            finally:
                c.close()
        return cm()
    st.Store.connect = short
    try:
        s.put_record(request_id='busy-1', kind='note', text='SYNTHETIC busy', occurred_at=AT)
    except StoreError as e:
        code = e.code
    finally:
        st.Store.connect = orig
        holder.rollback()
        holder.close()
    ok_after = s.put_record(request_id='busy-1', kind='note', text='SYNTHETIC busy', occurred_at=AT)['status']
    step('db_busy_bounded_then_retry', code == 'storage_busy' and ok_after == 'committed',
         f'error={code} after {time.time() - t0:.1f}s; retry={ok_after}')

    # 4. disk full on original write: nothing referenced, clear error, live data intact
    import phctx.store as stmod
    real_replace = os.replace

    def full(*args, **kw):
        raise OSError(28, 'No space left on device')
    stmod.os.replace = full
    try:
        s.put_attachment_bytes(request_id='full-1', data=b'SYNTHETIC bytes that cannot be written',
                               filename='x.txt', mime='text/plain', text='SYNTHETIC', occurred_at=AT)
        code = None
    except StoreError as e:
        code = e.code
    finally:
        stmod.os.replace = real_replace
    with s.connect() as c:
        objs = c.execute('SELECT count(*) FROM objects').fetchone()[0]
    step('disk_full_no_false_original', code == 'storage_unavailable' and objs == 0, f'error={code} objects={objs}')

    # 5. read-only filesystem for the DB → clean failure, no partial state
    # WAL mode writes to -wal/-shm, so all three files must be read-only to simulate a read-only volume.
    ro = [p for p in live.glob('context.sqlite3*')]
    for p in ro:
        os.chmod(p, 0o400)
    try:
        try:
            s.put_record(request_id='ro-1', kind='note', text='SYNTHETIC ro', occurred_at=AT)
            code = None
        except (StoreError, sqlite3.OperationalError) as e:
            code = getattr(e, 'code', type(e).__name__)
    finally:
        for p in ro:
            os.chmod(p, 0o600)
    step('readonly_db_refuses_write', code is not None and s.receipt('ro-1') is None, f'error={code}')

    # 6. backup while writes continue → snapshot verifies; restore into new root; counts match snapshot
    s.put_attachment_bytes(request_id='att-1', data=b'SYNTHETIC original bytes \x00\x01', filename='o.bin',
                           mime='application/octet-stream', text='SYNTHETIC original', occurred_at=AT)
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            s.put_record(request_id=f'bg-{i}', kind='note', text=f'SYNTHETIC bg {i}', occurred_at=AT)
            i += 1
    th = threading.Thread(target=writer)
    th.start()
    time.sleep(0.3)
    snap = base / 'snap1'
    s.backup(snap)
    stop.set()
    th.join()
    info = Store.verify_snapshot(snap)
    restored = Store.restore(snap, base / 'restored1')
    with restored.connect() as c:
        cnt = c.execute('SELECT count(*) FROM records').fetchone()[0]
    step('backup_during_writes_restore', cnt == info['counts']['records'],
         f'snapshot records={info["counts"]["records"]} restored={cnt} (writes continued during backup)')

    # 7. encrypted archive → real cloud folder → read back → decrypt → restore → verify originals
    if a.cloud_dir:
        a.cloud_dir.mkdir(parents=True, exist_ok=True)
        secret = bk.passphrase(a.keychain_service, create=True)
        arc = a.cloud_dir / f'drill-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.phbk'
        meta = bk.encrypt_dir(snap, arc, secret)
        local_copy = base / 'downloaded.phbk'
        local_copy.write_bytes(arc.read_bytes())  # read back through the cloud folder
        same = hashlib.sha256(local_copy.read_bytes()).hexdigest() == meta['sha256']
        cfg = Config(profile='synthetic', root=live, backup_keychain_service=a.keychain_service)
        rr = bk.restore(cfg, local_copy, base / 'restored_enc', a.keychain_service)
        plain_leak = b'SYNTHETIC original bytes' in arc.read_bytes()
        step('encrypted_cloud_roundtrip', same and rr['originals_verified'] >= 1 and not plain_leak,
             f'archive={arc} bytes={meta["bytes"]} readback_hash_match={same} restored={rr["counts"]} '
             f'originals_verified={rr["originals_verified"]} plaintext_visible_in_archive={plain_leak}')
        bad = base / 'tampered.phbk'
        raw = bytearray(local_copy.read_bytes())
        raw[len(raw) // 2] ^= 1
        bad.write_bytes(bytes(raw))
        try:
            bk.restore(cfg, bad, base / 'restored_bad', a.keychain_service)
            tam = 'accepted'
        except StoreError as e:
            tam = e.code
        step('tampered_archive_rejected', tam == 'backup_invalid', f'result={tam}')
        arc.unlink()  # drill artifact only; production archives are managed by rotation
    else:
        step('encrypted_cloud_roundtrip', None, 'no --cloud-dir given')

    # 8. model unavailable → worker defers, never surfaces or marks done (scripted failing backend)
    from phctx import model, worker
    q = s.put_record(request_id='q-1', kind='question', text='SYNTHETIC: is posture improving?', occurred_at=AT,
                     payload={'state': 'open', 'watch_terms': ['posture']})
    s.put_record(request_id='n-1', kind='note', text='SYNTHETIC posture reassessment', occurred_at=AT)
    cfg = Config(profile='synthetic', root=live, model_enabled=True, model_backend='scripted', model_id='x',
                 minimum_semantic_interval_seconds=0)

    def boom(task):
        raise model.ModelError('model_call_failed')
    res = worker.run_once(cfg, scripted=boom)
    jobs = res.get('jobs', [])
    with s.connect() as c:
        ins = c.execute('SELECT count(*) FROM insights').fetchone()[0]
        st_ = [dict(r) for r in c.execute("SELECT state, attempts, last_error_code FROM jobs WHERE type='revisit'")]
    step('model_unavailable_defers', ins == 0 and st_ and all(j['state'] == 'queued' for j in st_),
         f'jobs={jobs} insights={ins} job_states={st_}; question={q["record_id"][:12]}')

    # 9. worker killed while holding its lease → another worker proceeds after lease expiry
    worker.acquire(s, 'worker', 'dead-owner', seconds=1)
    blocked = worker.run_once(cfg, scripted=boom).get('outcome')
    time.sleep(1.2)
    later = worker.run_once(cfg, scripted=boom).get('outcome')
    step('stale_lease_recovers', blocked == 'lease_held_by_other_worker' and later in {'idle', 'ran'},
         f'while held={blocked}; after expiry={later}')

    step('mac_sleep_resume', None, 'Not simulated: sleeping the Mac would end this session. launchd StartInterval '
         'jobs run after wake; verify with `ops/status.sh` next day (recent_runs timestamps).')
    step('phone_lock_screen', None, 'Requires the signed iPhone helper on a device (Xcode not installed here).')

    report = {'executed_at': datetime.now(timezone.utc).isoformat(), 'root': str(base), 'profile': 'synthetic',
              'results': results,
              'summary': {k: sum(1 for r in results if r['status'] == k) for k in ('PASS', 'FAIL', 'NOT_RUN')}}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=1))
    print(json.dumps(report['summary']))
    return 1 if report['summary']['FAIL'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
