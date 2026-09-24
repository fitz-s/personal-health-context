#!/usr/bin/env python3
"""Scan the repo for secrets / live data, then build the code delivery archive + manifest.sha256.

Refuses to package when a secret pattern or a live-data file is found. Production data never lives in the repo.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {'.venv', '.git', '__pycache__', 'traces', 'package_ref', '.build', 'dist'}
EXCLUDE_FILES = {'tunnel-client', 'cloudflared'}  # large third-party binaries: fetched + checksum-verified by docs
SECRET = [re.compile(p) for p in [
    r'sk-[A-Za-z0-9_-]{20,}', r'sk-proj-[A-Za-z0-9_-]{20,}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'ghp_[A-Za-z0-9]{30,}', r'xox[bap]-[A-Za-z0-9-]{10,}', r'AKIA[0-9A-Z]{16}',
    r'"(?:access_token|refresh_token|id_token)"\s*:\s*"[^"]{20,}"', r'Bearer\s+[A-Za-z0-9._-]{30,}']]
LIVE_DATA = re.compile(r'(context\.sqlite3(-wal|-shm)?|\.phbk|export\.zip|ingest-key\.pem)$')


def files() -> list[Path]:
    out = []
    for p in ROOT.rglob('*'):
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts) or not p.is_file():
            continue
        if rel.parts[0] == 'tools' and (p.name in EXCLUDE_FILES or p.suffix == '.zip'):
            continue
        out.append(p)
    return sorted(out)


def main() -> int:
    findings = []
    fs = files()
    for p in fs:
        rel = str(p.relative_to(ROOT))
        if LIVE_DATA.search(p.name):
            findings.append({'file': rel, 'issue': 'live-data file type'})
            continue
        try:
            text = p.read_text(errors='ignore')
        except OSError:
            continue
        for pat in SECRET:
            m = pat.search(text)
            if m:
                findings.append({'file': rel, 'issue': 'secret pattern', 'pattern': pat.pattern[:30]})
    # Evidence/eval files must be synthetic: every eval trace dir is excluded; summaries are checked for markers.
    for p in (ROOT / 'delivery').rglob('*.json*'):
        if 'traces' in p.parts:
            continue
        t = p.read_text(errors='ignore')
        if re.search(r'SYNTHETIC-OURA-FIXTURE', t) and 'eval-report' not in str(p):
            findings.append({'file': str(p.relative_to(ROOT)), 'issue': 'vendor fixture marker outside eval'})
    report = {'scanned_files': len(fs), 'findings': findings, 'at': datetime.now(timezone.utc).isoformat()}
    (ROOT / 'delivery' / 'package_scan.json').write_text(json.dumps(report, indent=1))
    if findings:
        print(json.dumps(report, indent=1))
        return 1
    fs = files()  # include the scan report
    manifest = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n' for p in fs
                       if p.name != 'manifest.sha256')
    (ROOT / 'delivery' / 'manifest.sha256').write_text(manifest)
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    arc = dist / f'personal-health-context-0.3.0-{rev or "worktree"}.tar.gz'
    with tarfile.open(arc, 'w:gz') as tar:
        for p in files():
            tar.add(p, arcname=f'personal-health-context/{p.relative_to(ROOT)}')
    print(json.dumps({'archive': str(arc), 'bytes': arc.stat().st_size,
                      'sha256': hashlib.sha256(arc.read_bytes()).hexdigest(), 'files': len(files()),
                      'findings': 0}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
