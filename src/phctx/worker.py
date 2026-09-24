"""Single background worker: change watermark → relevant jobs → optional model investigation → silence/outbox.

No relevant new evidence ⇒ no model call. Jobs are durable and at-least-once; side effects are idempotent via
request_id. A failed/disabled model leaves jobs queued with backoff — never marks a question done, never
creates a user message to compensate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import extract, model
from .config import Config
from .store import Store, StoreError, dump, utc_in, utcnow

log = logging.getLogger('phctx.worker')
LEASE_SECONDS = 1800
REVIEW_DAYS = 7
MAX_ATTEMPTS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def acquire(store: Store, name: str, owner: str, seconds: int = LEASE_SECONDS) -> bool:
    with store.transaction() as c:
        row = c.execute('SELECT owner, expires_at FROM leases WHERE name=?', (name,)).fetchone()
        if row and row['expires_at'] > utcnow() and row['owner'] != owner:
            return False
        c.execute('INSERT INTO leases VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, '
                  'expires_at=excluded.expires_at', (name, owner, utc_in(seconds)))
    return True


def release(store: Store, name: str, owner: str) -> None:
    with store.transaction() as c:
        c.execute('DELETE FROM leases WHERE name=? AND owner=?', (name, owner))


def _meta(c, key: str, default: str) -> str:
    row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _set_meta(c, key: str, value: str) -> None:
    c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def open_questions(store: Store) -> list[dict]:
    with store.connect() as c:
        rows = c.execute("SELECT * FROM active_records WHERE kind='question' ORDER BY occurred_at").fetchall()
    out = []
    for r in rows:
        p = json.loads(r['payload_json'])
        if p.get('state', 'open') not in Store.CLOSED_QUESTION:
            out.append({'id': r['id'], 'text': r['text'], 'payload': p, 'created_at': r['created_at']})
    return out


def relevant(question: dict, change: dict, detail: dict | str, record: dict | None) -> bool:
    """Does a change plausibly bear on this question? Uses the question's own watch terms/metrics.

    This only decides whether to spend a model call; the model itself decides what the evidence means.
    """
    p = question['payload']
    metrics = [m.lower() for m in p.get('watch_metrics', [])]
    terms = [t.lower() for t in p.get('watch_terms', [])]
    if change['entity'] == 'observations' and isinstance(detail, dict):
        return any(m in x.lower() for m in metrics for x in detail.get('metrics', []))
    if change['entity'] == 'record' and record is not None:
        if record['id'] == question['id'] or record['kind'] == 'question':
            return False
        hay = (record['text'] + ' ' + record['payload_json']).lower()
        return any(t in hay for t in terms)
    if change['entity'] == 'extraction':
        return bool(terms) and record is not None and any(t in (record.get('pages') or '').lower() for t in terms)
    return False


def plan(store: Store, cfg: Config) -> dict:
    """Read changes since the watermark and enqueue deduplicated jobs. Advances the watermark atomically."""
    with store.transaction() as c:
        mark = int(_meta(c, 'worker_watermark', '0'))
        changes = [dict(r) for r in c.execute('SELECT * FROM changes WHERE seq>? ORDER BY seq LIMIT 5000', (mark,))]
        if not changes:
            return {'changes': 0, 'enqueued': []}
        questions = []
        for r in c.execute("SELECT * FROM active_records WHERE kind='question'"):
            p = json.loads(r['payload_json'])
            if p.get('state', 'open') not in Store.CLOSED_QUESTION:
                questions.append({'id': r['id'], 'text': r['text'], 'payload': p})
        hits: dict[str, list[dict]] = {}
        for ch in changes:
            detail = ch['detail']
            try:
                detail = json.loads(detail) if detail.startswith('{') else detail
            except ValueError:
                pass
            record = None
            if ch['entity'] == 'record':
                row = c.execute('SELECT * FROM records WHERE id=?', (ch['entity_id'],)).fetchone()
                record = dict(row) if row else None
            elif ch['entity'] == 'extraction':
                pages = ' '.join(t for (t,) in c.execute('SELECT text FROM object_pages WHERE object_sha=?',
                                                         (ch['entity_id'],)))
                record = {'pages': pages[:200000]}
            for q in questions:
                if relevant(q, ch, detail, record):
                    hits.setdefault(q['id'], []).append({'seq': ch['seq'], 'entity': ch['entity'],
                                                         'id': ch['entity_id'], 'detail': detail})
        enqueued = []
        now = utcnow()
        question = "type='revisit' AND json_extract(payload_json, '$.question_id')=?"
        for qid, evid in hits.items():
            # One open job per question: new evidence joins it (a running job re-queues on completion, see execute).
            open_job = c.execute(f"SELECT id, dedupe_key, payload_json FROM jobs WHERE {question} "
                                 "AND state IN('queued','running') LIMIT 1", (qid,)).fetchone()
            if open_job:
                p = json.loads(open_job['payload_json'])
                p['evidence'] = (p['evidence'] + evid)[-50:]
                c.execute('UPDATE jobs SET payload_json=?, updated_at=? WHERE id=?', (dump(p), now, open_job['id']))
                enqueued.append(open_job['dedupe_key'])
                continue
            key = 'revisit:' + qid + ':' + hashlib.sha256(dump([e['seq'] for e in evid]).encode()).hexdigest()[:16]
            # Minimum semantic interval per question, measured from the last executed investigation.
            last = c.execute(f"SELECT max(updated_at) FROM jobs WHERE {question} AND state='done'", (qid,)).fetchone()[0]
            run_at = now
            if last:
                earliest = datetime.fromisoformat(last) + timedelta(seconds=cfg.minimum_semantic_interval_seconds)
                run_at = max(now, earliest.isoformat(timespec='microseconds'))
            c.execute('INSERT OR IGNORE INTO jobs(id,type,dedupe_key,state,payload_json,next_run_at,created_at,'
                      "updated_at) VALUES(?,?,?,'queued',?,?,?,?)",
                      ('job_' + uuid.uuid4().hex, 'revisit', key, dump({'question_id': qid, 'evidence': evid[:50]}),
                       run_at, now, now))
            enqueued.append(key)
        last_review = _meta(c, 'last_review_at', '')
        substantive = any(ch['entity'] in {'record', 'observations', 'extraction'} for ch in changes)
        if substantive and (not last_review or datetime.fromisoformat(last_review) < _now() - timedelta(days=REVIEW_DAYS)):
            key = f'review:{changes[-1]["seq"]}'
            c.execute('INSERT OR IGNORE INTO jobs(id,type,dedupe_key,state,payload_json,next_run_at,created_at,'
                      "updated_at) VALUES(?,?,?,'queued',?,?,?,?)",
                      ('job_' + uuid.uuid4().hex, 'review', key,
                       dump({'since_seq': mark, 'through_seq': changes[-1]['seq']}), now, now, now))
            _set_meta(c, 'last_review_at', now)
            enqueued.append(key)
        _set_meta(c, 'worker_watermark', str(changes[-1]['seq']))
    return {'changes': len(changes), 'enqueued': enqueued}


def calls_today(store: Store) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec='microseconds')
    with store.connect() as c:
        return c.execute("SELECT count(*) FROM model_calls WHERE started_at>=? AND status!='skipped'",
                         (start,)).fetchone()[0]


def task_text(store: Store, job: dict) -> str:
    p = json.loads(job['payload_json'])
    if job['type'] == 'revisit':
        q = store.get_records([p['question_id']])['records']
        qtext = q[0]['text'] if q else '(missing)'
        return (f'Revisit open question {p["question_id"]}: "{qtext}".\n'
                f'New evidence since the last look (change descriptors, read the actual data with tools): '
                f'{dump(p["evidence"])[:6000]}\n'
                'Read the question, any prior analyses that cite it (search analyses mentioning its id), and the new '
                'evidence. Surface only if the new evidence changes the answer, makes it answerable, contradicts a '
                'prior analysis, or shows passive data cannot answer it and a specific measurement would. '
                f'Set question_id to {p["question_id"]} when surfacing.')
    return ('Periodic broad review for unknown unknowns and measurement gaps across all permitted local data. '
            f'Changes window seq {p["since_seq"]}..{p["through_seq"]}. Default to silence unless a finding has '
            'concrete decision value and is not a repeat of anything already surfaced.')


def execute(store: Store, cfg: Config, job: dict, owner: str, config_path: str,
            scripted=None) -> dict:
    backend = cfg.model_backend if cfg.model_enabled else 'none'
    call_id = 'mc_' + uuid.uuid4().hex
    started = utcnow()
    prompt_sha = hashlib.sha256(model.background_prompt().encode()).hexdigest()
    try:
        if backend == 'none':
            raise model.ModelError('model_disabled')
        if calls_today(store) >= cfg.daily_call_cap:
            raise model.ModelError('budget_exhausted')
        with store.transaction() as c:
            c.execute('INSERT INTO model_calls VALUES(?,?,?,?,?,?,NULL,?,NULL,NULL)',
                      (call_id, job['id'], backend, cfg.model_id or backend, prompt_sha, started, 'running'))
        result = model.investigate(backend, cfg.model_id, task_text(store, job), config_path=config_path,
                                   scripted=scripted)
    except model.ModelError as e:
        attempts = job['attempts'] + 1
        delay = min(3600 * 24, 900 * 2 ** attempts)
        final = e.code not in {'model_disabled', 'budget_exhausted'} and attempts >= MAX_ATTEMPTS
        with store.transaction() as c:
            c.execute("UPDATE model_calls SET finished_at=?, status='failed', error_code=? WHERE id=?",
                      (utcnow(), e.code, call_id))
            if not c.execute('UPDATE jobs SET state=?, attempts=?, next_run_at=?, last_error_code=?, lease_owner=NULL, '
                             'lease_expires_at=NULL, updated_at=? WHERE id=? AND lease_owner=?',
                             ('failed' if final else 'queued',
                              attempts if e.code != 'model_disabled' else job['attempts'],
                              utc_in(delay), e.code, utcnow(), job['id'], owner)).rowcount:
                return {'job': job['dedupe_key'], 'outcome': 'lease_lost'}
        return {'job': job['dedupe_key'], 'outcome': 'deferred', 'error': e.code}
    with store.transaction() as c:
        c.execute("UPDATE model_calls SET finished_at=?, status='done', model_id=? WHERE id=?",
                  (utcnow(), result.model_id, call_id))

    def finish(c) -> None:
        """Complete only while this worker still owns the job. Evidence merged in during the run re-queues it, due
        one minimum interval after this execution."""
        now = utcnow()
        if not c.execute("UPDATE jobs SET state=CASE WHEN payload_json=? THEN 'done' ELSE 'queued' END, next_run_at=?, "
                         'attempts=attempts+1, lease_owner=NULL, lease_expires_at=NULL, last_error_code=NULL, '
                         'updated_at=? WHERE id=? AND lease_owner=?',
                         (job['payload_json'], utc_in(cfg.minimum_semantic_interval_seconds), now, job['id'],
                          owner)).rowcount:
            raise StoreError('lease_lost', 'Another worker owns this job; its result is dropped.')
    cand = result.candidate
    gate, done = {'queued': False, 'reason': 'silence'}, False
    try:
        if cand['decision'] == 'surface':
            # Shadow candidates go to shadow_insights only: never the outbox, dedup or attention budget.
            queue = store.queue_shadow if cfg.worker_mode == 'shadow' else store.queue_insight
            try:
                # attempts advances in the same transaction, so a re-run of a re-queued job is a new request; the
                # owner makes a stale owner's replay miss the receipt and hit the fence instead.
                gate, done = queue(request_id=f'worker:{job["id"]}:{job["attempts"]}:{owner}', candidate=cand,
                                   fence=finish, read_at=started), True
            except StoreError as e:
                if e.code == 'lease_lost':
                    raise
                gate = {'queued': False, 'reason': 'gate_rejected:' + e.code}
        if not done:
            with store.transaction() as c:
                finish(c)
    except StoreError as e:
        if e.code != 'lease_lost':
            raise
        log.info('lease_lost job=%s', job['id'])
        return {'job': job['dedupe_key'], 'outcome': 'lease_lost'}
    return {'job': job['dedupe_key'], 'outcome': cand['decision'], 'gate': gate, 'tool_calls': len(result.trace)}


def run_once(cfg: Config, config_path: str | None = None, scripted=None) -> dict:
    store = Store(cfg.root, cfg.profile)
    owner = 'w_' + uuid.uuid4().hex
    if not acquire(store, 'worker', owner):
        return {'outcome': 'lease_held_by_other_worker'}
    run_id = 'run_' + uuid.uuid4().hex
    with store.transaction() as c:
        c.execute('INSERT INTO worker_runs(id, started_at) VALUES(?,?)', (run_id, utcnow()))
    summary: dict = {'run_id': run_id}
    try:
        summary['extraction'] = len(extract.extract_pending(store))
        summary['plan'] = plan(store, cfg)
        with store.transaction() as c:
            due = [dict(r) for r in c.execute(
                "SELECT * FROM jobs WHERE state='queued' AND next_run_at<=? ORDER BY next_run_at LIMIT 5", (utcnow(),))]
            for j in due:
                c.execute("UPDATE jobs SET state='running', lease_owner=?, lease_expires_at=? WHERE id=?",
                          (owner, utc_in(LEASE_SECONDS), j['id']))
            # Recover jobs whose worker died mid-run.
            c.execute("UPDATE jobs SET state='queued', lease_owner=NULL WHERE state='running' AND lease_expires_at<?",
                      (utcnow(),))
        results = [execute(store, cfg, j, owner, config_path or str(cfg.source or ''), scripted) for j in due]
        summary['jobs'] = results
        surfaced = sum(1 for r in results if r.get('gate', {}).get('queued'))
        calls = sum(1 for r in results if r['outcome'] in {'silence', 'surface'})
        outcome = 'idle' if not due else 'ran'
        with store.transaction() as c:
            c.execute('UPDATE worker_runs SET finished_at=?, outcome=?, changes_seen=?, jobs_run=?, model_calls=?, '
                      'surfaced=? WHERE id=?', (utcnow(), outcome, summary['plan']['changes'], len(due), calls,
                                               surfaced, run_id))
        summary['outcome'] = outcome
        if surfaced and cfg.macos_notification_enabled:  # shadow candidates are never `queued`
            notify()
        return summary
    except Exception as e:
        code = e.code if isinstance(e, StoreError) else type(e).__name__
        with store.transaction() as c:
            c.execute("UPDATE worker_runs SET finished_at=?, outcome='error', error_code=? WHERE id=?",
                      (utcnow(), code, run_id))
        log.exception('worker_error code=%s', code)
        return {**summary, 'outcome': 'error', 'error': code}
    finally:
        release(store, 'worker', owner)


def notify() -> None:
    """Content-free local notification (user opt-in only): no condition names, values or text."""
    import subprocess
    subprocess.run(['/usr/bin/osascript', '-e',
                    'display notification "有一项值得回看的更新。下次对话时会提到。" with title "Personal Health Context"'],
                   capture_output=True)


def run_forever(cfg: Config) -> None:
    while True:
        run_once(cfg)
        time.sleep(cfg.change_check_seconds)


def status(cfg: Config) -> dict:
    store = Store(cfg.root, cfg.profile)
    with store.connect() as c:
        runs = [dict(r) for r in c.execute('SELECT started_at, finished_at, outcome, changes_seen, jobs_run, '
                                           'model_calls, surfaced, error_code FROM worker_runs '
                                           'ORDER BY started_at DESC LIMIT 5')]
        first = c.execute('SELECT min(started_at) FROM worker_runs').fetchone()[0]
        jobs = {r[0]: r[1] for r in c.execute('SELECT state, count(*) FROM jobs GROUP BY state')}
        shadow = c.execute('SELECT count(*) FROM shadow_insights').fetchone()[0]
    observed = None
    if first:
        observed = round((_now() - datetime.fromisoformat(first)).total_seconds() / 86400, 2)
    return {'mode': cfg.worker_mode, 'model': {'enabled': cfg.model_enabled, 'backend': cfg.model_backend,
                                               'model_id': cfg.model_id},
            'first_run_at': first, 'observed_days': observed, 'recent_runs': runs, 'jobs': jobs,
            'shadow_candidates': shadow, 'calls_today': calls_today(store)}
