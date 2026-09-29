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
from .tools import ToolContext, Tools

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


JOBS = {'revisit': 'This job was triggered by new data matching one open question. Decide whether it changes what '
                   'the user should know about that question.',
        'review': 'This is the periodic review; no question triggered it. Look across the recorded goals, routines and '
                  'open questions for a measurement gap or an unasked question with decision value now (see Measurement '
                  'gaps), and otherwise stay silent.'}
SCHEMA = ('## Tables you can query (context_query, SQLite)\n'
          'canonical_observations(id, source_id, metric, start_at, end_at, timezone, value_num, value_text, unit, '
          'source_name): one row per sample, times UTC ISO text; there is no observed_at column.\n'
          'active_records(id, kind, occurred_at, timezone, text, payload_json, source_id, object_sha, supersedes, '
          'created_at): current records; kind in event, routine, question, analysis, note, attachment.\n'
          'observation_catalog(source_id, metric, unit, n, first_at, last_at). sources(id, label, state, '
          'last_success_at, latest_sample_at). Field names in this packet are not tables.')
PACKET_CHARS = 60_000  # ≈15–20k tokens: the fixed context; everything else the model retrieves with tools
MEANING = {'pending': 'queued, not yet shown to the user', 'delivered': 'shown to the user',
           'dismissed': 'shown, dismissed by the user', 'stale': 'withdrawn before showing: its evidence changed',
           'expired': 'expired unshown'}


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f'…[truncated: {n} of {len(text)} chars]'


def _section(title: str, items: list, cap: int, empty: str) -> str:
    """One packet section: JSON lines up to `cap` chars, then an explicit marker naming what was left out."""
    lines, used = [], 0
    for i, item in enumerate(items):
        line = dump(item)
        if used + len(line) > cap:
            lines.append(f'[{len(items) - i} more omitted for the packet budget; retrieve them with tools]')
            break
        lines.append(line)
        used += len(line)
    return f'## {title}\n' + ('\n'.join(lines) if lines else empty)


def _record(c, rid: str) -> dict | None:
    row = c.execute('SELECT id, kind, text, occurred_at, timezone, payload_json, supersedes FROM records WHERE id=?',
                    (rid,)).fetchone()
    if not row:
        return None
    out = {'id': row['id'], 'kind': row['kind'], 'occurred_at': row['occurred_at'], 'text': _cut(row['text'], 1500)}
    payload = {k: v for k, v in json.loads(row['payload_json']).items() if k != 'synthetic'}
    if payload:
        out['payload'] = payload if len(dump(payload)) <= 800 else _cut(dump(payload), 800)
    if row['supersedes']:
        old = c.execute('SELECT text FROM records WHERE id=?', (row['supersedes'],)).fetchone()
        out['revises'] = {'id': row['supersedes'], 'text': _cut(old['text'], 600) if old else None,
                          'insights_citing_it': _citing(c, row['supersedes'])}
    return out


def _citing(c, ref: str) -> list[dict]:
    """Insights (and shadow candidates) that already cite `ref`: novelty is a lookup, not a guess."""
    return [dict(r) for r in c.execute(
        "SELECT i.id, i.state FROM insights i, json_each(i.payload_json, '$.evidence_ids') e WHERE e.value=? "
        "UNION ALL SELECT s.id, 'shadow' FROM shadow_insights s, json_each(s.payload_json, '$.evidence_ids') e "
        'WHERE e.value=?', (ref, ref))]


def _values(c, source: str, metric: str, lo: str, hi: str) -> list[tuple]:
    """(start_at, value_num, unit) with lo <= start_at < hi, from one source."""
    return c.execute('SELECT start_at, value_num, unit FROM canonical_observations WHERE source_id=? AND metric=? '
                     'AND start_at>=? AND start_at<? AND value_num IS NOT NULL ORDER BY start_at',
                     (source, metric, lo, hi)).fetchall()


def _stats(rows: list[tuple]) -> dict:
    if not rows:
        return {'n': 0}
    v = [r[1] for r in rows]
    return {'n': len(v), 'mean': round(sum(v) / len(v), 2), 'min': min(v), 'max': max(v), 'first': rows[0][0],
            'last': rows[-1][0], 'unit': ','.join(sorted({r[2] or '' for r in rows}))}


def _observation_change(c, source: str, detail: dict) -> dict:
    """Deterministic before/after for one ingested batch: the batch window's samples against the 28 days before it
    (disjoint) from the same source, where each new value falls relative to that range, and every source that reports
    the metric (device switches show up here)."""
    lo = detail['start']
    hi = (datetime.fromisoformat(detail['end']) + timedelta(microseconds=1)).isoformat(timespec='microseconds')
    base = (datetime.fromisoformat(lo) - timedelta(days=28)).isoformat(timespec='microseconds')
    metrics = []
    for m in detail.get('metrics', [])[:8]:
        new, old = _values(c, source, m, lo, hi), _values(c, source, m, base, lo)
        entry = {'metric': m, 'new_window': _stats(new), 'previous_28_days_same_source': _stats(old)}
        if new and old:
            top, bottom = max(r[1] for r in old), min(r[1] for r in old)
            entry['new_values_vs_previous_range'] = {
                'new_values': [r[1] for r in new][:30], 'previous_range': [bottom, top],
                'above_previous_max': sum(r[1] > top for r in new), 'below_previous_min': sum(r[1] < bottom for r in new),
                'inside_previous_range': sum(bottom <= r[1] <= top for r in new)}
        entry['sources_reporting_metric'] = [dict(r) for r in c.execute(
            'SELECT source_id, unit, n, first_at, last_at FROM observation_catalog WHERE metric=?', (m,))]
        metrics.append(entry)
    return {'source_id': source, 'window': [lo, detail['end']], 'upserted': detail.get('upserted'),
            'deleted': detail.get('deleted'), 'metrics': metrics,
            'note': 'Per-sample values, not daily totals; the previous window ends where the new one starts.'}


def _trigger(c, ev: dict):
    if ev['entity'] == 'record':
        return {'change': 'record', 'record': _record(c, ev['id']), 'already_cited_by': _citing(c, ev['id'])}
    if ev['entity'] == 'observations' and isinstance(ev['detail'], dict):
        return {'change': 'observations', **_observation_change(c, ev['id'], ev['detail'])}
    if ev['entity'] == 'extraction':
        obj = c.execute('SELECT filename, mime FROM objects WHERE sha256=?', (ev['id'],)).fetchone()
        return {'change': 'extraction', 'object_sha256': ev['id'], 'filename': obj and obj['filename'],
                'pages': c.execute('SELECT count(*) FROM object_pages WHERE object_sha=?', (ev['id'],)).fetchone()[0],
                'records': [r[0] for r in c.execute('SELECT id FROM active_records WHERE object_sha=?', (ev['id'],))]}
    return {'change': ev['entity'], 'id': ev['id'], 'detail': ev['detail']}


def _insight(row, shadow: bool = False) -> dict:
    p = json.loads(row['payload_json'])
    out = {'id': row['id'], 'question_id': row['question_id'], 'created_at': row['created_at'],
           **{k: _cut(p[k], 600) for k in ('topic', 'what_changed', 'next_step') if p.get(k)},
           'evidence_ids': p.get('evidence_ids', [])}
    if shadow:
        out['state'] = 'shadow: recorded by an earlier background run, never shown (counts as already produced)'
    else:
        out['state'] = f'{row["state"]} ({MEANING.get(row["state"], row["state"])})'
        if row['delivered_at']:
            out['delivered_at'] = row['delivered_at']
    return out


def _sources(c, now: datetime) -> list[dict]:
    out = []
    for r in c.execute('SELECT id, label, state, last_success_at, latest_sample_at FROM sources '
                       "WHERE id != 'user' ORDER BY id"):
        row = dict(r)
        if r['latest_sample_at']:
            row['days_since_latest_sample'] = round((now - datetime.fromisoformat(r['latest_sample_at']))
                                                    .total_seconds() / 86400, 1)
        out.append(row)
    return out


def packet(store: Store, job: dict, now: datetime | None = None) -> str:
    """The fixed context of one investigation, assembled deterministically and bounded: the question and its history,
    what the user was already told, preferences, source health and the concrete changes that triggered the job, with
    before/after numbers. The model retrieves anything else with its read-only tools."""
    now = now or _now()
    p = json.loads(job['payload_json'])
    prefs = store.preferences()
    store.pending_insights()  # closes pending insights whose evidence changed, so "already surfaced" is current
    parts = [f'# Background investigation packet\nNow: {now.isoformat(timespec="seconds")}. Job: {job["type"]}. '
             'Assembled from the local store just now; each section is complete unless it says it was truncated.',
             JOBS[job['type']], SCHEMA]
    with store.connect() as c:
        last = c.execute("SELECT max(created_at) FROM insights WHERE state IN('pending','delivered')").fetchone()[0]
        hours = 168 if prefs['proactivity'] == 'quiet' else 72
        spent = bool(last) and now - datetime.fromisoformat(last) < timedelta(hours=hours)
        attention = {'proactivity': prefs['proactivity'],
                     'meaning': {'normal': 'the user accepts occasional proactive messages',
                                 'quiet': 'the user asked to be interrupted less: surface only a direct answer to their '
                                          'own question that the new evidence itself provides, a correction of '
                                          'something they were shown, or a measurement gap tied to a decision the '
                                          'records date within about two weeks; everything else is silence',
                                 'off': 'the user turned proactive messages off'}.get(prefs['proactivity']),
                     'last_insight_queued_or_shown_at': last,
                     'attention_budget': f'at most one proactive message per {hours} h (enforced by the gate)',
                     'budget_available_now': not spent}
        if job['type'] == 'revisit':
            qid = p['question_id']
            q = _record(c, qid) or {'id': qid, 'text': '(missing)'}
            parts.append('## Question\n' + dump(q))
            analyses = [store._decorate(c, r) for r in c.execute(
                "SELECT * FROM active_records WHERE kind='analysis' AND (json_extract(payload_json, '$.question_id')=? "
                'OR id IN (SELECT record_id FROM evidence_links WHERE evidence_id=?)) ORDER BY occurred_at DESC',
                (qid, qid))]
            parts.append(_section('Prior analyses of this question (newest first)', [
                {'id': a['id'], 'occurred_at': a['occurred_at'], 'text': _cut(a['text'], 2000),
                 'revisit_when': a['payload'].get('revisit_when'),
                 'evidence': a.get('evidence')} for a in analyses], 12_000, 'None: this question was never analysed.'))
            told = [_insight(r) for r in c.execute('SELECT * FROM insights WHERE question_id=? ORDER BY created_at DESC',
                                                   (qid,))]
            told += [_insight(r, True) for r in c.execute(
                'SELECT * FROM shadow_insights WHERE question_id=? ORDER BY created_at DESC', (qid,))]
            parts.append(_section('Already surfaced on this question', told, 8_000,
                                  'Nothing has been surfaced on this question.'))
            others = [_insight(r) for r in c.execute(
                'SELECT * FROM insights WHERE question_id IS NOT ? ORDER BY created_at DESC LIMIT 5', (qid,))]
            parts.append(_section('Most recent insights on other topics', others, 3_000, 'None.'))
        else:
            qs = open_questions(store)
            parts.append(_section('Open questions', [{'id': q['id'], 'text': _cut(q['text'], 400),
                                                      'asked_at': q['created_at'],
                                                      **{k: q['payload'][k] for k in ('outcome', 'watch_terms',
                                                                                     'watch_metrics')
                                                         if k in q['payload']}} for q in qs], 8_000, 'None.'))
            goals = [_record(c, r[0]) for r in c.execute(
                "SELECT id FROM active_records WHERE kind='routine' ORDER BY occurred_at DESC LIMIT 20")]
            parts.append(_section('Current routines and goals', goals, 5_000, 'None recorded.'))
            told = [_insight(r) for r in c.execute('SELECT * FROM insights ORDER BY created_at DESC LIMIT 15')]
            told += [_insight(r, True) for r in c.execute(
                'SELECT * FROM shadow_insights ORDER BY created_at DESC LIMIT 15')]
            parts.append(_section('Already surfaced (all topics, newest first)', told, 8_000, 'Nothing yet.'))
            parts.append(_section('Recent analyses (newest first)', [
                {'id': a['id'], 'occurred_at': a['occurred_at'], 'question_id': a['payload'].get('question_id'),
                 'text': _cut(a['text'], 800)} for a in (store._decorate(c, r) for r in c.execute(
                     "SELECT * FROM active_records WHERE kind='analysis' ORDER BY occurred_at DESC LIMIT 10"))],
                6_000, 'None.'))
            p = {'evidence': [{'seq': r['seq'], 'entity': r['entity'], 'id': r['entity_id'],
                               'detail': json.loads(r['detail']) if r['detail'].startswith('{') else r['detail']}
                              for r in c.execute("SELECT * FROM changes WHERE seq>? AND seq<=? AND entity IN "
                                                 "('record','observations','extraction') ORDER BY seq DESC LIMIT 50",
                                                 (p['since_seq'], p['through_seq']))]}
        parts.append('## User preferences and attention\n' + dump(attention))
        parts.append(_section('Source status (sync health, not health data)', _sources(c, now), 5_000,
                              'No device or vendor sources are connected.'))
        parts.append(_section('Passive data available (observation catalog)', [dict(r) for r in c.execute(
            'SELECT source_id, metric, unit, n, first_at, last_at FROM observation_catalog ORDER BY n DESC')],
            4_000, 'No observations stored.'))
        used = sum(map(len, parts))
        parts.append(_section('What changed since the last look (the trigger of this job)',
                              [_trigger(c, ev) for ev in p['evidence']], max(4_000, PACKET_CHARS - used),
                              'No change descriptors.'))
    if job['type'] == 'revisit':
        parts.append(f'If you surface, set question_id to {p["question_id"]}.')
    return '\n\n'.join(parts)


def execute(store: Store, cfg: Config, job: dict, owner: str, config_path: str,
            scripted=None, traces: list | None = None) -> dict:
    """`traces` (callers in code only, e.g. the eval harness) receives each model call's candidate or error with its
    tool trace; run_once's return value, which the CLI prints, never carries them."""
    backend = cfg.model_backend if cfg.model_enabled else 'none'
    call_id = 'mc_' + uuid.uuid4().hex
    started = utcnow()
    since = store.generation()  # evidence that changes after this point cannot have been seen by this run
    prompt_sha = hashlib.sha256(model.background_prompt().encode()).hexdigest()
    task = None
    try:
        if backend == 'none':
            raise model.ModelError('model_disabled')
        if calls_today(store) >= cfg.daily_call_cap:
            raise model.ModelError('budget_exhausted')
        with store.transaction() as c:
            c.execute('INSERT INTO model_calls VALUES(?,?,?,?,?,?,NULL,?,NULL,NULL)',
                      (call_id, job['id'], backend, cfg.model_id or backend, prompt_sha, started, 'running'))
        tools = Tools(ToolContext(store=store, profile='readonly')) if backend == 'router' else None
        task = packet(store, job)
        result = model.investigate(backend, cfg.model_id, task, config_path=config_path,
                                   scripted=scripted, file_auth=cfg.codex_file_auth, tools=tools,
                                   reasoning_effort=cfg.model_reasoning_effort)
    except model.ModelError as e:
        if traces is not None:
            traces.append({'job': job['dedupe_key'], 'task': task, 'error': e.code, 'trace': e.trace})
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
    if traces is not None:
        traces.append({'job': job['dedupe_key'], 'task': task, 'candidate': result.candidate, 'trace': result.trace})

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
                                   fence=finish, since_seq=since), True
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


def run_once(cfg: Config, config_path: str | None = None, scripted=None, traces: list | None = None) -> dict:
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
        results = [execute(store, cfg, j, owner, config_path or str(cfg.source or ''), scripted, traces)
                   for j in due]
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
