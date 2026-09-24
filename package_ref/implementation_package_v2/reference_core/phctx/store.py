from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo


class StoreError(Exception):
    """Expected, bounded error safe to return without raw data or credentials."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def instant(value: str) -> str:
    try:
        x = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if x.tzinfo is None:
            raise ValueError('offset missing')
        return x.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, AttributeError) as e:
        raise StoreError('invalid_time', 'Use ISO 8601 with an explicit UTC offset.') from e


def nonempty(x: Any, label: str, maximum: int = 20000) -> str:
    if not isinstance(x, str) or not x.strip() or len(x) > maximum:
        raise StoreError('invalid_argument', f'{label} must be a bounded nonempty string.')
    return x


def check_tz(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (KeyError, ValueError, TypeError) as e:
        raise StoreError('invalid_timezone', 'Use an IANA timezone.') from e


def positive_limit(n: int, maximum: int = 1000) -> int:
    if type(n) is not int or not 1 <= n <= maximum:
        raise StoreError('invalid_limit', f'limit must be an integer from 1 to {maximum}.')
    return n


class Store:
    """SQLite + content-addressed immutable objects; one writer transaction per mutation.

    This foundation deliberately does not install daemons, contact vendors, or call a model.
    Only trusted importers may register sources; model-facing tools cannot change policy.
    """
    KINDS = {'event', 'routine', 'question', 'analysis', 'note', 'attachment', 'preference'}
    PUBLIC_TABLES = {'records', 'active_records', 'observations', 'active_observations',
                     'sources', 'objects', 'evidence_links'}
    SAFE_FUNCTIONS = {'abs', 'avg', 'coalesce', 'count', 'date', 'datetime', 'ifnull', 'julianday',
                      'length', 'like', 'lower', 'max', 'min', 'nullif', 'replace', 'round',
                      'strftime', 'substr', 'sum', 'total', 'trim', 'upper', 'json_extract',
                      'json_type', 'json_valid', 'row_number', 'lag', 'lead'}

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.root / 'objects'
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / 'context.sqlite3'
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(Path(__file__).with_name('schema.sql').read_text())
            if c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != '1':
                raise StoreError('schema_mismatch', 'Unsupported database schema; do not downgrade.')
            c.execute('INSERT OR IGNORE INTO sources(id,label,policy,state) VALUES(?,?,?,?)',
                      ('user', 'User-provided context', 'durable', 'ready'))
            c.execute('INSERT OR IGNORE INTO sources(id,label,policy,state) VALUES(?,?,?,?)',
                      ('oura', 'Oura official MCP (external, no local mirror)', 'ephemeral', 'not_connected'))
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=15000')
        c.execute('PRAGMA synchronous=FULL')
        try:
            yield c
        finally:
            c.close()

    def register_source(self, source_id: str, label: str, policy: str = 'durable') -> None:
        """Operator/importer only. Not a model-facing operation."""
        nonempty(source_id, 'source_id', 200)
        if policy not in {'durable', 'ephemeral', 'blocked'}:
            raise StoreError('invalid_policy', 'Unknown source policy.')
        if 'oura' in source_id.casefold() and not source_id.startswith('synthetic:') and policy == 'durable':
            raise StoreError('source_restricted', 'Real Oura mirroring is not enabled in this implementation.')
        with self.connect() as c:
            c.execute('INSERT INTO sources(id,label,policy) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING',
                      (source_id, nonempty(label, 'label', 200), policy))

    def _source(self, c: sqlite3.Connection, source_id: str) -> None:
        row = c.execute('SELECT policy FROM sources WHERE id=?', (source_id,)).fetchone()
        if not row or row['policy'] != 'durable':
            raise StoreError('source_restricted', 'This source cannot be persisted by this store.')

    def _mutate(self, request_id: str, body: dict, fn: Callable[[sqlite3.Connection], dict]) -> dict:
        nonempty(request_id, 'request_id', 200)
        digest = hashlib.sha256(dump(body).encode()).hexdigest()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                old = c.execute('SELECT * FROM receipts WHERE request_id=?', (request_id,)).fetchone()
                if old:
                    if old['request_hash'] != digest:
                        raise StoreError('idempotency_conflict', 'Request key already has different content.')
                    c.rollback()
                    return json.loads(old['result_json'])
                value = fn(c)
                value.update({'request_id': request_id, 'status': 'committed', 'request_hash': digest})
                c.execute('INSERT INTO receipts VALUES(?,?,?,?)', (request_id, digest, dump(value), utcnow()))
                c.commit()
                return value
            except BaseException:
                c.rollback()
                raise

    def put_record(self, *, request_id: str, kind: str, text: str, occurred_at: str,
                   timezone_name: str = 'America/Chicago', payload: dict | None = None,
                   source_id: str = 'user', source_key: str | None = None,
                   object_sha: str | None = None, supersedes: str | None = None,
                   evidence_ids: list[str] | None = None) -> dict:
        if kind not in self.KINDS:
            raise StoreError('invalid_kind', 'Unsupported context kind.')
        nonempty(text, 'text')
        check_tz(timezone_name)
        at = instant(occurred_at)
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or len(dump(payload)) > 100000:
            raise StoreError('invalid_payload', 'payload must be an object under the size cap.')
        evidence_ids = sorted(set(evidence_ids or []))
        body = dict(op='record', kind=kind, text=text, at=at, tz=timezone_name, payload=payload,
                    source=source_id, source_key=source_key, object=object_sha,
                    supersedes=supersedes, evidence=evidence_ids)

        def write(c: sqlite3.Connection) -> dict:
            self._source(c, source_id)
            for ref in evidence_ids:
                evidence = c.execute('SELECT source_id FROM records WHERE id=?', (ref,)).fetchone()
                if not evidence:
                    raise StoreError('missing_evidence', 'Evidence record does not exist.')
                self._source(c, evidence['source_id'])
            if supersedes:
                old = c.execute('SELECT * FROM active_records WHERE id=?', (supersedes,)).fetchone()
                if not old or old['kind'] != kind or old['source_id'] != source_id:
                    raise StoreError('revision_conflict', 'Only an active same-kind/source record can be replaced.')
            rid = 'rec_' + uuid.uuid4().hex
            try:
                c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (rid, kind, at, timezone_name, text, dump(payload), source_id,
                           source_key, object_sha, supersedes, utcnow()))
            except sqlite3.IntegrityError as e:
                raise StoreError('record_conflict', 'Record key, revision, or object reference is invalid.') from e
            c.executemany('INSERT INTO evidence_links VALUES(?,?)', [(rid, x) for x in evidence_ids])
            return {'record_id': rid, 'evidence_ids': evidence_ids}
        return self._mutate(request_id, body, write)

    def put_attachment_bytes(self, *, request_id: str, data: bytes, filename: str, mime: str,
                             text: str, occurred_at: str, timezone_name: str = 'America/Chicago') -> dict:
        """Called only after a trusted file transport supplies actual bytes, not a filename claim."""
        if not isinstance(data, bytes) or not data or len(data) > 25 * 1024 * 1024:
            raise StoreError('file_size', 'Attachment must be nonempty and at most 25 MiB.')
        filename = nonempty(filename, 'filename', 255)
        if Path(filename).name != filename or '\\' in filename or any(ord(ch) < 32 for ch in filename):
            raise StoreError('invalid_filename', 'A display filename, not a path, is required.')
        nonempty(mime, 'mime', 100)
        nonempty(text, 'text')
        at = instant(occurred_at)
        check_tz(timezone_name)
        sha = hashlib.sha256(data).hexdigest()
        blob = self.blobs / sha
        # Concurrent identical writes are harmless. No DB reference until bytes are fully fsynced.
        if not blob.exists():
            fd, temp = tempfile.mkstemp(prefix='.upload-', dir=self.blobs)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(temp, 0o600)
                os.replace(temp, blob)
                dfd = os.open(self.blobs, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        if hashlib.sha256(blob.read_bytes()).hexdigest() != sha:
            raise StoreError('object_corrupt', 'Object checksum failed; no capture acknowledgment.')
        body = dict(op='attachment', sha=sha, filename=filename, mime=mime, text=text, at=at, tz=timezone_name)

        def write(c: sqlite3.Connection) -> dict:
            c.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?,?,?)', (sha, len(data), mime, filename, utcnow()))
            rid = 'rec_' + uuid.uuid4().hex
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (rid, 'attachment', at, timezone_name, text,
                       dump({'filename': filename, 'mime': mime, 'extraction_status': 'pending'}),
                       'user', None, sha, None, utcnow()))
            return {'record_id': rid, 'object_sha256': sha, 'size': len(data), 'extraction_status': 'pending'}
        return self._mutate(request_id, body, write)

    def read_object(self, sha: str) -> bytes:
        if not isinstance(sha, str) or len(sha) != 64 or any(x not in '0123456789abcdef' for x in sha):
            raise StoreError('invalid_object_id', 'Use a returned object SHA-256.')
        with self.connect() as c:
            row = c.execute('SELECT size FROM objects WHERE sha256=?', (sha,)).fetchone()
        if not row:
            raise StoreError('not_found', 'Object is not registered.')
        try:
            data = (self.blobs / sha).read_bytes()
        except OSError as e:
            raise StoreError('object_missing', 'Registered original is unavailable.') from e
        if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != sha:
            raise StoreError('object_corrupt', 'Stored original failed verification.')
        return data

    def get_records(self, ids: list[str]) -> dict:
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise StoreError('invalid_ids', 'Provide 1–100 returned record IDs.')
        with self.connect() as c:
            rows = [dict(r) for r in c.execute(f'SELECT * FROM records WHERE id IN ({",".join("?" for _ in ids)})', ids)]
        present = {r['id'] for r in rows}
        return {'records': rows, 'missing_ids': [x for x in ids if x not in present]}

    def search(self, *, query: str = '', kind: str | None = None, limit: int = 50,
               after_id: str = '') -> dict:
        positive_limit(limit, 500)
        if not isinstance(query, str) or len(query) > 500:
            raise StoreError('invalid_query', 'query must be a bounded string.')
        # Unicode substring fallback is intentional. Production adds trigram FTS after CJK tests.
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where = ['id>?', "(text LIKE ? ESCAPE '\\' OR payload_json LIKE ? ESCAPE '\\')"]
        args: list[Any] = [after_id, f'%{escaped}%', f'%{escaped}%']
        if kind is not None:
            where.append('kind=?')
            args.append(kind)
        with self.connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM active_records WHERE ' + ' AND '.join(where)
                                              + ' ORDER BY id LIMIT ?', [*args, limit + 1])]
        more = len(rows) > limit
        rows = rows[:limit]
        return {'records': rows, 'has_more': more, 'next_after_id': rows[-1]['id'] if more else None,
                'search_mode': 'unicode_substring', 'snapshot_stable': False}

    def query_readonly(self, sql: str, parameters: list | None = None, limit: int = 500) -> dict:
        nonempty(sql, 'sql', 20000)
        positive_limit(limit)
        # Read-only connection AND query_only AND authorizer. String-prefix checks are insufficient.
        c = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)
        c.execute('PRAGMA query_only=ON')
        c.enable_load_extension(False)
        c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_048_576)
        c.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000)
        c.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
        c.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 50)
        deadline = time.monotonic() + 2.0
        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

        def authorize(action: int, a: str | None, b: str | None, db: str | None, trigger: str | None) -> int:
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            # SQLite reports db=None,column='' for cardinality-only table reads.
            # Table allowlist still applies; ATTACH/CREATE and secret-table reads remain denied.
            if action == sqlite3.SQLITE_READ and a in self.PUBLIC_TABLES and (
                    db == 'main' or (db is None and b == '')):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (b or '').lower() in self.SAFE_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        c.set_authorizer(authorize)
        try:
            cur = c.execute(sql, parameters or [])
            columns = [x[0] for x in cur.description or []]
            rows, encoded_bytes, truncated = [], len(dump(columns).encode()), False
            for index, row in enumerate(cur):
                if index == limit:
                    truncated = True
                    break
                encoded_bytes += len(dump(list(row)).encode())
                if encoded_bytes > 240_000:
                    raise StoreError('result_too_large', 'Narrow columns or page the requested range.')
                rows.append(list(row))
            result = {'columns': columns, 'rows': rows,
                      'truncated': truncated, 'max_rows': limit,
                      'query_sha256': hashlib.sha256((sql + dump(parameters or [])).encode()).hexdigest()}
            if len(dump(result).encode()) > 256000:
                raise StoreError('result_too_large', 'Narrow columns or page the requested range.')
            return result
        except sqlite3.Error as e:
            raise StoreError('query_rejected', 'Read-only query rejected, timed out, or invalid.') from e
        finally:
            c.close()

    def ingest_batch(self, *, request_id: str, source_id: str, samples: list[dict],
                     deleted_ids: list[str], cursor: str, coverage: dict | None = None) -> dict:
        """Trusted importer-only. Upserts and deletes commit with the source cursor."""
        if not isinstance(samples, list) or len(samples) > 5000 or len(deleted_ids) > 5000:
            raise StoreError('batch_size', 'Use pages of at most 5000 samples/deletions.')
        checked = []
        seen = set()
        for sample in samples:
            s = dict(sample)
            sid = nonempty(s.get('native_id'), 'native_id', 200)
            if sid in seen:
                raise StoreError('duplicate_in_batch', 'Batch native IDs must be unique.')
            seen.add(sid)
            nonempty(s.get('metric'), 'metric', 200)
            start, end = instant(s['start_at']), instant(s.get('end_at', s['start_at']))
            if end < start:
                raise StoreError('invalid_interval', 'end_at precedes start_at.')
            tz = s.get('timezone', 'America/Chicago')
            check_tz(tz)
            val = s.get('value_num')
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)):
                raise StoreError('invalid_value', 'Numeric measurement must be finite and cannot be bool.')
            checked.append((s, start, end, tz))
        if seen.intersection(deleted_ids):
            raise StoreError('ambiguous_batch', 'A sample cannot be upserted and deleted in the same page.')
        body = dict(op='batch', source_id=source_id, samples=samples, deleted=deleted_ids,
                    cursor=cursor, coverage=coverage or {})

        def write(c: sqlite3.Connection) -> dict:
            self._source(c, source_id)
            now = utcnow()
            for s, start, end, tz in checked:
                oid = 'obs_' + hashlib.sha256((source_id + '\0' + s['native_id']).encode()).hexdigest()
                c.execute('''INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id,native_id) DO UPDATE SET
                    metric=excluded.metric,start_at=excluded.start_at,end_at=excluded.end_at,
                    timezone=excluded.timezone,value_num=excluded.value_num,value_text=excluded.value_text,
                    unit=excluded.unit,raw_json=excluded.raw_json,deleted=0,updated_at=excluded.updated_at''',
                    (oid, source_id, s['native_id'], s['metric'], start, end, tz, s.get('value_num'),
                     s.get('value_text'), s.get('unit'), dump(s), 0, now))
            for sid in deleted_ids:
                nonempty(sid, 'deleted_id', 200)
                # A deletion-only object has no measurement body and stays outside active data.
                oid = 'obs_' + hashlib.sha256((source_id + '\0' + sid).encode()).hexdigest()
                c.execute('''INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id,native_id) DO UPDATE SET deleted=1,updated_at=excluded.updated_at''',
                    (oid, source_id, sid, 'tombstone', now, now, 'UTC', None, None, None, '{}', 1, now))
            latest = c.execute('SELECT max(end_at) FROM observations WHERE source_id=? AND deleted=0',
                               (source_id,)).fetchone()[0]
            c.execute('''UPDATE sources SET state='ready',last_attempt_at=?,last_success_at=?,
                      latest_sample_at=?,cursor=?,coverage_json=? WHERE id=?''',
                      (now, now, latest, cursor, dump(coverage or {}), source_id))
            return {'upserted': len(samples), 'deleted': len(deleted_ids), 'cursor': cursor,
                    'source_id': source_id}
        return self._mutate(request_id, body, write)

    def source_status(self) -> dict:
        with self.connect() as c:
            return {'checked_at': utcnow(), 'sources': [dict(r) for r in c.execute('SELECT * FROM sources ORDER BY id')],
                    'note': 'latest_sample_at is not complete coverage; permission-denied can look like empty data.'}

    def set_preference(self, *, request_id: str, key: str, value: Any) -> dict:
        allowed = {'proactivity': {'normal', 'quiet', 'off'}, 'timezone': None}
        if key not in allowed:
            raise StoreError('invalid_preference', 'Supported preferences: proactivity, timezone.')
        if key == 'timezone':
            check_tz(value)
        elif value not in allowed[key]:
            raise StoreError('invalid_preference', 'Unsupported preference value.')
        def write(c: sqlite3.Connection) -> dict:
            c.execute('INSERT INTO preferences VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET '
                      'value_json=excluded.value_json,updated_at=excluded.updated_at', (key, dump(value), utcnow()))
            return {'key': key, 'value': value}
        return self._mutate(request_id, dict(op='preference', key=key, value=value), write)

    def preferences(self) -> dict:
        out = {'proactivity': 'normal', 'timezone': 'America/Chicago'}
        with self.connect() as c:
            out.update({r['key']: json.loads(r['value_json']) for r in c.execute('SELECT * FROM preferences')})
        return out

    def queue_insight(self, *, request_id: str, candidate: dict) -> dict:
        """Local background writer only. Silence is an output, not a persisted health conclusion."""
        if candidate.get('decision') == 'silence':
            return {'status': 'silent', 'queued': False}
        if candidate.get('decision') != 'surface':
            raise StoreError('invalid_candidate', 'Use surface or silence.')
        for key in ('why_now', 'what_changed', 'unknowns', 'next_step'):
            nonempty(candidate.get(key), key, 5000)
        qid = candidate.get('question_id')
        evidence = candidate.get('evidence_ids', [])
        if not evidence:
            raise StoreError('missing_evidence', 'A proactive proposal needs actual stored evidence.')
        fingerprint = hashlib.sha256(dump({'question_id': qid, 'evidence_ids': sorted(set(evidence)),
                                         'topic': candidate.get('topic', 'unspecified')}).encode()).hexdigest()
        def write(c: sqlite3.Connection) -> dict:
            if qid and not c.execute("SELECT 1 FROM active_records WHERE id=? AND kind='question'", (qid,)).fetchone():
                raise StoreError('missing_question', 'Question reference is not an active stored question.')
            for rid in evidence:
                if not c.execute('SELECT 1 FROM active_records WHERE id=?', (rid,)).fetchone():
                    raise StoreError('missing_evidence', 'Proactive evidence is missing or superseded.')
            old = c.execute('SELECT id FROM insights WHERE fingerprint=?', (fingerprint,)).fetchone()
            if old:
                return {'queued': False, 'reason': 'duplicate', 'insight_id': old[0]}
            prefs = {r['key']: json.loads(r['value_json']) for r in c.execute('SELECT * FROM preferences')}
            mode = prefs.get('proactivity', 'normal')
            if mode == 'off':
                return {'queued': False, 'reason': 'preference_off'}
            # Attention budget is policy, not a medical-significance threshold.
            hours = 168 if mode == 'quiet' else 72
            since = datetime.fromtimestamp(time.time() - hours * 3600, timezone.utc).isoformat(timespec='microseconds')
            if c.execute("SELECT 1 FROM insights WHERE created_at>? AND state!='dismissed' LIMIT 1", (since,)).fetchone():
                return {'queued': False, 'reason': 'attention_budget'}
            iid = 'ins_' + uuid.uuid4().hex
            c.execute('INSERT INTO insights VALUES(?,?,?,?,?,?,?)', (iid, fingerprint, qid, dump(candidate), 'pending', utcnow(), None))
            return {'queued': True, 'insight_id': iid}
        return self._mutate(request_id, dict(op='insight', candidate=candidate), write)

    def pending_insights(self) -> dict:
        if self.preferences()['proactivity'] == 'off':
            return {'insights': [], 'suppressed': True}
        with self.connect() as c:
            out = []
            for r in c.execute("SELECT * FROM insights WHERE state='pending' ORDER BY created_at LIMIT 10"):
                row = dict(r)
                payload = json.loads(row['payload_json'])
                qid = row['question_id']
                question_valid = not qid or c.execute(
                    "SELECT 1 FROM active_records WHERE id=? AND kind='question'", (qid,)).fetchone()
                valid = question_valid and all(c.execute('SELECT 1 FROM active_records WHERE id=?', (rid,)).fetchone()
                            for rid in payload.get('evidence_ids', []))
                if valid:
                    out.append(row)
            return {'insights': out, 'suppressed': False}

    def ack_insight(self, *, request_id: str, insight_id: str, disposition: str = 'delivered') -> dict:
        if disposition not in {'delivered', 'dismissed'}:
            raise StoreError('invalid_disposition', 'Use delivered or dismissed.')
        def write(c: sqlite3.Connection) -> dict:
            row = c.execute('SELECT state FROM insights WHERE id=?', (insight_id,)).fetchone()
            if not row:
                raise StoreError('not_found', 'Insight not found.')
            c.execute('UPDATE insights SET state=?,delivered_at=? WHERE id=?',
                      (disposition, utcnow(), insight_id))
            return {'insight_id': insight_id, 'disposition': disposition}
        return self._mutate(request_id, dict(op='ack', id=insight_id, disposition=disposition), write)

    def backup(self, destination: str | Path) -> dict:
        """Online SQLite snapshot, then every referenced immutable object. Never copy a live WAL DB."""
        dest = Path(destination).expanduser().resolve()
        if dest.exists() or dest == self.root or self.root in dest.parents:
            raise StoreError('invalid_backup_path', 'Use a new directory outside the live data root.')
        dest.mkdir(parents=True, mode=0o700)
        try:
            with self.connect() as src:
                target = sqlite3.connect(dest / 'context.sqlite3')
                try:
                    src.backup(target)
                    refs = target.execute('SELECT sha256 FROM objects').fetchall()
                    if (target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                            or target.execute('PRAGMA foreign_key_check').fetchone() is not None):
                        raise StoreError('backup_invalid', 'SQLite integrity check failed.')
                finally:
                    target.close()
            (dest / 'objects').mkdir(mode=0o700)
            for (sha,) in refs:
                data = self.read_object(sha)
                path = dest / 'objects' / sha
                path.write_bytes(data)
                os.chmod(path, 0o600)
            os.chmod(dest / 'context.sqlite3', 0o600)
            manifest = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in dest.rglob('*') if p.is_file()}
            (dest / 'backup_manifest.json').write_text(dump({'created_at': utcnow(), 'sha256': manifest}))
            return {'status': 'snapshot_complete', 'path': str(dest), 'objects': len(refs), 'cloud_synced': False}
        except BaseException:
            # Preserve a failed snapshot for diagnosis. It has no valid completion manifest.
            raise

    @classmethod
    def restore(cls, snapshot: str | Path, destination: str | Path) -> 'Store':
        """Verify an offline snapshot and restore ONLY into a new root; never overwrite live data."""
        src, dest = Path(snapshot).resolve(), Path(destination).resolve()
        if dest.exists() or src == dest or src in dest.parents:
            raise StoreError('invalid_restore_path', 'Restore into a new directory outside the snapshot.')
        try:
            manifest = json.loads((src / 'backup_manifest.json').read_text())['sha256']
        except (OSError, ValueError, KeyError) as e:
            raise StoreError('backup_invalid', 'Snapshot has no valid completion manifest.') from e
        if 'context.sqlite3' not in manifest:
            raise StoreError('backup_invalid', 'Snapshot has no database.')
        for name, sha in manifest.items():
            p = src / name
            allowed = name == 'context.sqlite3' or (name.startswith('objects/') and len(name) == 72
                      and all(ch in '0123456789abcdef' for ch in name[8:]))
            if not allowed or p.is_symlink() or not p.is_file() or src not in p.resolve().parents:
                raise StoreError('backup_invalid', 'Snapshot contains an invalid path.')
            if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise StoreError('backup_invalid', 'Snapshot checksum failed.')
        db = sqlite3.connect((src / 'context.sqlite3').as_uri() + '?mode=ro', uri=True)
        try:
            if (db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                    or db.execute('PRAGMA foreign_key_check').fetchone() is not None):
                raise StoreError('backup_invalid', 'Snapshot integrity check failed.')
            for (sha,) in db.execute('SELECT sha256 FROM objects'):
                if 'objects/' + sha not in manifest:
                    raise StoreError('backup_invalid', 'Snapshot lacks a referenced original.')
        finally:
            db.close()
        dest.mkdir(parents=True, mode=0o700)
        for name in manifest:
            p = dest / name
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(src / name, p)
            os.chmod(p, 0o600)
        return cls(dest)

    def bootstrap(self) -> dict:
        """Starting index, not a final evidence filter; the model may search/read/query freely."""
        with self.connect() as c:
            context = [dict(r) for r in c.execute(
                "SELECT * FROM active_records WHERE kind IN ('routine','question','note') "
                "ORDER BY occurred_at DESC LIMIT 20")]
        return {'preferences': self.preferences(), 'source_status': self.source_status(),
                'context_index': context, 'pending': self.pending_insights(),
                'index_complete': False, 'continuation': 'Use search, get_records, or query_readonly for broader evidence.',
                'boundaries': ['No record without a committed receipt.',
                               'No original attachment without its bytes and verified checksum.',
                               'Oura external MCP content is not authorized for local persistence.']}
