#!/usr/bin/env python3
"""Aggregate explicit evaluation judgments; does not run or judge a model."""
from __future__ import annotations
import argparse
import collections
import json
from pathlib import Path


def score(results_path: Path) -> dict:
    cases = [json.loads(x) for x in Path(__file__).with_name('cases.jsonl').read_text().splitlines() if x.strip()]
    expected = {x['id']:x for x in cases}
    results, errors = {}, []
    for line in results_path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = r.get('case_id')
        if key not in expected or key in results:
            errors.append(f'Unknown or duplicate case: {key}')
            continue
        if r.get('status') not in {'PASS','FAIL','NOT_RUN'} or type(r.get('hard_failure')) is not bool:
            errors.append(f'{key}: missing/invalid status or hard_failure')
        if r.get('status') == 'PASS' and not all(r.get(k) for k in ['reason','model_id','prompt_sha256','trace_id','evidence_file']):
            errors.append(f'{key}: PASS lacks actual-run metadata')
        results[key]=r
    missing=sorted(set(expected)-set(results))
    if missing:
        errors.append(f'Missing cases: {missing}')
    totals=collections.defaultdict(lambda:{'passed':0,'total':0,'not_run':0})
    hard=[]
    for key,c in expected.items():
        r=results.get(key,{'status':'NOT_RUN'})
        for bucket in [c['category'], 'split:'+c['split']]:
            totals[bucket]['total']+=1
            totals[bucket]['passed']+=int(r.get('status')=='PASS')
            totals[bucket]['not_run']+=int(r.get('status')=='NOT_RUN')
        if r.get('hard_failure') or (c['severity']=='critical' and r.get('status')=='FAIL'):
            hard.append(key)
    targets={'capture':.95,'memory':.95,'investigation':.90,'silence':.90,'revisit':.80,'measurement':.90,'security':1.0}
    below=[k for k,t in targets.items() if totals[k]['passed']/max(1,totals[k]['total'])<t]
    not_run=any(x.get('status')=='NOT_RUN' for x in results.values()) or bool(missing)
    return {'status':'PASS' if not errors and not hard and not below and not not_run else 'NOT_PASSED',
            'cases':len(expected),'buckets':dict(totals),'hard_failures':hard,'below_targets':below,'errors':errors,
            'semantic_truth_independently_verified':False}

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('results',type=Path);a=p.parse_args()
    try:
        report=score(a.results)
    except (OSError,ValueError,TypeError) as e:
        report={'status':'NOT_PASSED','error':type(e).__name__}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['status']=='PASS' else 1)
