#!/bin/bash
# campaign.sh OUT SPLIT MODEL: run one background campaign; rerun quota NOT_RUN rows on the same code after the router
# recovers, and merge them in place (originals kept in results_all_runs_not_run_original.jsonl). Synthetic only.
set -u
OUT=$1; SPLIT=$2; MODEL=$3
R=/Users/leofitz/personal-health-context; P=$R/.venv/bin/python; H=$R/evals/harness.py
PROBE=/private/tmp/claude-501/-Users-leofitz/fa3c7f95-a8e5-4db4-bb5e-f4729975c319/scratchpad/probe.py
A="--backend router --judge-model cx/gpt-6-sol --effort medium --suite background --workers 2"
wait_ok() { until $P $PROBE "$MODEL" 2>&1 | grep -q '^OK' && $P $PROBE cx/gpt-6-sol 2>&1 | grep -q '^OK'; do sleep 240; done; }
wait_ok
$P $H --out "$OUT" --split "$SPLIT" --model "$MODEL" --repeat 3 $A > "$OUT.log" 2>&1
for pass in 1 2 3; do
  NR=$($P - "$OUT" <<'PY'
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1] + '/results_all_runs.jsonl')]
print(','.join(sorted({r['case_id'] for r in rows if r['status'] == 'NOT_RUN'})))
PY
)
  [ -z "$NR" ] && break
  wait_ok
  rm -rf "$OUT.rerun"
  $P $H --out "$OUT.rerun" --cases "$NR" --model "$MODEL" --repeat 3 $A > "$OUT.rerun.log" 2>&1
  $P - "$OUT" <<'PY'
import json, shutil, sys
from pathlib import Path
d = Path(sys.argv[1]); rr = Path(str(d) + '.rerun')
rows = [json.loads(x) for x in (d / 'results_all_runs.jsonl').read_text().splitlines() if x.strip()]
new = {(r['case_id'], r['run']): r for r in (json.loads(x) for x in (rr / 'results_all_runs.jsonl').read_text().splitlines() if x.strip())}
orig = d / 'results_all_runs_not_run_original.jsonl'
with open(orig, 'a') as fh:
    for r in rows:
        if r['status'] == 'NOT_RUN':
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
out = []
for r in rows:
    k = (r['case_id'], r['run'])
    if r['status'] == 'NOT_RUN' and k in new and new[k]['status'] != 'NOT_RUN':
        n = dict(new[k]); src = rr / n['evidence_file']; dst = d / 'traces' / src.name
        shutil.copy(src, dst); n['evidence_file'] = str(dst.relative_to(d)); r = n
    out.append(r)
(d / 'results_all_runs.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in out))
PY
done
$P $H --out "$OUT" --summarize
echo done > "$OUT.flag"
