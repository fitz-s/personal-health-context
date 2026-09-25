#!/usr/bin/env python3
"""Assemble delivery/release.json + capabilities.json from evidence files that already exist.

Every PASS points at a file under delivery/ with its SHA-256 AND a run manifest (delivery/run-manifests/*.json,
written by scripts/run_manifest.py) whose recorded tree hashes equal the current tree and whose evidence hash equals
the file now; a manifest's own declared `inputs` must each still validate the same way, recursively — the consumer's
recorded input digest, the file's current digest, and the producer's own recorded evidence digest must all agree,
and the producer manifest's own bytes must be unchanged since consumption — or the whole binding is refused.
eval-report/summary.json additionally requires its bound manifest to declare results_all_runs.jsonl AND
run_meta.json as inputs for both campaign dirs it names (dev_round/holdout_round); an incomplete declared closure
downgrades it to NOT_RUN even though --input is otherwise optional per run. Live checks (chatgpt_*, single_interface)
instead read delivery/live-evidence/chatgpt_live_probe.json — written by scripts/record_live_probe.py — and PASS
only while its structural inputs and top-level artifacts match the current tree and every probe a check needs is
itself {"status": "PASS", "artifacts": [...]} with at least one of those artifacts still intact. Statuses for checks
that need the user's account/device stay BLOCKED/NOT_RUN with the reason. Then run scripts/release_gate.py yourself.
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


def _inputs_valid(record: dict, cur: dict, seen: frozenset[str]) -> bool:
    """Every entry in record['inputs'] is itself still a clean, current binding, recursively (cycle-safe: a
    manifest name already in `seen` is treated as invalid rather than re-descended into).

    Three digests must agree, not just the producer manifest's name: the input's sha256 as the CONSUMER recorded
    it at the time (meta['sha256']), the file's CURRENT sha256, and the PRODUCER's own recorded evidence sha256
    for that file. A rerun of the producer that overwrites both its result file and its manifest (same name, new
    bytes) makes the current file and the producer's fresh record agree with each other while disagreeing with
    what the consumer actually consumed — meta['sha256'] catches exactly that. The producer manifest's own bytes
    are pinned too (meta['manifest_sha256']): if the manifest file itself changed at all since consumption, the
    binding it named is no longer the one relied on, even if the other three digests still happen to line up."""
    for rel, meta in record.get('inputs', {}).items():
        mname = meta.get('manifest')
        if not mname or mname in seen:
            return False
        mpath = D / 'run-manifests' / mname
        if not mpath.is_file():
            return False
        try:
            raw = mpath.read_bytes()
            r = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return False
        if hashlib.sha256(raw).hexdigest() != meta.get('manifest_sha256'):
            return False
        f = D / rel
        if not f.is_file():
            return False
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if digest != meta.get('sha256') or digest != r.get('evidence', {}).get(rel):
            return False
        if r.get('hashes') != cur or r.get('exit_code') != 0 or r.get('tree_changed_during_run'):
            return False
        if not _inputs_valid(r, cur, seen | {mname}):
            return False
    return True


def binding(evidence: str) -> str | None:
    """Name of a clean, successful run manifest that produced this exact evidence from the current tree, whose
    declared inputs (if any) are themselves still validly bound, recursively."""
    cur, digest = tree_hashes(), sha(evidence)
    for m in sorted((D / 'run-manifests').glob('*.json')):
        r = json.loads(m.read_text())
        if (r.get('evidence', {}).get(evidence) == digest and r.get('hashes') == cur and r.get('exit_code') == 0
                and not r.get('tree_changed_during_run') and _inputs_valid(r, cur, frozenset({m.name}))):
            return m.name
    return None


LIVE_INPUT_KEYS = ('src', 'contracts', 'prompts', 'ops')
LIVE_PROBES = {
    'chatgpt_read_write': ('write', 'fresh_read'),
    'chatgpt_files': ('file_image', 'file_pdf'),
    'fresh_conversation': ('fresh_read',),
    'single_interface': ('write', 'fresh_read', 'file_image', 'file_pdf', 'real_data_investigation'),
}


def live(kind: str, notes: str) -> dict:
    """A check observed by hand in the user's ChatGPT (no command can re-run it): PASS only while
    delivery/live-evidence/chatgpt_live_probe.json binds its structural inputs (src, contracts, prompts, ops) to
    the current tree, every artifact it lists still hashes as recorded, and every probe `kind` needs is itself
    {"status": "PASS", "artifacts": [rel, ...]} with at least one of those artifacts intact — a status of PASS
    with no verifiable artifact records that the probe was merely asserted, not actually re-checkable."""
    probe = json.loads((D / LIVE).read_text())
    cur = tree_hashes()
    inputs = probe.get('inputs', {})
    reasons = [f'input {k} does not match the current tree' for k in LIVE_INPUT_KEYS if inputs.get(k) != cur.get(k)]
    artifacts = probe.get('artifacts', {})
    intact = set()
    for rel, digest in artifacts.items():
        f = D / rel
        if f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == digest:
            intact.add(rel)
        else:
            reasons.append(f'artifact {rel} missing or changed')
    probes = probe.get('probes', {})
    for name in LIVE_PROBES[kind]:
        entry = probes.get(name)
        entry = entry if isinstance(entry, dict) else {}
        if entry.get('status') != 'PASS':
            reasons.append(f'probe {name} not PASS')
        elif not any(rel in intact for rel in entry.get('artifacts', [])):
            reasons.append(f'probe {name} is PASS but has no verified artifact')
    ok = not reasons
    return check('PASS_LIVE' if ok else 'NOT_RUN', LIVE,
                 notes if ok else f'Live probe check failed: {"; ".join(reasons)}. {notes}')


def src_tree() -> str:
    return hashlib.sha256(b''.join(hashlib.sha256(p.read_bytes()).digest()
                                   for p in sorted((ROOT / 'src/phctx').glob('*.py')))).hexdigest()


SUMMARY_EVIDENCE = 'eval-report/summary.json'


def _summary_missing_required_inputs(bound_manifest: str) -> list[str]:
    """eval-report/summary.json is a known derived artifact with a fixed dependency shape: for each campaign dir
    it names (its own dev_round/holdout_round fields — basenames under eval-report/), the bound manifest must
    declare BOTH results_all_runs.jsonl and run_meta.json as inputs. --input is otherwise optional per run, so an
    omitted edge would silently validate; this closes that specific, known-shape gap rather than making --input
    generically mandatory. Returns the missing required relative paths, empty if the closure is complete."""
    record = json.loads((D / 'run-manifests' / bound_manifest).read_text())
    summary = load(SUMMARY_EVIDENCE)
    rounds = [summary.get('dev_round'), summary.get('holdout_round')]
    if not all(isinstance(d, str) and d and d == Path(d).name and d not in {'.', '..'} for d in rounds):
        return ['summary.json dev_round/holdout_round (both must name a campaign directory under eval-report/)']
    required = [f'eval-report/{d}/{name}' for d in rounds for name in ('results_all_runs.jsonl', 'run_meta.json')]
    have = set(record.get('inputs', {}))
    return [r for r in required if r not in have]


def check(status: str, evidence: str | None, notes: str) -> dict:
    if evidence is not None and not (D / evidence).is_file():
        raise SystemExit(f'missing evidence file: {evidence}')
    if status == 'PASS_LIVE':
        status, notes = 'PASS', f'{notes} [live observation, bound to delivery/{LIVE}]'
    elif status == 'PASS':
        bound = binding(evidence) if evidence else None
        if not bound:
            status, notes = 'NOT_RUN', f'Evidence is not bound to the current tree by a run manifest. {notes}'
        elif evidence == SUMMARY_EVIDENCE and (missing := _summary_missing_required_inputs(bound)):
            status, notes = 'NOT_RUN', f'Summary is missing required campaign inputs {missing}. {notes}'
        else:
            notes = f'{notes} [run-manifests/{bound}]'
    return {'status': status, 'evidence': evidence, 'evidence_sha256': sha(evidence) if evidence else None,
            'notes': notes}


def load(rel: str) -> dict:
    return json.loads((D / rel).read_text())


LIVE = 'live-evidence/chatgpt_live_probe.json'


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
        'chatgpt_read_write': live('chatgpt_read_write',
                                   'ChatGPT web wrote a capture (committed receipt) and read it back.'),
        'chatgpt_files': live('chatgpt_files', 'Photo and 2-page PDF saved from ChatGPT with local SHA-256 == '
                                               'stored SHA-256; pages readable.'),
        'fresh_conversation': live('fresh_conversation', 'A new ChatGPT chat found the earlier capture by content.'),
        'apple_device_sync': check('BLOCKED', None, 'Helper not built: needs Xcode licence (sudo), Apple ID signing and '
                                                    'the iPhone — deferred by the user. Two routes now exist — this '
                                                    'repo\'s own ios/HealthSyncHelper, or the open-source Life Dashboard '
                                                    'Companion webhook receiver (src/phctx/companion.py, '
                                                    'ios/COMPANION_SETUP.md) — neither is installed on-device. Swift '
                                                    'core tests, TLS interop, and companion signature/replay tests '
                                                    'pass; full export backfill is in production.'),
        'oura_official_access': check('BLOCKED', None, 'No official Oura MCP connector route available to this '
                                                       'account for ChatGPT (live-evidence/p0_account_probe_2026-09-23.md); '
                                                       'unchanged since that probe. Oura data is instead synced directly '
                                                       'as a durable source via Oura API v2 (src/phctx/oura.py, hourly '
                                                       'launchd job com.personalhealthcontext.oura; ~250k observations '
                                                       'in production per ops/status.sh) — this closes the data-access '
                                                       'gap but is not the official MCP route this check names, so it '
                                                       'stays BLOCKED.'),
        'model_eval': check(ev['status'], 'eval-report/summary.json', ev['note']),
        'sparse_proactivity': check('PASS' if ev['buckets'].get('silence', {}).get('rate', 0) >= .9
                                    and ev['buckets'].get('revisit', {}).get('rate', 0) >= .8 else 'FAIL',
                                    'eval-report/summary.json',
                                    'Real worker + strong model on synthetic fixtures: silence and revisit categories.'),
        'background_shadow': check('NOT_RUN', None, 'Background model disabled in production (user decision pending, '
                                                    'USER_ACTIONS §4); no multi-day shadow observation.'),
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
        'single_interface': live('single_interface',
                                 'ChatGPT is the only daily interface: write, read, files, fresh chat, real-data '
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
