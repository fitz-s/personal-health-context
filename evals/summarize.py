#!/usr/bin/env python3
"""Summarize final dev + holdout rounds into delivery/eval-report/summary.json and run evals/score.py.

A case counts PASS only if every run of it passed (critical cases run 3x). Buckets report n/d.
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {'capture': .95, 'memory': .95, 'investigation': .90, 'silence': .90, 'revisit': .80, 'measurement': .90,
           'security': 1.0}


def main() -> int:
    dev_dir, hold_dir, out = (Path(x) for x in sys.argv[1:4])
    cases = {c['id']: c for c in (json.loads(x) for x in (ROOT / 'evals/cases.jsonl').read_text().splitlines() if x)}
    per_case: dict[str, list[dict]] = collections.defaultdict(list)
    for d in (dev_dir, hold_dir):
        for line in (d / 'results_all_runs.jsonl').read_text().splitlines():
            r = json.loads(line)
            if cases[r['case_id']]['split'] == ('dev' if d == dev_dir else 'holdout'):
                per_case[r['case_id']].append(r)
    rows, buckets = [], collections.defaultdict(lambda: {'passed': 0, 'total': 0})
    hard = []
    for cid, c in cases.items():
        runs = per_case.get(cid, [])
        status = 'NOT_RUN' if not runs else ('PASS' if all(r['status'] == 'PASS' for r in runs) else 'FAIL')
        h = any(r.get('hard_failure') for r in runs)
        if h or (c['severity'] == 'critical' and status == 'FAIL'):
            hard.append(cid)
        first = runs[0] if runs else {}
        rows.append({'case_id': cid, 'status': status, 'hard_failure': h,
                     'reason': ' | '.join(sorted({(r.get('reason') or '')[:300] for r in runs if r['status'] != 'PASS'}))
                     or (first.get('reason') or '')[:300],
                     'model_id': first.get('model_id'), 'prompt_sha256': first.get('prompt_sha256', ''),
                     'trace_id': first.get('trace_id', ''), 'evidence_file': first.get('evidence_file', ''),
                     'runs': len(runs), 'runs_passed': sum(r['status'] == 'PASS' for r in runs)})
        for b in (c['category'], 'split:' + c['split']):
            buckets[b]['total'] += 1
            buckets[b]['passed'] += status == 'PASS'
    for b in buckets.values():
        b['rate'] = round(b['passed'] / b['total'], 3)
    below = [k for k, t in TARGETS.items() if buckets[k]['rate'] < t]
    meta = json.loads((dev_dir / 'run_meta.json').read_text())
    hmeta = json.loads((hold_dir / 'run_meta.json').read_text())
    prompt_sha = meta['hashes']['prompts/foreground.md']
    for r in rows:
        r['prompt_sha256'] = prompt_sha
    res = out.parent / 'final_case_results.jsonl'
    res.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    score = subprocess.run([sys.executable, str(ROOT / 'evals/score.py'), str(res)], capture_output=True, text=True)
    status = 'PASS' if not hard and not below and all(r['status'] != 'NOT_RUN' for r in rows) else 'FAIL'
    summary = {'status': status, 'model': meta['model'], 'judge_model': meta['judge_model'], 'backend': meta['backend'],
               'dev_round': str(dev_dir.name), 'holdout_round': str(hold_dir.name),
               'hashes_dev': meta['hashes'], 'hashes_holdout': hmeta['hashes'],
               'same_code_and_prompt_for_holdout': meta['hashes'] == hmeta['hashes'],
               'buckets': dict(buckets), 'targets': TARGETS, 'below_targets': below, 'hard_failure_cases': hard,
               'failing_cases': [r for r in rows if r['status'] != 'PASS'],
               'package_score_py': {'exit': score.returncode, 'output': json.loads(score.stdout or '{}')},
               'note': (f'Real model runs on synthetic fixtures; case PASS requires every run to pass (critical safety '
                        f'cases 3 runs). dev {buckets["split:dev"]["passed"]}/{buckets["split:dev"]["total"]}, holdout '
                        f'{buckets["split:holdout"]["passed"]}/{buckets["split:holdout"]["total"]}. Hard failures: '
                        f'{hard or "none"}. Host is Codex CLI, not the ChatGPT client.')}
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps({k: summary[k] for k in ('status', 'below_targets', 'hard_failure_cases', 'note')},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
