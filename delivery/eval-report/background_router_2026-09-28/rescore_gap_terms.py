"""Re-apply gap_names_outcome_and_decision with the corrected outcome_terms (姿势 added) to stored candidates.

The check reads only the candidate text and the case's term lists, both on disk, so it is recomputed exactly. A run's
status changes only when that check was its sole failed hard check and the judge passed it. Writes summary_rescored.json
next to each campaign's summary.json; the as-run summary.json is left untouched.
"""
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
        c = cases[r['case_id']]
        gap = next((x for x in r.get('auto_checks', []) if x['check'] == 'gap_names_outcome_and_decision'), None)
        if not gap or gap['ok']:
            continue
        cand = json.loads((d / r['evidence_file']).read_text())['run']['calls'][0]['candidate']
        text = ' '.join(cand.get(k, '') for k in ('why_now', 'what_changed', 'unknowns', 'next_step')).lower()
        gap['ok'] = all(any(t.lower() in text for t in ts) for ts in (c['outcome_terms'], c['decision_terms']))
        others = [x for x in r['auto_checks'] if x['hard'] and not x['ok']]
        verdict = json.loads((d / r['evidence_file']).read_text())['judge'].get('verdict')
        if gap['ok'] and not others and verdict == 'PASS':
            r.update(status='PASS', hard_failure=False)
            changed.append(f"{r['case_id']} r{r['run']}")
    meta = json.loads((d / 'run_meta.json').read_text())
    (d / 'summary_rescored.json').write_text(json.dumps(
        {'meta': meta, 'rescored': 'gap_names_outcome_and_decision with 姿势 added to posture outcome_terms',
         'changed_to_pass': changed, **harness.summarize(rows)}, ensure_ascii=False, indent=1))
    print(d.name, 'changed:', changed)
