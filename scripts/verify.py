#!/usr/bin/env python3
"""Offline, dependency-free reference verification. No network or real accounts."""
from __future__ import annotations
import ast
import contextlib
import hashlib
import io
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import unittest
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main() -> int:
    if sys.version_info < (3,11):
        print('Python 3.11+ required.',file=sys.stderr);return 2
    sys.path.insert(0,str(ROOT/'reference_core'))
    checks=[]
    try:
        for p in ROOT.rglob('*.py'):
            ast.parse(p.read_text(),filename=str(p.relative_to(ROOT)))
        checks.append({'name':'python_syntax','status':'PASS'})
        for p in (ROOT/'contracts').glob('*.json'):
            json.loads(p.read_text())
        tools=json.loads((ROOT/'contracts/tools.json').read_text())['tools']
        assert len({x['name'] for x in tools})==len(tools)
        ft=next(x for x in tools if x['name']=='context_capture_file')
        assert ft['_meta']['openai/fileParams']==['file']
        fs=ft['inputSchema']['properties']['file']
        assert set(fs['required'])=={'download_url','file_id'}
        assert set(fs['properties'])=={'download_url','file_id','mime_type','file_name'}
        assert not ft['annotations']['readOnlyHint']
        checks.append({'name':'json_and_file_contract_structure','status':'PASS','tools':len(tools)})
        cases=[json.loads(x) for x in (ROOT/'evals/cases.jsonl').read_text().splitlines() if x.strip()]
        assert len({x['id'] for x in cases})==len(cases)
        assert all(x['synthetic_only'] for x in cases)
        assert {x['split'] for x in cases}=={'dev','holdout'}
        checks.append({'name':'evaluation_case_structure_only','status':'PASS','cases':len(cases),'model_executed':False})
    except (OSError,ValueError,KeyError,AssertionError,SyntaxError) as e:
        checks.append({'name':'static_checks','status':'FAIL','error':str(e)})
    buffer=io.StringIO()
    start=time.perf_counter()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(stream=buffer,verbosity=2).run(suite)
    duration=time.perf_counter()-start
    ev=ROOT/'evidence';ev.mkdir(exist_ok=True)
    (ev/'unittest.log').write_text(buffer.getvalue())
    env={**os.environ,'PYTHONPATH':str(ROOT/'reference_core')}
    demo=subprocess.run([sys.executable,'-m','phctx','demo'],env=env,cwd=ROOT,capture_output=True,text=True,timeout=15)
    (ev/'demo.json').write_text(demo.stdout)
    checks.append({'name':'synthetic_fresh_instance_demo','status':'PASS' if demo.returncode==0 else 'FAIL','exit_code':demo.returncode})
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for directory in ['reference_core','tests','scripts','contracts','prompts','evals'] for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    ok=result.wasSuccessful() and all(x['status']=='PASS' for x in checks)
    report={'status':'OFFLINE_REFERENCE_VERIFIED' if ok else 'OFFLINE_VERIFICATION_FAILED','executed_at':datetime.now(timezone.utc).isoformat(),'environment':{'python':platform.python_version(),'platform':platform.platform(),'sqlite':sqlite3.sqlite_version},'unit_tests':{'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'seconds':duration},'checks':checks,'real_accounts_used':False,'model_calls_executed':0,'not_run':['Production MCP SDK and secure tunnel','Mac launchd/Keychain deployment','ChatGPT actual read/write and original file bytes','iPhone HealthKit build/device/background sync','Oura official MCP account access','Actual model behavioral evaluation','Multi-day real shadow observation','Encrypted iCloud restore'],'source_sha256':hashes}
    (ev/'offline_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='source_sha256'},ensure_ascii=False,indent=2))
    if not ok:print(buffer.getvalue())
    return 0 if ok else 1

if __name__=='__main__':raise SystemExit(main())
