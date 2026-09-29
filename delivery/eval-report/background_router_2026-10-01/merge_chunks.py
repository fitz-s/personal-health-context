"""merge_chunks.py OUT CHUNK...: combine foreground chunk runs of one split into one campaign directory."""
import json, shutil, sys
from pathlib import Path
out = Path(sys.argv[1]); (out / 'traces').mkdir(parents=True, exist_ok=True)
rows, metas = [], []
for c in map(Path, sys.argv[2:]):
    metas.append(json.loads((c / 'run_meta.json').read_text()))
    for r in (json.loads(x) for x in (c / 'results_all_runs.jsonl').read_text().splitlines() if x.strip()):
        if 'evidence_file' in r:
            shutil.copy(c / r['evidence_file'], out / 'traces' / Path(r['evidence_file']).name)
        rows.append(r)
meta = dict(metas[0], cases=[x for m in metas for x in m['cases']], runs=sum(m['runs'] for m in metas),
            chunks=[str(c) for c in sys.argv[2:]])
(out / 'run_meta.json').write_text(json.dumps(meta, indent=1))
(out / 'results_all_runs.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
for c in sys.argv[2:]:
    shutil.rmtree(c)
print(out, len(rows))
