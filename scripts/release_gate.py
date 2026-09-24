#!/usr/bin/env python3
"""Validate release evidence paths/hashes and prohibit false READY. Does not certify test truth."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

CHECKS = ['unit_regression','auth_and_policy','durable_capture','original_file_integrity','local_restore',
          'chatgpt_read_write','chatgpt_files','fresh_conversation','apple_device_sync','oura_official_access',
          'model_eval','sparse_proactivity','background_shadow','restart_recovery','encrypted_backup_restore','single_interface']
CORE = set(CHECKS[:5])


def assess(report: dict, evidence_root: Path) -> dict:
    root = evidence_root.resolve()
    errors, gaps = [], []
    checks = report.get('checks', {})
    if set(checks) != set(CHECKS):
        errors.append('Check set must match the fixed delivery contract exactly.')
    for name in CHECKS:
        c = checks.get(name, {})
        status = c.get('status')
        if status not in {'PASS','FAIL','NOT_RUN','BLOCKED'}:
            errors.append(f'{name}: invalid or missing status')
            continue
        if status != 'PASS':
            gaps.append(f'{name}: {status}')
            if name in CORE or status == 'FAIL':
                errors.append(f'{name}: unresolved core/failed test')
            if not c.get('notes'):
                errors.append(f'{name}: missing gap explanation')
            continue
        rel = c.get('evidence')
        if not isinstance(rel, str) or not rel:
            errors.append(f'{name}: PASS lacks evidence path')
            continue
        p = root / rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts or p.is_symlink() or root not in p.resolve().parents or not p.is_file():
            errors.append(f'{name}: invalid evidence path')
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if c.get('evidence_sha256') != sha:
            errors.append(f'{name}: evidence hash mismatch')
    derived = 'BLOCKED' if errors else ('READY_WITH_GAPS' if gaps else 'READY')
    claimed = report.get('claimed_status')
    if claimed != derived:
        errors.append(f'Claimed {claimed!r} does not match derived {derived}.')
    if gaps and not report.get('gaps'):
        errors.append('Unresolved checks require an explicit user-facing gaps list.')
    return {'derived_status': derived, 'claimed_status': claimed, 'valid': not errors,
            'gaps': gaps, 'errors': errors, 'test_truth_certified': False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('report', type=Path)
    p.add_argument('--evidence-root', type=Path, required=True)
    args = p.parse_args()
    try:
        result = assess(json.loads(args.report.read_text()), args.evidence_root)
    except (OSError, ValueError, TypeError) as e:
        result = {'derived_status':'BLOCKED','valid':False,'error':type(e).__name__}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['valid'] and result['derived_status'] != 'BLOCKED' else 1

if __name__ == '__main__':
    raise SystemExit(main())
