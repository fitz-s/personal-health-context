#!/usr/bin/env python3
"""Assemble delivery/release.json + capabilities.json from evidence files that already exist.

Every PASS points at a file under delivery/ with its SHA-256 AND a run manifest (delivery/run-manifests/*.json,
written by scripts/run_manifest.py) whose recorded tree hashes equal the current tree and whose evidence hash equals
the file now. Otherwise the check is downgraded to NOT_RUN. Statuses for checks that need the user's account/device
stay BLOCKED/NOT_RUN with the reason. Then run scripts/release_gate.py yourself.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / 'delivery'
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from phctx import __version__  # noqa: E402
from run_manifest import tree_hashes  # noqa: E402


def sha(rel: str) -> str:
    return hashlib.sha256((D / rel).read_bytes()).hexdigest()


def binding(evidence: str) -> str | None:
    """Name of a clean, successful run manifest that produced this exact evidence from the current tree."""
    cur, digest = tree_hashes(), sha(evidence)
    for m in sorted((D / 'run-manifests').glob('*.json')):
        r = json.loads(m.read_text())
        if (r.get('evidence', {}).get(evidence) == digest and r.get('hashes') == cur and r.get('exit_code') == 0
                and not r.get('tree_changed_during_run')):
            return m.name
    return None


def live(evidence: str, notes: str) -> dict:
    """A check observed by hand in the user's ChatGPT (no command can re-run it): PASS only while the evidence names
    the revision it was observed on and that revision's src tree equals the current one."""
    text = (D / evidence).read_text()
    ok = f'src-tree:{src_tree()[:16]}' in text
    return check('PASS_LIVE' if ok else 'NOT_RUN', evidence,
                 notes if ok else f'Live evidence was observed on an older src tree; re-probe. {notes}')


def src_tree() -> str:
    return hashlib.sha256(b''.join(hashlib.sha256(p.read_bytes()).digest()
                                   for p in sorted((ROOT / 'src/phctx').glob('*.py')))).hexdigest()


def check(status: str, evidence: str | None, notes: str) -> dict:
    if evidence is not None and not (D / evidence).is_file():
        raise SystemExit(f'missing evidence file: {evidence}')
    if status == 'PASS_LIVE':
        status, notes = 'PASS', f'{notes} [live observation, src-tree bound]'
    elif status == 'PASS':
        bound = binding(evidence) if evidence else None
        if not bound:
            status, notes = 'NOT_RUN', f'Evidence is not bound to the current tree by a run manifest. {notes}'
        else:
            notes = f'{notes} [run-manifests/{bound}]'
    return {'status': status, 'evidence': evidence, 'evidence_sha256': sha(evidence) if evidence else None,
            'notes': notes}


def load(rel: str) -> dict:
    return json.loads((D / rel).read_text())


LIVE = 'live-evidence/chatgpt_live_probe.md'


def main() -> int:
    eng = (D / 'test-report/p1_p5_engineering_tests.log').read_text()
    eng_ok = 'Exit code: 0' in eng and '\nOK\n' in eng
    drill = load('recovery-report/recovery_drill.json')
    steps = {r['step']: r['status'] for r in drill['results']}
    ev = load('eval-report/summary.json')
    code_rev = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip() \
        or 'uncommitted'
    tree = src_tree()

    core_file_ok = eng_ok and steps.get('disk_full_no_false_original') == 'PASS'
    checks = {
        'unit_regression': check('PASS' if eng_ok else 'FAIL', 'test-report/p1_p5_engineering_tests.log',
                                 'Full engineering suite (store, migrations, tools, SSRF-safe download, MCP stdio via '
                                 'official SDK client, Apple XML import, TLS ingest protocol, worker, backup).'),
        'auth_and_policy': check('PASS' if eng_ok else 'FAIL', 'test-report/p1_p5_engineering_tests.log',
                                 'Read-only tool profile enforced server-side; SQL authorizer; device tokens hashed; '
                                 'Oura durable registration/ingest refused; synthetic sources refused in production; '
                                 'eval security category in eval-report/summary.json.'),
        'durable_capture': check('PASS' if eng_ok and steps.get('sigkill_mid_write_consistent') == 'PASS' else 'FAIL',
                                 'recovery-report/recovery_drill.json',
                                 'Receipts only after COMMIT; SIGKILL mid-burst leaves records==receipts, integrity ok; '
                                 'same-key retry idempotent; new-process read.'),
        'original_file_integrity': check('PASS' if core_file_ok else 'FAIL', 'test-report/p1_p5_engineering_tests.log',
                                         'Real HTTPS download → SHA-256 verified original; failed/partial/oversize/SSRF '
                                         'refused with nothing persisted; disk-full leaves no object row.'),
        'local_restore': check('PASS' if steps.get('backup_during_writes_restore') == 'PASS' else 'FAIL',
                               'recovery-report/recovery_drill.json',
                               'Online snapshot during writes → verify → restore into new root → counts match; '
                               'production paths also restored (recovery-report/production_local_restore.log).'),
        'chatgpt_read_write': live(LIVE, 'ChatGPT web wrote a capture (committed receipt) and read it back.'),
        'chatgpt_files': live(LIVE, 'Photo and 2-page PDF saved from ChatGPT with local SHA-256 == stored SHA-256; '
                                    'pages readable.'),
        'fresh_conversation': live(LIVE, 'A new ChatGPT chat found the earlier capture by content.'),
        'apple_device_sync': check('BLOCKED', None, 'No Xcode/iOS SDK on this Mac; helper source + Swift core tests '
                                                    'delivered (ios/STATUS.md). XML backfill importer works.'),
        'oura_official_access': check('BLOCKED', None, 'No official Oura MCP route available to this account '
                                                       '(live-evidence/p0_account_probe_2026-09-23.md). Nothing persisted.'),
        'model_eval': check(ev['status'], 'eval-report/summary.json', ev['note']),
        'sparse_proactivity': check('PASS' if ev['buckets'].get('silence', {}).get('rate', 0) >= .9
                                    and ev['buckets'].get('revisit', {}).get('rate', 0) >= .8 else 'FAIL',
                                    'eval-report/summary.json',
                                    'Real worker + strong model on synthetic fixtures: silence and revisit categories.'),
        'background_shadow': check('NOT_RUN', None, 'Worker installed 2026-09-23 in shadow mode with the model disabled; '
                                                    'no multi-day observation has elapsed.'),
        'restart_recovery': check('PASS' if (D / 'recovery-report/launchd_restart_drill.log').is_file()
                                  and steps.get('stale_lease_recovers') == 'PASS' else 'FAIL',
                                  'recovery-report/launchd_restart_drill.log',
                                  'launchd uninstall/reinstall keeps data and runs; killed worker lease recovers; '
                                  'Mac sleep/wake not tested.'),
        'encrypted_backup_restore': check('PASS' if steps.get('encrypted_cloud_roundtrip') == 'PASS' else 'FAIL',
                                          'recovery-report/recovery_drill.json',
                                          'AES-256-GCM archive written to iCloud Drive, read back, decrypted, restored '
                                          'into a new root (synthetic data); tamper rejected. Production cloud backup '
                                          'is opt-in (USER_ACTIONS §5).'),
        'single_interface': live(LIVE, 'ChatGPT is the only daily interface: write, read, files, fresh chat, real-data '
                                       'investigation observed live. The iPhone helper is a one-time setup screen.'),
    }
    gaps = [f'{k}: {v["status"]} — {v["notes"]}' for k, v in checks.items() if v['status'] != 'PASS']
    core = ['unit_regression', 'auth_and_policy', 'durable_capture', 'original_file_integrity', 'local_restore']
    failed = [k for k, v in checks.items() if v['status'] == 'FAIL']
    claimed = 'BLOCKED' if any(checks[k]['status'] != 'PASS' for k in core) or failed else (
        'READY_WITH_GAPS' if gaps else 'READY')
    release = {'release_version': __version__, 'claimed_status': claimed, 'code_revision': f'{code_rev} src-tree:{tree[:16]}',
               'created_at': datetime.now(timezone.utc).isoformat(), 'checks': checks, 'gaps': gaps,
               'scope_changes_accepted_by_user': []}
    (D / 'release.json').write_text(json.dumps(release, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'claimed_status': claimed, 'gaps': len(gaps), 'failed': failed}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
