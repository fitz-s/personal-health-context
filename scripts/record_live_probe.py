#!/usr/bin/env python3
"""Write delivery/live-evidence/chatgpt_live_probe.json: the structured evidence build_release.live() checks
against probes observed by hand in the user's ChatGPT (no command can re-run these).

    scripts/record_live_probe.py --probe write=PASS --probe fresh_read=PASS \\
        [--artifact eval-report/summary.json ...]

`inputs` (src, contracts, prompts, ops) come from run_manifest.tree_hashes(); each --artifact path (relative to
delivery/) is hashed as it stands now. observed_at is the current UTC time. Re-run this after any change to the
tree or the artifacts, or build_release.live() will report NOT_RUN.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts')]
from run_manifest import tree_hashes  # noqa: E402

LIVE_INPUT_KEYS = ('src', 'contracts', 'prompts', 'ops')


def main(argv: list[str] | None = None, delivery: Path = ROOT / 'delivery') -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--probe', action='append', default=[], metavar='name=STATUS')
    p.add_argument('--artifact', action='append', default=[], metavar='rel')
    a = p.parse_args(sys.argv[1:] if argv is None else argv)

    probes = {}
    for item in a.probe:
        name, sep, status = item.partition('=')
        if not sep or not name or not status:
            p.error(f'--probe must be name=STATUS, got {item!r}')
        probes[name] = status

    artifacts = {}
    for rel in a.artifact:
        f = delivery / rel
        if not f.is_file():
            p.error(f'artifact not found under delivery/: {rel}')
        artifacts[rel] = hashlib.sha256(f.read_bytes()).hexdigest()

    hashes = tree_hashes()
    doc = {'observed_at': datetime.now(timezone.utc).isoformat(),
           'inputs': {k: hashes[k] for k in LIVE_INPUT_KEYS if k in hashes},
           'artifacts': artifacts, 'probes': probes}
    out = delivery / 'live-evidence' / 'chatgpt_live_probe.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + '\n')
    print(json.dumps(doc, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
