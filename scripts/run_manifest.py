#!/usr/bin/env python3
"""Run a command and bind its evidence to the exact inputs: delivery/run-manifests/NAME.json.

    scripts/run_manifest.py NAME --evidence test-report/x.log [--evidence ...] \\
        [--input eval-report/dev_x/results_all_runs.jsonl ...] -- CMD ARGS...

Records per-input tree hashes (src, contracts, prompts, evals, scripts, tests, ops, ios sources, pyproject.toml,
uv.lock), the command, exit code, UTC timestamp, and the SHA-256 of each evidence file (paths relative to delivery/).
Evidence files are removed before the command runs, so only a file the command itself wrote gets a hash.
build_release.py grants PASS only while these hashes equal the current tree.

--input REL (repeatable) declares a file this run reads that must itself already be validated evidence: it must
exist, and be the recorded `evidence` of some existing delivery/run-manifests/*.json whose `hashes` equal the
current tree_hashes(), whose recorded evidence hash equals the file's current SHA-256, whose exit_code is 0, and
which is not tree_changed_during_run. This stops a stale campaign (e.g. re-summarized under a changed tree) from
being laundered into a new manifest's evidence. Each validated input is recorded as
{rel: {sha256, manifest, manifest_sha256}} under the new manifest's `inputs` key — manifest_sha256 pins the exact
bytes of the producer manifest consumed, so a later rerun that overwrites both the input file and its manifest
under the same name cannot be mistaken for the one actually consumed here. Any --input that fails validation
refuses the whole command: exit 2, no manifest is written, nothing runs.

The command's own argv is refused (exit 2, no manifest, nothing runs) if any element resolves to an existing path
outside the repo root — this catches a wrapper invoking a script from a private temp directory that sits outside
the hashed source inputs. sys.executable, anything under ROOT/.venv, /bin/bash and /usr/bin/env are allowed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ('src', 'contracts', 'prompts', 'evals', 'scripts', 'tests', 'ops', 'ios', 'pyproject.toml', 'uv.lock')
SKIP = {'__pycache__', '.DS_Store', '.build'}  # caches and OS litter, excluded wherever they appear
EVIDENCE_SUFFIXES = {'.log', '.json'}  # generated evidence under a test-report dir; its drivers stay hashed
ALLOWED_ARGV_EXACT = {sys.executable, '/bin/bash', '/usr/bin/env'}


def _excluded(rel: Path) -> bool:
    parts = set(rel.parts)
    return bool(parts & SKIP) or ('test-report' in parts and rel.suffix in EVIDENCE_SUFFIXES)


def tree_hash(path: Path) -> str:
    """sha256 over (relative path, file sha256) for every file, sorted. __pycache__/.DS_Store/.build directories
    are excluded entirely; under a test-report directory only .log/.json files (generated evidence) are excluded —
    executable test drivers (e.g. ios/test-report/*.sh) are hashed like any other source file."""
    files = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file()
                                                 and not _excluded(p.relative_to(ROOT)))
    h = hashlib.sha256()
    for p in files:
        h.update(f'{p.relative_to(ROOT)}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n'.encode())
    return h.hexdigest()


def tree_hashes() -> dict[str, str]:
    return {k: tree_hash(ROOT / k) for k in INPUTS if (ROOT / k).exists()}


def _existing_path(a: str) -> Path | None:
    """Absolute form of A (relative elements join onto ROOT), lexically normalized but WITHOUT following
    symlinks, if it names something that exists; else None. Symlinks are deliberately left unresolved so a venv
    interpreter (.venv/bin/python often symlinks out to the system Python) still reads as "under ROOT/.venv"."""
    p = Path(a)
    cand = Path(os.path.normpath(p if p.is_absolute() else ROOT / p))
    try:
        return cand if cand.exists() else None
    except OSError:
        return None


def argv_path_outside_repo(cmd: list[str]) -> str | None:
    """First argv element that names an existing path outside ROOT, or None."""
    venv = Path(os.path.normpath(ROOT / '.venv'))
    for a in cmd:
        if a in ALLOWED_ARGV_EXACT:
            continue
        cand = _existing_path(a)
        if cand is None:
            continue
        if cand == venv or venv in cand.parents:
            continue
        try:
            cand.relative_to(ROOT)
        except ValueError:
            return a
    return None


def validate_input(delivery: Path, rel: str, cur_hashes: dict[str, str]) -> dict[str, str] | None:
    """{'sha256', 'manifest', 'manifest_sha256'} if REL is the valid, current evidence of an existing clean
    manifest; else None. manifest_sha256 pins the exact producer manifest bytes consumed, alongside the file's own
    digest, so a later rerun that overwrites both under the same name cannot be mistaken for the one consumed
    here (build_release._inputs_valid checks both, not just the manifest name)."""
    f = delivery / rel
    if not f.is_file():
        return None
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    mdir = delivery / 'run-manifests'
    if not mdir.is_dir():
        return None
    for m in sorted(mdir.glob('*.json')):
        try:
            raw = m.read_bytes()
            r = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            continue
        if (r.get('evidence', {}).get(rel) == digest and r.get('hashes') == cur_hashes
                and r.get('exit_code') == 0 and not r.get('tree_changed_during_run')):
            return {'sha256': digest, 'manifest': m.name, 'manifest_sha256': hashlib.sha256(raw).hexdigest()}
    return None


def main(argv: list[str] | None = None, delivery: Path = ROOT / 'delivery') -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('name')
    p.add_argument('--evidence', action='append', default=[], help='file under delivery/ the command writes')
    p.add_argument('--input', action='append', default=[],
                   help='file under delivery/ that must already be validated evidence of an existing manifest')
    argv = sys.argv[1:] if argv is None else argv
    cut = argv.index('--') if '--' in argv else len(argv)
    a, cmd = p.parse_args(argv[:cut]), argv[cut + 1:]
    if not cmd or not a.name.replace('-', '').replace('_', '').isalnum():
        p.error('need a simple NAME and a command after --')

    bad_argv = argv_path_outside_repo(cmd)
    if bad_argv is not None:
        print(json.dumps({'error': 'argv references a path outside the repo root', 'path': bad_argv}),
              file=sys.stderr)
        return 2

    cur = tree_hashes()
    inputs: dict[str, dict[str, str]] = {}
    for rel in a.input:
        valid = validate_input(delivery, rel, cur)
        if valid is None:
            print(json.dumps({'error': 'input is not the valid, current evidence of an existing manifest',
                               'input': rel}), file=sys.stderr)
            return 2
        inputs[rel] = valid

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
                'tree_changed_during_run': before != after, 'evidence': evidence, 'inputs': inputs}
    out = delivery / 'run-manifests' / f'{a.name}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps({k: manifest[k] for k in ('name', 'exit_code', 'tree_changed_during_run', 'evidence', 'inputs')}))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
