#!/usr/bin/env python3
"""Run a command and bind its evidence to the exact inputs: delivery/run-manifests/NAME.json.

    scripts/run_manifest.py NAME --evidence test-report/x.log [--evidence ...] -- CMD ARGS...

Records per-input tree hashes (src, contracts, prompts, evals, scripts, tests, ops, ios sources, pyproject.toml,
uv.lock), the command, exit code, UTC timestamp, and the SHA-256 of each evidence file (paths relative to delivery/).
Evidence files are removed before the command runs, so only a file the command itself wrote gets a hash.
build_release.py grants PASS only while these hashes equal the current tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ('src', 'contracts', 'prompts', 'evals', 'scripts', 'tests', 'ops', 'ios', 'pyproject.toml', 'uv.lock')
SKIP = {'__pycache__', '.DS_Store', '.build', 'test-report'}  # caches, OS litter, evidence (ios/test-report)


def tree_hash(path: Path) -> str:
    """sha256 over (relative path, file sha256) for every file, sorted; SKIP parts excluded."""
    files = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file()
                                                 and not SKIP & set(p.relative_to(ROOT).parts))
    h = hashlib.sha256()
    for p in files:
        h.update(f'{p.relative_to(ROOT)}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n'.encode())
    return h.hexdigest()


def tree_hashes() -> dict[str, str]:
    return {k: tree_hash(ROOT / k) for k in INPUTS if (ROOT / k).exists()}


def main(argv: list[str] | None = None, delivery: Path = ROOT / 'delivery') -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('name')
    p.add_argument('--evidence', action='append', default=[], help='file under delivery/ the command writes')
    argv = sys.argv[1:] if argv is None else argv
    cut = argv.index('--') if '--' in argv else len(argv)
    a, cmd = p.parse_args(argv[:cut]), argv[cut + 1:]
    if not cmd or not a.name.replace('-', '').replace('_', '').isalnum():
        p.error('need a simple NAME and a command after --')
    for rel in a.evidence:
        (delivery / rel).unlink(missing_ok=True)
    before = tree_hashes()
    started = datetime.now(timezone.utc).isoformat()
    rc = subprocess.call(cmd, cwd=ROOT)
    after = tree_hashes()
    evidence = {}
    for rel in a.evidence:
        f = delivery / rel
        evidence[rel] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
    manifest = {'name': a.name, 'command': cmd, 'exit_code': rc, 'timestamp': started,
                'finished_at': datetime.now(timezone.utc).isoformat(), 'hashes': before,
                'tree_changed_during_run': before != after, 'evidence': evidence}
    out = delivery / 'run-manifests' / f'{a.name}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps({k: manifest[k] for k in ('name', 'exit_code', 'tree_changed_during_run', 'evidence')}))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
