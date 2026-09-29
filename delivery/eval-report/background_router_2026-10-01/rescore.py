"""Recompute the deterministic candidate checks (evidence, causal wording, question_id, gap terms, method) from the
stored run with the current evals/harness.py and case files, and re-derive each run's status the way the harness does:
FAIL on any failed hard check or a judge FAIL, else PASS. A judge FAIL whose stated reason is only a check that now
passes (the judge sees automatic_checks) counts as PASS. Writes summary_rescored.json; as-run summary.json untouched.
Applied to every campaign identically."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'evals')]
import harness  # noqa: E402

CAND = {'evidence_returned_by_tools', 'no_causal_assertion', 'question_id', 'gap_names_outcome_and_decision',
        'next_step_names_a_method', 'valid_candidate_after_repair'}
cases = {c['id']: c for c in harness.CASES}
for d in map(Path, sys.argv[1:]):
    src = d / 'results_all_runs_rejudged.jsonl'
    rows = [json.loads(x) for x in (src if src.exists() else d / 'results_all_runs.jsonl').read_text().splitlines()
            if x.strip()]
    changed = []
    for r in rows:
        if r['status'] == 'NOT_RUN' or 'evidence_file' not in r:
            continue
        ev = json.loads((d / r['evidence_file']).read_text())
        ctx = {'scenario': cases[r['case_id']]['fixture']['scenario'], 'q': None}
        qids = {c.get('candidate', {}).get('question_id') for c in ev['run'].get('calls', [])}
        old = {x['check']: x for x in r['auto_checks'] if x['check'] in CAND}
        if 'question_id' in old:  # the question's id is not stored separately; keep the as-run verdict for it
            ctx['q'] = next(iter(qids)) if old['question_id']['ok'] else '__as_run_failed__'
        new = harness.candidate_checks(cases[r['case_id']], ctx, ev['run'])
        r['auto_checks'] = [x for x in r['auto_checks'] if x['check'] not in CAND] + new
        hard = [x['check'] for x in r['auto_checks'] if x['hard'] and not x['ok']]
        verdict = ev['judge']
        freed = [k for k, v in old.items() if not v['ok'] and k not in hard]
        # The judge read the as-run automatic_checks; a FAIL that cites a check now passing is re-read as that check.
        cites = ('next_step_names_a_method', 'measurement-method', 'measurement method', '测量方法', 'causal', 'no_causal')
        judge_fail = verdict.get('verdict') == 'FAIL' and not (freed and any(k in verdict.get('reason', '') for k in
                                                                            freed + list(cites)))
        status = 'FAIL' if hard or judge_fail else 'PASS' if verdict.get('verdict') in ('PASS', 'FAIL') else r['status']
        if status != r['status']:
            changed.append(f"{r['case_id']} r{r['run']}: {r['status']}→{status}")
            r['status'], r['hard_failure'] = status, bool(hard) or bool(judge_fail and verdict.get('hard_failure'))
    (d / 'summary_rescored.json').write_text(json.dumps(
        {'meta': json.loads((d / 'run_meta.json').read_text()), 'rescored_with': 'rescore.py (current checks)',
         'changed': changed, **harness.summarize(rows)}, ensure_ascii=False, indent=1))
    print(d.name, changed)
