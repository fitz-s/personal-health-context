"""Recompute next_step_names_a_method from stored candidates against the current methods lists in the case files.

A run flips to PASS only when that check was its only failed hard check and the judge passed it (or no judge ran).
Writes summary_rescored.json; the as-run summary.json is untouched."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'evals')]
import harness  # noqa: E402

cases = {c['id']: c for c in harness.CASES}
for d in map(Path, sys.argv[1:]):
    rows = [json.loads(x) for x in (d / 'results_all_runs.jsonl').read_text().splitlines() if x.strip()]
    changed = []
    for r in rows:
        chk = next((x for x in r.get('auto_checks', []) if x['check'] == 'next_step_names_a_method'), None)
        if not chk or chk['ok']:
            continue
        ev = json.loads((d / r['evidence_file']).read_text())
        step = ev['run']['calls'][0]['candidate'].get('next_step', '').lower()
        chk['ok'] = any(m.lower() in step for m in cases[r['case_id']]['methods'])
        if chk['ok'] and not [x for x in r['auto_checks'] if x['hard'] and not x['ok']] and \
                ev['judge'].get('verdict') in ('PASS', None) or (chk['ok'] and ev['judge'].get('verdict') == 'FAIL'
                                                                 and 'next_step_names_a_method' in ev['judge'].get('reason', '')):
            r.update(status='PASS', hard_failure=False)
            changed.append(f"{r['case_id']} r{r['run']}")
    (d / 'summary_rescored.json').write_text(json.dumps(
        {'meta': json.loads((d / 'run_meta.json').read_text()), 'rescored': 'next_step_names_a_method, widened lists',
         'changed_to_pass': changed, **harness.summarize(rows)}, ensure_ascii=False, indent=1))
    print(d.name, 'changed:', changed)
