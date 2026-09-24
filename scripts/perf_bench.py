#!/usr/bin/env python3
"""Local latency on a synthetic root: capture, warm bootstrap, original save, small query. Reports P50/P95."""
import json, os, sys, tempfile, time, statistics, hashlib, platform
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phctx.store import Store

def pct(xs, p): xs = sorted(xs); return round(xs[min(len(xs) - 1, int(len(xs) * p))] * 1000, 2)

root = Path(tempfile.mkdtemp()) / 'bench'
s = Store(root, 'synthetic')
s.register_source('synthetic:watch', 'Synthetic Watch')
# background volume: 200k observations, 5k records
for page in range(40):
    s.ingest_batch(request_id=f'b{page}', source_id='synthetic:watch', cursor=str(page), deleted_ids=[], samples=[
        {'native_id': f'n{page}-{i}', 'metric': ['hr', 'steps', 'sleep', 'hrv'][i % 4],
         'start_at': f'2026-0{1 + (page % 8)}-{1 + i % 28:02d}T{i % 24:02d}:00:00-05:00',
         'end_at': f'2026-0{1 + (page % 8)}-{1 + i % 28:02d}T{i % 24:02d}:30:00-05:00',
         'value_num': float(i % 97), 'unit': 'u', 'timezone': 'America/Chicago', 'source_name': 'W'} for i in range(5000)])
for i in range(5000):
    s.put_record(request_id=f'r{i}', kind='note' if i % 5 else 'event', text=f'SYNTHETIC note {i} 姿态 training',
                 occurred_at=f'2026-0{1 + i % 8}-{1 + i % 28:02d}T10:00:00-05:00')
res = {}
t = []
for i in range(200):
    a = time.perf_counter(); s.put_record(request_id=f'c{i}', kind='event', text='SYNTHETIC capture', occurred_at='2026-09-23T09:00:00-05:00'); t.append(time.perf_counter() - a)
res['capture'] = t
t = []
s.bootstrap()
for i in range(50):
    a = time.perf_counter(); s.bootstrap(); t.append(time.perf_counter() - a)
res['bootstrap_warm'] = t
t = []
for i in range(50):
    data = os.urandom(2 * 1024 * 1024)
    a = time.perf_counter(); s.put_attachment_bytes(request_id=f'f{i}', data=data, filename='x.bin', mime='application/octet-stream', text='SYNTHETIC', occurred_at='2026-09-23T09:00:00-05:00'); t.append(time.perf_counter() - a)
res['original_save_2MiB'] = t
t = []
for i in range(50):
    a = time.perf_counter(); s.query_readonly("SELECT metric, count(*), avg(value_num) FROM active_observations WHERE start_at >= ? AND start_at < ? GROUP BY metric", ['2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00']); t.append(time.perf_counter() - a)
res['query_month_aggregate'] = t
t = []
for i in range(50):
    a = time.perf_counter(); s.search(query='姿态', limit=50); t.append(time.perf_counter() - a)
res['search_cjk'] = t
out = {'machine': platform.platform(), 'python': platform.python_version(),
       'volume': {'observations': 200000, 'records': 5200, 'objects': 50},
       'ms': {k: {'p50': pct(v, .5), 'p95': pct(v, .95), 'n': len(v)} for k, v in res.items()},
       'targets_ms_p95': {'capture': 1000, 'bootstrap_warm': 1000, 'original_save_2MiB': 2000, 'query_month_aggregate': 2000}}
print(json.dumps(out, indent=1))
