"""Re-run the (Sol) judge on stored surface candidates with the full tool results the assistant saw. The round-3
iteration-3 judge saw only the first 3,000 characters of each tool result and flagged values that appear later in them
as unverified. Updates the judge verdict in each trace and writes results_all_runs_rejudged.jsonl; rescore.py then
reads the updated verdicts. Deterministic checks are untouched."""
import concurrent.futures
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'evals')]
import harness  # noqa: E402

cases = {c['id']: c for c in harness.CASES}


def one(d: Path, r: dict):
    f = d / r['evidence_file']
    ev = json.loads(f.read_text())
    if not any(c.get('candidate', {}).get('decision') == 'surface' for c in ev['run'].get('calls', [])):
        return r['case_id'], r['run'], None
    v = harness.judge_router(cases[r['case_id']], ev['run'], r['auto_checks'], 'cx/gpt-6-sol')
    ev['judge_first_pass'] = ev['judge']
    ev['judge'] = v
    f.write_text(json.dumps(ev, ensure_ascii=False, indent=1, default=str))
    return r['case_id'], r['run'], v.get('verdict')


for d in map(Path, sys.argv[1:]):
    rows = [json.loads(x) for x in (d / 'results_all_runs.jsonl').read_text().splitlines() if x.strip()]
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        out = list(ex.map(lambda r: one(d, r), rows))
    for (cid, run, v), r in zip(out, rows):
        if v in ('PASS', 'FAIL'):
            hard = [x for x in r['auto_checks'] if x['hard'] and not x['ok']]
            r['status'] = 'FAIL' if hard or v == 'FAIL' else 'PASS'
    (d / 'results_all_runs_rejudged.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    print(d.name, [(c, n, v) for c, n, v in out if v])
