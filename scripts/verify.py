#!/usr/bin/env python3
"""Offline verification of the delivered layout (src/phctx, tests). No network, real accounts or production root.

Static contract checks, the full unittest suite, and a fresh synthetic instance driven through the supported CLI in
an isolated interpreter (`python -I`, PYTHONPATH=src only), so an editable install cannot mask a path error.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
HASHED = ('src', 'tests', 'scripts', 'contracts', 'prompts', 'evals')


def fresh_instance(work: Path) -> dict:
    """`phctx init` + `phctx status` on a new synthetic root; confirms which phctx package actually ran."""
    root, cfg = work / 'root', work / 'config.toml'
    cfg.write_text(f'[app]\nprofile = "synthetic"\nroot = {json.dumps(str(root))}\n')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(work), 'PHCTX_CONFIG': str(cfg), 'PHCTX_CONFIG_DIR': str(work)}
    boot = f'import sys; sys.path.insert(0, {str(SRC)!r}); import runpy; sys.argv[0] = "phctx"; ' \
           'runpy.run_module("phctx", run_name="__main__")'
    where = subprocess.run([sys.executable, '-I', '-c', f'import sys; sys.path.insert(0, {str(SRC)!r}); '
                            'import phctx, os; print(os.path.dirname(phctx.__file__))'],
                           env=env, capture_output=True, text=True, timeout=30)
    runs = [subprocess.run([sys.executable, '-I', '-c', boot, *args], env=env, capture_output=True, text=True,
                           timeout=60) for args in (['init'], ['status'])]
    ok = where.returncode == 0 and all(r.returncode == 0 for r in runs) and (root / 'context.sqlite3').is_file()
    status = json.loads(runs[1].stdout) if runs[1].returncode == 0 else {}
    return {'name': 'fresh_instance_via_cli', 'status': 'PASS' if ok and status.get('profile') == 'synthetic'
            else 'FAIL', 'module_path': where.stdout.strip(), 'exit_codes': [r.returncode for r in runs],
            'stderr': ''.join(r.stderr for r in runs)[-1000:]}


def static_checks() -> list[dict]:
    checks = []
    try:
        for d in ('src', 'tests', 'scripts', 'evals'):
            for p in (ROOT / d).rglob('*.py'):
                ast.parse(p.read_text(), filename=str(p.relative_to(ROOT)))
        checks.append({'name': 'python_syntax', 'status': 'PASS'})
        for p in (ROOT / 'contracts').glob('*.json'):
            json.loads(p.read_text())
        tools = json.loads((ROOT / 'contracts/tools.json').read_text())['tools']
        assert len({x['name'] for x in tools}) == len(tools)
        ft = next(x for x in tools if x['name'] == 'context_capture_file')
        assert ft['_meta']['openai/fileParams'] == ['file']
        fs = ft['inputSchema']['properties']['file']
        assert set(fs['required']) == {'download_url', 'file_id'}
        assert not ft['annotations']['readOnlyHint']
        checks.append({'name': 'json_and_file_contract_structure', 'status': 'PASS', 'tools': len(tools)})
        cases = [json.loads(x) for x in (ROOT / 'evals/cases.jsonl').read_text().splitlines() if x.strip()]
        assert len({x['id'] for x in cases}) == len(cases)
        assert all(x['synthetic_only'] for x in cases)
        assert {x['split'] for x in cases} == {'dev', 'holdout'}
        checks.append({'name': 'evaluation_case_structure_only', 'status': 'PASS', 'cases': len(cases),
                       'model_executed': False})
    except (OSError, ValueError, KeyError, AssertionError, SyntaxError, StopIteration) as e:
        checks.append({'name': 'static_checks', 'status': 'FAIL', 'error': f'{type(e).__name__}: {e}'})
    return checks


def main() -> int:
    if sys.version_info < (3, 11):
        print('Python 3.11+ required.', file=sys.stderr)
        return 2
    sys.path.insert(0, str(SRC))
    checks = static_checks()
    with tempfile.TemporaryDirectory(prefix='phctx-verify-') as tmp:
        checks.append(fresh_instance(Path(tmp)))
    buffer = io.StringIO()
    start = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT / 'tests'))
    result = unittest.TextTestRunner(stream=buffer, verbosity=2).run(suite)
    duration = time.perf_counter() - start
    ev = ROOT / 'evidence'
    ev.mkdir(exist_ok=True)
    (ev / 'unittest.log').write_text(buffer.getvalue())
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for d in HASHED for p in (ROOT / d).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    ok = result.wasSuccessful() and all(x['status'] == 'PASS' for x in checks)
    report = {'status': 'OFFLINE_VERIFIED' if ok else 'OFFLINE_VERIFICATION_FAILED',
              'executed_at': datetime.now(timezone.utc).isoformat(),
              'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                              'sqlite': sqlite3.sqlite_version},
              'unit_tests': {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                             'skipped': len(result.skipped), 'seconds': round(duration, 1)},
              'checks': checks, 'real_accounts_used': False, 'model_calls_executed': 0, 'source_sha256': hashes}
    (ev / 'offline_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'source_sha256'}, ensure_ascii=False, indent=2))
    if not ok:
        print(buffer.getvalue()[-20000:])
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
