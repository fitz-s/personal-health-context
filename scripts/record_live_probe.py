#!/usr/bin/env python3
"""Write delivery/live-evidence/chatgpt_live_probe.json: the structured evidence build_release.live() checks
against probes observed by hand in the user's ChatGPT (no command can re-run these).

    scripts/record_live_probe.py --probe write=PASS:live-evidence/x.md,live-evidence/y.txt \\
        --probe fresh_read=PASS:live-evidence/x.md

Each --probe is name=STATUS[:rel1,rel2,...] (artifacts optional, comma-separated, relative to delivery/): the
listed files are what an operator or reviewer can independently re-open to check the probe actually happened,
distinct from merely asserting STATUS. build_release.live() requires a needed probe's status to be PASS AND at
least one of its listed artifacts to still hash as recorded. `inputs` (src, contracts, prompts, ops) come from
run_manifest.tree_hashes(); every artifact referenced by any probe is hashed as it stands now and recorded in the
top-level `artifacts` map. observed_at is the current UTC time. Re-run this after any change to the tree or the
artifacts, or build_release.live() will report NOT_RUN.
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
    p.add_argument('--probe', action='append', default=[], metavar='name=STATUS[:rel1,rel2,...]')
    a = p.parse_args(sys.argv[1:] if argv is None else argv)

    probes: dict[str, dict] = {}
    artifacts: dict[str, str] = {}
    for item in a.probe:
        name, sep, rest = item.partition('=')
        if not sep or not name or not rest:
            p.error(f'--probe must be name=STATUS[:rel1,rel2,...], got {item!r}')
        status, _, rels = rest.partition(':')
        rel_list = [r for r in rels.split(',') if r]
        for rel in rel_list:
            f = delivery / rel
            if not f.is_file():
                p.error(f'probe {name!r} names an artifact not found under delivery/: {rel}')
            artifacts[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        probes[name] = {'status': status, 'artifacts': rel_list}

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
