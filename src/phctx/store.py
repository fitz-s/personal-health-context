from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from . import migrations


class StoreError(Exception):
    """Expected, bounded error safe to return without raw data or credentials."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def utc_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='microseconds')


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


RESTRICTED_VENDORS = re.compile(r'oura', re.IGNORECASE)


def restricted_origin(*labels: Any) -> bool:
    """True when a sample's provenance names a vendor whose data may not be persisted (Oura).

    A mirrored sample keeps its origin: an Oura value written into Apple Health is still Oura data.
    """
    return any(isinstance(x, str) and RESTRICTED_VENDORS.search(x) for x in labels)


MAGIC = [(b'%PDF-', 'application/pdf'), (b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg'),
         (b'GIF87a', 'image/gif'), (b'GIF89a', 'image/gif'), (b'PK\x03\x04', 'application/zip')]


def sniff_mime(data: bytes) -> str:
    """Content-based type; the declared MIME/filename is only a hint."""
    for magic, mime in MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[4:8] == b'ftyp' and data[8:12] in {b'heic', b'heix', b'mif1', b'msf1', b'hevc'}:
        return 'image/heic'
    try:
        data[:65536].decode('utf-8')
        return 'text/plain'
    except UnicodeDecodeError:
        return 'application/octet-stream'


# Export bookkeeping that differs between two writes of one sample by the source app; everything else must match.
REPEAT_IGNORED = ('native_id', 'metadata.creation_date', 'metadata.import', 'metadata.source_version')


def repeat_signature(raw_json: str) -> str:
    """Canonical JSON of a stored export sample (its raw_json) without REPEAT_IGNORED fields: two rows with the same
    signature are one source record written twice. Timezone, device, every other metadata key and child element count."""
    raw = json.loads(raw_json or '{}')
    for path in REPEAT_IGNORED:
        head, _, tail = path.partition('.')
        if not tail:
            raw.pop(head, None)
        elif isinstance(raw.get(head), dict):
            raw[head].pop(tail, None)
    return dump(raw)


def mark_repeats(c: sqlite3.Connection, source_id: str, key: str) -> None:
    """Within one export origin_key group, a row whose repeat_signature equals an earlier (smaller id) active row's
    repeats it: repeat_of = the first such row. Recomputed for the whole (small) group on any change."""
    # INDEXED BY: the planner otherwise walks obs_source_end — every row of the source — for a group of a few rows.
    rows = c.execute('SELECT id, raw_json FROM observations INDEXED BY obs_origin WHERE origin_key=? AND source_id=? '
                     'AND deleted=0 ORDER BY id', (key, source_id)).fetchall()
    first: dict[str, str] = {}
    for oid, raw in rows:
        original = first.setdefault(repeat_signature(raw), oid)
        c.execute('UPDATE observations SET repeat_of=? WHERE id=?', (None if original == oid else original, oid))
    c.execute('UPDATE observations INDEXED BY obs_origin SET repeat_of=NULL WHERE origin_key=? AND source_id=? '
              'AND deleted=1', (key, source_id))


def ref_kind(ref: str) -> str:
    if not isinstance(ref, str):
        raise StoreError('missing_evidence', 'Evidence IDs are strings returned by the store.')
    if ref.startswith('rec_'):
        return 'record'
    if ref.startswith('obs_'):
        return 'observation'
    if re.fullmatch(r'obj:[0-9a-f]{64}(#p[1-9][0-9]{0,4})?', ref):
        return 'object'
    raise StoreError('missing_evidence', 'Unknown evidence ID; use rec_*, obs_*, or obj:<sha256>[#p<page>] returned by the store.')


class Store:
    """SQLite + content-addressed immutable objects; one writer transaction per mutation.

    Model-facing wrappers live in tools.py. Source registration, device pairing, batch import,
    backup/restore and policy are operator/importer operations, never model tools.
    """
    KINDS = {'event', 'routine', 'question', 'analysis', 'note', 'attachment', 'preference'}
    CLOSED_QUESTION = {'closed', 'superseded', 'answered_final'}
    # Tables whose content an unfinished import changes (sources carries latest_sample_at / coverage).
    OBSERVATION_TABLES = {'observations', 'active_observations', 'canonical_observations', 'canonical_observations_raw',
                          'observation_catalog', 'supersessions', 'sources'}
    PUBLIC_TABLES = {'records', 'active_records', 'observations', 'active_observations', 'canonical_observations',
                     'canonical_observations_raw',
                     'sources', 'objects', 'evidence_links', 'evidence_refs', 'object_pages', 'extractions',
                     'observation_catalog', 'supersessions'}
    SAFE_FUNCTIONS = {'abs', 'avg', 'coalesce', 'count', 'date', 'datetime', 'ifnull', 'julianday',
                      'length', 'like', 'lower', 'max', 'min', 'nullif', 'replace', 'round',
                      'strftime', 'substr', 'sum', 'total', 'trim', 'upper', 'json_extract',
                      'json_type', 'json_valid', 'row_number', 'lag', 'lead', 'instr', 'iif',
                      'unixepoch', 'group_concat', 'printf', 'cast', 'time'}
    PROFILES = {'synthetic', 'production'}
    INSIGHT_TTL_DAYS = 14

    def __init__(self, root: str | Path, profile: str | None = None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        marker = self.root / 'PROFILE'
        if marker.exists():
            self.profile = marker.read_text().strip()
            if profile is not None and profile != self.profile:
                raise StoreError('profile_mismatch', f'Data root is bound to profile {self.profile!r}.')
        else:
            self.profile = profile or 'synthetic'
            if self.profile not in self.PROFILES:
                raise StoreError('invalid_profile', 'Profile must be synthetic or production.')
            marker.write_text(self.profile + '\n')
            os.chmod(marker, 0o600)
        self.blobs = self.root / 'objects'
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / 'context.sqlite3'
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(Path(__file__).with_name('schema.sql').read_text())
            self._migrate(c)
            c.execute('INSERT OR IGNORE INTO sources(id,label,policy,state) VALUES(?,?,?,?)',
                      ('user', 'User-provided context', 'durable', 'ready'))
            c.execute('INSERT OR IGNORE INTO sources(id,label,policy,state) VALUES(?,?,?,?)',
                      ('oura', 'Oura official MCP (external, no local mirror)', 'ephemeral', 'not_connected'))
            c.execute('INSERT OR IGNORE INTO sources(id,label,policy,state) VALUES(?,?,?,?)',
                      ('apple_health_export', 'Apple Health XML export (backfill only)', 'durable', 'not_connected'))
        os.chmod(self.db, 0o600)

    def _migrate(self, c: sqlite3.Connection) -> None:
        current = int(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        if current > migrations.TARGET:
            raise StoreError('schema_mismatch', 'Database is newer than this program; never downgrade.')
        if current == migrations.TARGET:
            return
        # Snapshot before taking the write lock: the backup API cannot run inside this connection's own write
        # transaction (it hangs waiting on itself).
        if c.execute('SELECT EXISTS(SELECT 1 FROM records) OR EXISTS(SELECT 1 FROM observations)').fetchone()[0]:
            snap = self.root / 'migrations'
            snap.mkdir(exist_ok=True, mode=0o700)
            target = sqlite3.connect(snap / f'pre-v{current + 1}-{int(time.time())}.sqlite3')
            try:
                c.backup(target)
            finally:
                target.close()
        c.execute('BEGIN IMMEDIATE')
        try:
            # Another process may have migrated between the check and the lock.
            now_v = int(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
            if now_v < migrations.TARGET:
                migrations.apply(c, now_v, utcnow())
            c.commit()
        except BaseException:
            c.rollback()
            raise

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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    # ---- sources -------------------------------------------------------------------------
    def register_source(self, source_id: str, label: str, policy: str = 'durable') -> None:
        """Operator/importer only. Not a model-facing operation."""
        nonempty(source_id, 'source_id', 200)
        if policy not in {'durable', 'ephemeral', 'blocked'}:
            raise StoreError('invalid_policy', 'Unknown source policy.')
        if source_id.startswith('synthetic:') and self.profile != 'synthetic':
            raise StoreError('source_restricted', 'Synthetic sources exist only in the synthetic profile.')
        if restricted_origin(source_id, label) and not source_id.startswith('synthetic:') and policy == 'durable':
            raise StoreError('source_restricted', 'Real Oura mirroring is not enabled in this implementation.')
        with self.connect() as c:
            c.execute('INSERT INTO sources(id,label,policy) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING',
                      (source_id, nonempty(label, 'label', 200), policy))

    def _source(self, c: sqlite3.Connection, source_id: str) -> None:
        row = c.execute('SELECT policy FROM sources WHERE id=?', (source_id,)).fetchone()
        if not row or row['policy'] != 'durable':
            raise StoreError('source_restricted', 'This source cannot be persisted by this store.')

    def mark_source_attempt(self, source_id: str, error_code: str | None) -> None:
        with self.connect() as c:
            c.execute("UPDATE sources SET last_attempt_at=?, state=CASE WHEN ? IS NULL THEN state ELSE 'error' END "
                      'WHERE id=?', (utcnow(), error_code, source_id))

    # ---- mutation core -------------------------------------------------------------------
    def _mutate(self, request_id: str, body: dict, fn: Callable[[sqlite3.Connection], dict]) -> dict:
        nonempty(request_id, 'request_id', 200)
        digest = hashlib.sha256(dump(body).encode()).hexdigest()
        with self.connect() as c:
            try:
                c.execute('BEGIN IMMEDIATE')
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
            except sqlite3.OperationalError as e:
                if c.in_transaction:
                    c.rollback()
                if 'locked' in str(e) or 'busy' in str(e):
                    raise StoreError('storage_busy', 'Storage busy; retry with the same request_id.') from e
                if 'disk' in str(e) or 'full' in str(e) or 'readonly' in str(e):
                    raise StoreError('storage_unavailable', 'Storage full or read-only; nothing was saved.') from e
                raise
            except BaseException:
                if c.in_transaction:
                    c.rollback()
                raise

    def receipt(self, request_id: str) -> dict | None:
        """Lets a client that lost the connection learn whether its write committed."""
        with self.connect() as c:
            row = c.execute('SELECT result_json FROM receipts WHERE request_id=?', (request_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # ---- evidence ------------------------------------------------------------------------
    def _ref_version(self, c: sqlite3.Connection, ref: str) -> str | None:
        """Current version of an evidence reference, or None when it no longer exists/applies."""
        kind = ref_kind(ref)
        if kind == 'record':
            row = c.execute('SELECT source_id FROM active_records WHERE id=?', (ref,)).fetchone()
            return 'active' if row else None
        if kind == 'observation':
            row = c.execute('SELECT updated_at, deleted FROM observations WHERE id=?', (ref,)).fetchone()
            return None if not row or row['deleted'] else row['updated_at']
        sha, _, page = ref[4:].partition('#p')
        if not c.execute('SELECT 1 FROM objects WHERE sha256=?', (sha,)).fetchone():
            return None
        if not page:
            return 'immutable'  # original bytes are content-addressed
        row = c.execute('SELECT sha256 FROM object_pages WHERE object_sha=? AND page=?', (sha, int(page))).fetchone()
        return None if row is None else 'text:' + row[0]  # derived text: re-extraction changes the version

    def _evidence_versions(self, c: sqlite3.Connection, refs: list[str]) -> dict[str, str]:
        versions = {}
        for ref in refs:
            v = self._ref_version(c, ref)
            if v is None:
                raise StoreError('missing_evidence', f'Evidence {ref} does not exist or is no longer current.')
            if ref_kind(ref) == 'record':
                self._source(c, c.execute('SELECT source_id FROM records WHERE id=?', (ref,)).fetchone()[0])
            versions[ref] = v
        return versions

    def generation(self) -> int:
        """Change-log sequence now: every committed record, observation batch, extraction and preference change
        advances it."""
        with self.connect() as c:
            return c.execute('SELECT coalesce(max(seq),0) FROM changes').fetchone()[0]

    def issue_receipt(self, refs: set[str], observations: bool, seq: int) -> str:
        """Store what one read returned: `seq` (taken before the read ran), the evidence ids it returned, and whether it
        read observation data. A derived write cites evidence through receipts, never through a bare sequence."""
        rid = 'rr_' + uuid.uuid4().hex
        with self.transaction() as c:
            c.execute('INSERT INTO read_receipts VALUES(?,?,?,?,?)',
                      (rid, seq, int(observations), dump(sorted(refs)[:5000]), utcnow()))
            c.execute('DELETE FROM read_receipts WHERE created_at<?', (utc_in(-7 * 86400),))
        return rid

    def _bind(self, c: sqlite3.Connection, evidence: list[str], receipts: list[str]) -> tuple[int, bool]:
        """(earliest read sequence, depends on observations) for evidence cited through `receipts`; refuses a receipt
        the store did not issue and evidence none of the receipts returned."""
        rows = c.execute(f'SELECT seq, observations, refs_json FROM read_receipts WHERE id IN '
                         f'({",".join("?" * len(receipts))})', receipts).fetchall()
        if len(rows) != len(set(receipts)):
            raise StoreError('evidence_unbound', 'Unknown or expired read_receipt; read the evidence again.')
        read = set().union(*(json.loads(r['refs_json']) for r in rows))
        unread = [e for e in evidence if e not in read and not any(x.startswith(e + '#') for x in read)]
        if unread:
            raise StoreError('evidence_unbound', f'Evidence not returned by the cited reads: {", ".join(unread[:5])}. '
                             'Pass the read_receipt of the read that returned it.')
        return min(r['seq'] for r in rows), any(r['observations'] for r in rows) or any(
            ref_kind(e) == 'observation' for e in evidence)

    def _changed_since(self, c: sqlite3.Connection, refs: list[str], seq: int, observations: bool) -> list[str]:
        """What changed after a read at `seq` that the reader cannot have seen: any observation batch when the evidence
        depends on observation data (conservative: an aggregate's population, canonical membership or repeat marks
        can change without touching a cited id), a cited record written after it, a cited page re-extracted after it."""
        stale = []
        if observations and c.execute("SELECT 1 FROM changes INDEXED BY changes_entity_seq WHERE entity='observations' "
                                      'AND seq>? LIMIT 1', (seq,)).fetchone():
            stale.append('observations')
        for ref in refs:
            kind = ref_kind(ref)
            if kind == 'record':
                entity, eid = 'record', ref
            elif kind == 'object' and '#p' in ref:
                entity, eid = 'extraction', ref[4:].partition('#p')[0]
            else:
                continue
            if c.execute('SELECT 1 FROM changes INDEXED BY changes_entity_seq WHERE entity=? AND seq>? AND entity_id=? '
                         'LIMIT 1', (entity, seq, eid)).fetchone():
                stale.append(ref)
        return stale

    def _stale_refs(self, c: sqlite3.Connection, versions: dict[str, str]) -> list[str]:
        return [ref for ref, v in versions.items() if self._ref_version(c, ref) != v]

    def read_versions(self, refs: list[str]) -> dict[str, str]:
        """Current version of each existing ref, for a reader to echo back as a candidate's evidence_versions."""
        with self.connect() as c:
            return {r: v for r in refs if (v := self._ref_version(c, r)) is not None}

    # ---- records -------------------------------------------------------------------------
    def put_record(self, *, request_id: str, kind: str, text: str, occurred_at: str,
                   timezone_name: str = 'America/Chicago', payload: dict | None = None,
                   source_id: str = 'user', source_key: str | None = None,
                   object_sha: str | None = None, supersedes: str | None = None,
                   evidence_ids: list[str] | None = None, read_receipts: list[str] | None = None) -> dict:
        """With read_receipts the cited evidence is bound to the reads that returned it and the dependency is kept
        (evidence.bound). Without, the record is stored with its evidence unverified (tools require receipts for any
        derived write; trusted internal writers may omit them)."""
        if kind not in self.KINDS:
            raise StoreError('invalid_kind', 'Unsupported context kind.')
        nonempty(text, 'text')
        check_tz(timezone_name)
        at = instant(occurred_at)
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or len(dump(payload)) > 100000:
            raise StoreError('invalid_payload', 'payload must be an object under the size cap.')
        evidence_ids = sorted(set(evidence_ids or []))
        if len(evidence_ids) > 100:
            raise StoreError('invalid_argument', 'At most 100 evidence IDs.')
        body = dict(op='record', kind=kind, text=text, at=at, tz=timezone_name, payload=payload,
                    source=source_id, source_key=source_key, object=object_sha,
                    supersedes=supersedes, evidence=evidence_ids, receipts=sorted(set(read_receipts or [])))

        def write(c: sqlite3.Connection) -> dict:
            self._source(c, source_id)
            versions = self._evidence_versions(c, evidence_ids)
            bound = self._bind(c, evidence_ids, read_receipts) if read_receipts else None
            if bound and (stale := self._changed_since(c, evidence_ids, *bound)):
                raise StoreError('stale_evidence', f'Evidence changed after it was read ({", ".join(stale[:5])}); '
                                 're-read it and write again with the new read_receipt.')
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
            c.executemany('INSERT INTO evidence_links VALUES(?,?)',
                          [(rid, x) for x in evidence_ids if ref_kind(x) == 'record'])
            c.executemany('INSERT INTO evidence_refs VALUES(?,?,?)', [(rid, r, v) for r, v in versions.items()])
            if bound:
                c.execute('INSERT INTO record_dependencies VALUES(?,?,?)', (rid, bound[0], int(bound[1])))
            local = datetime.fromisoformat(at).astimezone(ZoneInfo(timezone_name)).isoformat(timespec='seconds')
            out = {'record_id': rid, 'kind': kind, 'occurred_at': at, 'occurred_at_local': local,
                   'evidence_ids': evidence_ids}
            if supersedes:
                out['supersedes'] = supersedes
            return out
        return self._mutate(request_id, body, write)

    def _decorate(self, c: sqlite3.Connection, row: sqlite3.Row) -> dict:
        r = dict(row)
        r['payload'] = json.loads(r.pop('payload_json'))
        # Stored instants are UTC; give the local wall time too so callers never re-label UTC as local.
        r['occurred_at_local'] = datetime.fromisoformat(r['occurred_at']).astimezone(
            ZoneInfo(r['timezone'])).isoformat(timespec='seconds')
        newer = c.execute('SELECT id FROM records WHERE supersedes=?', (r['id'],)).fetchone()
        r['active'] = newer is None
        r['superseded_by'] = newer[0] if newer else None
        refs = {x['ref_id']: x['ref_version'] for x in
                c.execute('SELECT ref_id, ref_version FROM evidence_refs WHERE record_id=?', (r['id'],))}
        dep = c.execute('SELECT seq, observations FROM record_dependencies WHERE record_id=?', (r['id'],)).fetchone()
        if refs or dep:
            stale = self._stale_refs(c, refs)
            if dep:
                stale += [x for x in self._changed_since(c, [], dep['seq'], bool(dep['observations'])) if x not in stale]
            r['evidence'] = {'ids': sorted(refs), 'stale_ids': stale, 'bound': dep is not None,
                             'current': dep is not None and not stale}
            if dep is None:
                r['evidence']['note'] = 'written without a read receipt: freshness unverified'
        if r['object_sha']:
            ex = c.execute('SELECT status, page_count, method FROM extractions WHERE object_sha=?',
                           (r['object_sha'],)).fetchone()
            r['extraction'] = dict(ex) if ex else None
        return r

    def get_records(self, ids: list[str]) -> dict:
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise StoreError('invalid_ids', 'Provide 1–100 returned record IDs.')
        with self.connect() as c:
            rows = [self._decorate(c, r) for r in
                    c.execute(f'SELECT * FROM records WHERE id IN ({",".join("?" for _ in ids)})', ids)]
        present = {r['id'] for r in rows}
        return {'records': rows, 'missing_ids': [x for x in ids if x not in present]}

    def history(self, record_id: str) -> list[dict]:
        """Full revision chain (oldest first) containing record_id."""
        with self.connect() as c:
            row = c.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
            if not row:
                raise StoreError('not_found', 'Record not found.')
            while row['supersedes']:
                row = c.execute('SELECT * FROM records WHERE id=?', (row['supersedes'],)).fetchone()
            chain = []
            while row:
                chain.append(self._decorate(c, row))
                row = c.execute('SELECT * FROM records WHERE supersedes=?', (row['id'],)).fetchone()
            return chain

    def search(self, *, query: str = '', kind: str | None = None, limit: int = 50, after_id: str = '',
               start_at: str | None = None, end_at: str | None = None, cursor: str | None = None,
               include_superseded: bool = False) -> dict:
        """Unicode substring search over record text/payload and extracted original pages.

        Newest first with a keyset cursor; CJK works because no tokenizer is involved.
        """
        positive_limit(limit, 500)
        if not isinstance(query, str) or len(query) > 500:
            raise StoreError('invalid_query', 'query must be a bounded string.')
        if kind is not None and kind not in self.KINDS:
            raise StoreError('invalid_kind', 'Unsupported context kind.')
        # Whitespace-separated terms match if ANY term appears (substring, so CJK needs no tokenizer).
        terms = [t for t in query.split() if t][:12] or ['']
        likes = ['%' + t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for t in terms]
        like = likes[0]
        table = 'records' if include_superseded else 'active_records'
        one = ("(r.text LIKE ? ESCAPE '\\' OR r.payload_json LIKE ? ESCAPE '\\' OR (r.object_sha IS NOT NULL AND "
               "EXISTS(SELECT 1 FROM object_pages p WHERE p.object_sha=r.object_sha AND p.text LIKE ? ESCAPE '\\')))")
        where = ['(' + ' OR '.join([one] * len(likes)) + ')']
        args: list[Any] = [x for lk in likes for x in (lk, lk, lk)]
        if kind is not None:
            where.append('r.kind=?')
            args.append(kind)
        if start_at:
            where.append('r.occurred_at>=?')
            args.append(instant(start_at))
        if end_at:
            where.append('r.occurred_at<?')
            args.append(instant(end_at))
        cursor = cursor or after_id or None
        if cursor:
            try:
                at, rid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            except (ValueError, TypeError) as e:
                raise StoreError('invalid_cursor', 'Use next_cursor exactly as returned.') from e
            where.append('(r.occurred_at<? OR (r.occurred_at=? AND r.id<?))')
            args += [at, at, rid]
        order = 'r.occurred_at DESC, r.id DESC'
        with self.connect() as c:
            # Total matches for the same filters (ignoring the cursor), so "all seen" is explicit, not inferred.
            base_where = where[:-1] if cursor else where
            base_args = args[:-3] if cursor else args
            total = c.execute(f'SELECT count(*) FROM {table} r WHERE ' + ' AND '.join(base_where),
                              base_args).fetchone()[0]
            rows = c.execute(f'SELECT r.* FROM {table} r WHERE ' + ' AND '.join(where) + f' ORDER BY {order} LIMIT ?',
                             [*args, limit + 1]).fetchall()
            more = len(rows) > limit
            rows = [self._decorate(c, r) for r in rows[:limit]]
            if query:
                for r in rows:
                    if r['object_sha']:
                        r['matched_pages'] = sorted({p[0] for lk in likes for p in c.execute(
                            "SELECT page FROM object_pages WHERE object_sha=? AND text LIKE ? ESCAPE '\\' "
                            'ORDER BY page LIMIT 50', (r['object_sha'], lk))})
        nxt = None
        if more:
            nxt = base64.urlsafe_b64encode(dump([rows[-1]['occurred_at'], rows[-1]['id']]).encode()).decode()
        hint = None
        if not rows and query and not cursor:
            # A lexical miss is not absence: wording differs ("supplements" vs "capsule", English vs Chinese).
            with self.connect() as c:
                current = [{'id': r['id'], 'kind': r['kind'], 'text': r['text'][:160]} for r in c.execute(
                    "SELECT id, kind, text FROM active_records WHERE kind IN ('routine','question') "
                    'ORDER BY occurred_at DESC LIMIT 30')]
            hint = {'message': 'No substring match. Wording may differ; review current routines/questions below, '
                               'try synonyms or other languages, or use context_query.', 'current_routines_and_questions': current}
        return {'records': rows, 'total_matches': total, 'has_more': more, 'next_cursor': nxt, 'zero_hit_hint': hint,
                'next_after_id': nxt, 'terms': terms if query else [],
                'search_mode': 'unicode_substring_any_term', 'snapshot_stable': False,
                'note': 'has_more=true means more matches exist; absence on this page is not absence.'}

    # ---- originals -----------------------------------------------------------------------
    def put_attachment_bytes(self, *, request_id: str, data: bytes, filename: str, mime: str,
                             text: str, occurred_at: str, timezone_name: str = 'America/Chicago',
                             payload: dict | None = None, max_bytes: int = 25 * 1024 * 1024,
                             source_key: str | None = None) -> dict:
        """Called only after a trusted file transport supplies actual bytes, not a filename claim."""
        if not isinstance(data, bytes) or not data or len(data) > max_bytes:
            raise StoreError('file_size', 'Attachment must be nonempty and within the size cap.')
        filename = nonempty(filename, 'filename', 255)
        if Path(filename).name != filename or '\\' in filename or any(ord(ch) < 32 for ch in filename):
            raise StoreError('invalid_filename', 'A display filename, not a path, is required.')
        nonempty(mime, 'mime', 100)
        nonempty(text, 'text')
        at = instant(occurred_at)
        check_tz(timezone_name)
        payload = dict(payload or {})
        sha = self._write_blob(data)
        detected = sniff_mime(data)
        body = dict(op='attachment', sha=sha, filename=filename, mime=mime, text=text, at=at, tz=timezone_name,
                    payload=payload, source_key=source_key)

        def write(c: sqlite3.Connection) -> dict:
            c.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?,?,?)', (sha, len(data), detected, filename, utcnow()))
            c.execute("INSERT OR IGNORE INTO extractions(object_sha,status,updated_at) VALUES(?, 'pending', ?)",
                      (sha, utcnow()))
            status = c.execute('SELECT status FROM extractions WHERE object_sha=?', (sha,)).fetchone()[0]
            rid = 'rec_' + uuid.uuid4().hex
            meta = {**payload, 'filename': filename, 'declared_mime': mime, 'detected_mime': detected}
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (rid, 'attachment', at, timezone_name, text, dump(meta), 'user', source_key, sha, None, utcnow()))
            return {'record_id': rid, 'object_sha256': sha, 'size': len(data), 'detected_mime': detected,
                    'original_saved': True, 'extraction_status': status}
        return self._mutate(request_id, body, write)

    def _write_blob(self, data: bytes) -> str:
        """Content-addressed, fsynced original. The file AND its directory entry are synced on every call, including
        when the blob already exists (an earlier writer may have renamed it and then failed its directory sync), so
        no database reference is ever made to bytes whose publication is not durable."""
        sha = hashlib.sha256(data).hexdigest()
        blob = self.blobs / sha
        temp = None
        try:
            if not blob.exists():
                fd, temp = tempfile.mkstemp(prefix='.upload-', dir=self.blobs)
                with os.fdopen(fd, 'wb') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(temp, 0o600)
                os.replace(temp, blob)
                temp = None
            for path in (blob, self.blobs):
                fd = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as e:
            raise StoreError('storage_unavailable', 'Could not write the original; nothing was saved.') from e
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        if hashlib.sha256(blob.read_bytes()).hexdigest() != sha:
            raise StoreError('object_corrupt', 'Object checksum failed; no capture acknowledgment.')
        return sha

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

    def object_info(self, sha: str) -> dict:
        self.read_object(sha)
        with self.connect() as c:
            obj = dict(c.execute('SELECT * FROM objects WHERE sha256=?', (sha,)).fetchone())
            ex = c.execute('SELECT * FROM extractions WHERE object_sha=?', (sha,)).fetchone()
            obj['extraction'] = dict(ex) if ex else None
            obj['records'] = [r[0] for r in c.execute('SELECT id FROM records WHERE object_sha=?', (sha,))]
        return obj

    def set_extraction(self, sha: str, *, status: str, method: str | None, pages: list[str] | None = None,
                       error_code: str | None = None) -> dict:
        """Derived page text; never replaces or edits the original bytes."""
        if status not in {'done', 'partial', 'failed', 'not_applicable'}:
            raise StoreError('invalid_status', 'Unsupported extraction status.')
        with self.transaction() as c:
            if not c.execute('SELECT 1 FROM objects WHERE sha256=?', (sha,)).fetchone():
                raise StoreError('not_found', 'Object is not registered.')
            c.execute('DELETE FROM object_pages WHERE object_sha=?', (sha,))
            c.executemany('INSERT INTO object_pages(object_sha, page, text, method, sha256) VALUES(?,?,?,?,?)',
                          [(sha, i + 1, t, method or 'unknown', hashlib.sha256(t.encode()).hexdigest())
                           for i, t in enumerate(pages or [])])
            c.execute('INSERT INTO extractions VALUES(?,?,?,?,?,?) ON CONFLICT(object_sha) DO UPDATE SET '
                      'status=excluded.status, method=excluded.method, page_count=excluded.page_count, '
                      'error_code=excluded.error_code, updated_at=excluded.updated_at',
                      (sha, status, method, len(pages or []), error_code, utcnow()))
            c.execute("INSERT INTO changes(entity, entity_id, detail, at) VALUES('extraction', ?, ?, ?)",
                      (sha, status, utcnow()))
        return {'object_sha256': sha, 'extraction_status': status, 'pages': len(pages or [])}

    def read_pages(self, sha: str, start: int = 1, end: int | None = None, max_chars: int = 60000) -> dict:
        self.read_object(sha)
        end = end or start + 19
        if type(start) is not int or type(end) is not int or start < 1 or end < start or end - start > 99:
            raise StoreError('invalid_pages', 'Use 1-based page ranges of at most 100 pages.')
        with self.connect() as c:
            ex = c.execute('SELECT status, page_count, method FROM extractions WHERE object_sha=?', (sha,)).fetchone()
            rows = c.execute('SELECT page, text FROM object_pages WHERE object_sha=? AND page BETWEEN ? AND ? '
                             'ORDER BY page', (sha, start, end)).fetchall()
        pages, used, truncated = [], 0, False
        for page, text in rows:
            if used + len(text) > max_chars:
                pages.append({'page': page, 'text': text[:max(0, max_chars - used)], 'truncated': True})
                truncated = True
                break
            pages.append({'page': page, 'text': text})
            used += len(text)
        return {'object_sha256': sha, 'extraction': dict(ex) if ex else None, 'pages': pages,
                'truncated': truncated, 'evidence_ids': [f'obj:{sha}#p{p["page"]}' for p in pages]}

    # ---- read-only SQL -------------------------------------------------------------------
    def query_readonly(self, sql: str, parameters: list | None = None, limit: int = 500) -> dict:
        nonempty(sql, 'sql', 20000)
        positive_limit(limit)
        if parameters is not None and (not isinstance(parameters, list) or len(parameters) > 100):
            raise StoreError('invalid_argument', 'parameters must be a list of at most 100 values.')
        # Read-only connection AND query_only AND authorizer. String-prefix checks are insufficient.
        c = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True, isolation_level=None)
        c.execute('PRAGMA query_only=ON')
        c.enable_load_extension(False)
        c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_048_576)
        c.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000)
        c.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
        c.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 50)
        deadline = time.monotonic() + 8.0  # multi-year aggregates over ~2M rows; still bounded
        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

        # One read transaction: the maintenance gate and the query see the same WAL snapshot, so an import that starts
        # between them cannot hand this query a mixed state.
        c.execute('BEGIN')
        gated = c.execute("SELECT value FROM meta WHERE key='import_in_progress'").fetchone() is not None
        touched: set[str] = set()

        def authorize(action: int, a: str | None, b: str | None, db: str | None, trigger: str | None) -> int:
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and a:
                touched.add(a)
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
            if gated and touched & self.OBSERVATION_TABLES:
                raise StoreError('import_in_progress', 'An Apple import is replacing observation rows; old and new '
                                 'rows overlap until it finishes, so observation totals would be wrong. Records, '
                                 'originals and search still work. Say so to the user and retry later.')
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
            result = {'columns': columns, 'rows': rows, 'reads_observations': bool(touched & self.OBSERVATION_TABLES),
                      'truncated': truncated, 'max_rows': limit,
                      'query_sha256': hashlib.sha256((sql + dump(parameters or [])).encode()).hexdigest()}
            if len(dump(result).encode()) > 256000:
                raise StoreError('result_too_large', 'Narrow columns or page the requested range.')
            return result
        except sqlite3.Error as e:
            # SQLite's message names the problem (no such column, not authorized, interrupted) and holds no data.
            detail = str(e).replace('\n', ' ')[:200]
            if detail == 'interrupted':
                raise StoreError('query_timeout', 'The query exceeded its 8 s budget. Narrow the time range or '
                                 'metric, aggregate in fewer steps, or use observation_catalog for counts.') from e
            raise StoreError('query_rejected', f'Read-only query rejected: {detail}. Readable tables: '
                             f'{", ".join(sorted(self.PUBLIC_TABLES))}.') from e
        finally:
            c.close()

    # ---- observations --------------------------------------------------------------------
    def ingest_batch(self, *, request_id: str, source_id: str, samples: list[dict],
                     deleted_ids: list[str], cursor: str, coverage: dict | None = None,
                     _extra: Callable[[sqlite3.Connection], dict] | None = None) -> dict:
        """Trusted importer-only. Upserts, deletes, source cursor and change log commit together.

        Samples whose provenance names a restricted vendor are dropped before persistence and counted.
        """
        if not isinstance(samples, list) or len(samples) > 5000 or len(deleted_ids) > 5000:
            raise StoreError('batch_size', 'Use pages of at most 5000 samples/deletions.')
        checked, seen, filtered = [], set(), 0
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
            if restricted_origin(s.get('source_name'), s.get('source_bundle_id'),
                                 dump(s.get('device') or {}), dump(s.get('metadata') or {})):
                filtered += 1
                continue
            checked.append((s, start, end, tz))
        if seen.intersection(deleted_ids):
            raise StoreError('ambiguous_batch', 'A sample cannot be upserted and deleted in the same page.')
        body = dict(op='batch', source_id=source_id, samples=samples, deleted=deleted_ids,
                    cursor=cursor, coverage=coverage or {})

        def write(c: sqlite3.Connection) -> dict:
            self._source(c, source_id)
            now = utcnow()
            metrics: set[str] = set()
            lo = hi = None
            delta: dict[tuple[str, str], list] = {}  # (metric, unit) -> [count change, min start, max end]

            def bump(metric: str, unit: str, n: int, lo_: str | None = None, hi_: str | None = None) -> None:
                d = delta.setdefault((metric, unit), [0, None, None])
                d[0] += n
                if lo_ and (d[1] is None or lo_ < d[1]):
                    d[1] = lo_
                if hi_ and (d[2] is None or hi_ > d[2]):
                    d[2] = hi_
            shrink: set[tuple[str, str]] = set()  # pairs that may have lost their first/last sample
            live = source_id.startswith('apple_health:')
            touched: set[str] = set()  # export origin_keys whose repeat marks must be re-resolved

            def leave(prev: sqlite3.Row) -> None:
                """prev (metric, unit, _, start, end) leaves or changes: its pair needs a re-read only if prev was that
                pair's first or last sample, and the change detail names what was removed."""
                nonlocal lo, hi
                cat = c.execute('SELECT first_at, last_at FROM observation_catalog WHERE source_id=? AND metric=? '
                                'AND unit=?', (source_id, prev[0], prev[1])).fetchone()
                if cat is None or prev[3] == cat[0] or prev[4] == cat[1]:
                    shrink.add((prev[0], prev[1]))
                metrics.add(prev[0])
                lo = prev[3] if lo is None or prev[3] < lo else lo
                hi = prev[4] if hi is None or prev[4] > hi else hi
            for s, start, end, tz in checked:
                oid = 'obs_' + hashlib.sha256((source_id + '\0' + s['native_id']).encode()).hexdigest()
                origin = s.get('origin_key')
                pair = (s['metric'], s.get('unit') or '')
                prev = c.execute("SELECT metric, coalesce(unit,''), deleted, start_at, end_at, origin_key "
                                 'FROM observations WHERE id=?', (oid,)).fetchone()
                if prev and prev[5] and not live:
                    touched.add(prev[5])  # the group this row leaves must be re-resolved too
                if prev and not prev[2]:
                    moved = (prev[0], prev[1]) != pair
                    bump(prev[0], prev[1], -moved)
                    bump(*pair, moved, start, end)
                    if moved or start > prev[3] or end < prev[4]:
                        leave(prev)
                else:
                    bump(*pair, 1, start, end)
                if live and origin:
                    c.execute('INSERT OR IGNORE INTO supersessions VALUES(?,?)', (origin, oid))
                c.execute('''INSERT INTO observations(id,source_id,native_id,metric,start_at,end_at,timezone,value_num,
                    value_text,unit,raw_json,deleted,updated_at,source_name,bundle_id,origin_key)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id,native_id) DO UPDATE SET
                    metric=excluded.metric,start_at=excluded.start_at,end_at=excluded.end_at,
                    timezone=excluded.timezone,value_num=excluded.value_num,value_text=excluded.value_text,
                    unit=excluded.unit,raw_json=excluded.raw_json,deleted=0,updated_at=excluded.updated_at,
                    source_name=excluded.source_name,bundle_id=excluded.bundle_id,origin_key=excluded.origin_key''',
                    (oid, source_id, s['native_id'], s['metric'], start, end, tz, s.get('value_num'),
                     s.get('value_text'), s.get('unit'), dump(s), 0, now, s.get('source_name'),
                     s.get('source_bundle_id'), origin))
                metrics.add(s['metric'])
                lo = start if lo is None or start < lo else lo
                hi = end if hi is None or end > hi else hi
                if origin and not live:
                    touched.add(origin)
            for sid in deleted_ids:
                nonempty(sid, 'deleted_id', 200)
                # A deletion-only object has no measurement body and stays outside active data.
                oid = 'obs_' + hashlib.sha256((source_id + '\0' + sid).encode()).hexdigest()
                prev = c.execute("SELECT metric, coalesce(unit,''), deleted, start_at, end_at, origin_key "
                                 'FROM observations WHERE id=?', (oid,)).fetchone()
                if prev and not prev[2]:
                    bump(prev[0], prev[1], -1)
                    leave(prev)
                    if prev[5] and not live:
                        touched.add(prev[5])
                c.execute('''INSERT INTO observations(id,source_id,native_id,metric,start_at,end_at,timezone,value_num,
                    value_text,unit,raw_json,deleted,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id,native_id) DO UPDATE SET deleted=1,updated_at=excluded.updated_at''',
                    (oid, source_id, sid, 'tombstone', now, now, 'UTC', None, None, None, '{}', 1, now))
            for k in touched:
                mark_repeats(c, source_id, k)
            # Any deletion or correction may remove the latest sample; one seek on obs_source_end, not a scan.
            latest = c.execute('SELECT max(end_at) FROM observations WHERE source_id=? AND deleted=0',
                               (source_id,)).fetchone()[0]
            c.execute('''UPDATE sources SET state='ready',last_attempt_at=?,last_success_at=?,
                      latest_sample_at=?,cursor=?,coverage_json=? WHERE id=?''',
                      (now, now, latest, cursor, dump(coverage or {}), source_id))
            if checked or deleted_ids:
                self._apply_catalog(c, source_id, delta, shrink)
                c.execute("INSERT INTO changes(entity, entity_id, detail, at) VALUES('observations', ?, ?, ?)",
                          (source_id, dump({'metrics': sorted(metrics), 'start': lo, 'end': hi,
                                            'upserted': len(checked), 'deleted': len(deleted_ids)}), now))
            out = {'upserted': len(checked), 'deleted': len(deleted_ids), 'filtered_restricted': filtered,
                   'cursor': cursor, 'source_id': source_id}
            if _extra:
                out.update(_extra(c))
            return out
        return self._mutate(request_id, body, write)

    def _apply_catalog(self, c: sqlite3.Connection, source_id: str, delta: dict, shrink: set) -> None:
        """Maintain observation_catalog in O(page): count deltas are exact and the range only widens. A pair that
        lost a row or a boundary (deletion, metric/unit move, endpoint shrink) gets its first/last re-read by two index
        seeks (obs_lookup, obs_group_end)."""
        for (metric, unit), (n, lo, hi) in delta.items():
            c.execute('INSERT INTO observation_catalog VALUES(?,?,?,?,?,?) ON CONFLICT(source_id, metric, unit) DO '
                      'UPDATE SET n = n + excluded.n, first_at = min(coalesce(first_at, excluded.first_at), '
                      'coalesce(excluded.first_at, first_at)), last_at = max(coalesce(last_at, excluded.last_at), '
                      'coalesce(excluded.last_at, last_at))', (source_id, metric, unit, n, lo, hi))
        group = "FROM observations INDEXED BY {} WHERE metric=? AND source_id=? AND coalesce(unit,'')=? AND deleted=0"
        for metric, unit in shrink:
            args = (metric, source_id, unit)
            first = c.execute(f'SELECT start_at {group.format("obs_lookup")} ORDER BY start_at LIMIT 1', args).fetchone()
            last = c.execute(f'SELECT end_at {group.format("obs_group_end")} ORDER BY end_at DESC LIMIT 1',
                             args).fetchone()
            if first:
                c.execute('UPDATE observation_catalog SET first_at=?, last_at=? WHERE source_id=? AND metric=? '
                          'AND unit=?', (first[0], last[0], source_id, metric, unit))
        c.execute('DELETE FROM observation_catalog WHERE source_id=? AND n<=0', (source_id,))

    def source_status(self) -> dict:
        with self.connect() as c:
            c.execute('BEGIN')  # counts and the maintenance marker from one snapshot
            return self._source_status(c)

    def _source_status(self, c: sqlite3.Connection) -> dict:
        importing = c.execute("SELECT value FROM meta WHERE key='import_in_progress'").fetchone()
        rows = []
        for r in c.execute('SELECT * FROM sources ORDER BY id'):
            row = dict(r)
            row['coverage'] = json.loads(row.pop('coverage_json'))
            row['active_observations'] = c.execute(
                'SELECT coalesce(sum(n),0) FROM observation_catalog WHERE source_id=?', (r['id'],)).fetchone()[0]
            if importing:  # mid-import numbers mix two parser generations: withhold them, keep identity and state
                row.update(active_observations=None, latest_sample_at=None, coverage={'withheld': 'import_in_progress'})
            rows.append(row)
        out = {'checked_at': utcnow(), 'sources': rows,
               'note': 'latest_sample_at is not complete coverage; permission-denied can look like empty data. '
                       'apple_health_export is a one-time backfill, not continuous sync. canonical_observations hides '
                       'export rows superseded by the live stream and exact repeats of one source record; '
                       'canonical_observations_raw keeps every source row. Correlation parents (e.g. blood-pressure '
                       'pairs) are not stored: pair the child records by time if needed. Export dates without a '
                       'recorded zone use the importing timezone.'}
        if importing:
            out['observation_reads'] = ('blocked: an Apple import is in progress (old and new parser rows overlap); '
                                        'observation queries refuse and counts are withheld until it finishes')
        return out

    # ---- preferences ---------------------------------------------------------------------
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
            c.execute("INSERT INTO changes(entity, entity_id, detail, at) VALUES('preference', ?, ?, ?)",
                      (key, dump(value), utcnow()))
            return {'key': key, 'value': value}
        return self._mutate(request_id, dict(op='preference', key=key, value=value), write)

    def preferences(self) -> dict:
        out = {'proactivity': 'normal', 'timezone': 'America/Chicago'}
        with self.connect() as c:
            out.update({r['key']: json.loads(r['value_json']) for r in c.execute('SELECT * FROM preferences')})
        return out

    # ---- insights (candidates, outbox) ---------------------------------------------------
    def _question_open(self, c: sqlite3.Connection, qid: str) -> bool:
        row = c.execute("SELECT payload_json FROM active_records WHERE id=? AND kind='question'", (qid,)).fetchone()
        return bool(row) and json.loads(row[0]).get('state', 'open') not in self.CLOSED_QUESTION

    def queue_insight(self, *, request_id: str, candidate: dict, ttl_days: int | None = None,
                      fence: Callable[[sqlite3.Connection], None] | None = None, since_seq: int | None = None) -> dict:
        """Local background writer only. Silence is an output, not a persisted health conclusion.

        `fence` runs first inside the same transaction; raising there (e.g. lease lost) drops the whole write.
        `since_seq` (the generation before the investigation began) rejects evidence that changed during it."""
        if candidate.get('decision') == 'silence':
            return {'status': 'silent', 'queued': False}
        qid, evidence = self._candidate(candidate)
        ttl = self.INSIGHT_TTL_DAYS if ttl_days is None else ttl_days

        def write(c: sqlite3.Connection) -> dict:
            if fence:
                fence(c)
            versions, fingerprint, verdict = self._gate(c, candidate, qid, evidence, since_seq)
            if verdict:
                return verdict
            iid = 'ins_' + uuid.uuid4().hex
            c.execute('INSERT INTO insights(id,fingerprint,question_id,payload_json,state,evidence_versions_json,'
                      'created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                      (iid, fingerprint, qid, dump(candidate), 'pending', dump(versions), utcnow(),
                       utc_in(ttl * 86400)))
            return {'queued': True, 'insight_id': iid}
        return self._mutate(request_id, dict(op='insight', candidate=candidate), write)

    def _candidate(self, candidate: dict) -> tuple[str | None, list[str]]:
        if candidate.get('decision') != 'surface':
            raise StoreError('invalid_candidate', 'Use surface or silence.')
        for key in ('why_now', 'what_changed', 'unknowns', 'next_step'):
            nonempty(candidate.get(key), key, 5000)
        if any(p != 'durable' for p in candidate.get('source_policies', ['durable'])):
            raise StoreError('source_restricted', 'Only durable-source evidence may be surfaced from the outbox.')
        evidence = sorted(set(candidate.get('evidence_ids', [])))
        if not evidence:
            raise StoreError('missing_evidence', 'A proactive proposal needs actual stored evidence.')
        return candidate.get('question_id'), evidence

    def _gate(self, c: sqlite3.Connection, candidate: dict, qid: str | None, evidence: list[str],
              since_seq: int | None = None) -> tuple[dict[str, str], str, dict | None]:
        """(current evidence versions, fingerprint, why a real insight would not be queued now or None to queue)."""
        if qid and not self._question_open(c, qid):
            raise StoreError('missing_question', 'Question reference is not an active open stored question.')
        versions = self._evidence_versions(c, evidence)
        read = candidate.get('evidence_versions') or {}
        if any((versions[r] if r in versions else self._ref_version(c, r)) != v for r, v in read.items()) or (
                since_seq is not None and self._changed_since(
                    c, evidence, since_seq, any(ref_kind(e) == 'observation' for e in evidence))):
            raise StoreError('stale_evidence', 'Evidence changed after it was read; re-read and re-investigate.')
        fingerprint = hashlib.sha256(dump({'question_id': qid, 'evidence': versions,
                                           'topic': candidate.get('topic', 'unspecified')}).encode()).hexdigest()
        old = c.execute('SELECT id FROM insights WHERE fingerprint=?', (fingerprint,)).fetchone()
        if old:
            return versions, fingerprint, {'queued': False, 'reason': 'duplicate', 'insight_id': old[0]}
        prefs = {r['key']: json.loads(r['value_json']) for r in c.execute('SELECT * FROM preferences')}
        mode = prefs.get('proactivity', 'normal')
        if mode == 'off':
            return versions, fingerprint, {'queued': False, 'reason': 'preference_off'}
        # Attention budget is policy, not a medical-significance threshold.
        hours = 168 if mode == 'quiet' else 72
        since = datetime.fromtimestamp(time.time() - hours * 3600, timezone.utc).isoformat(timespec='microseconds')
        if c.execute("SELECT 1 FROM insights WHERE created_at>? AND state IN('pending','delivered') LIMIT 1",
                     (since,)).fetchone():
            return versions, fingerprint, {'queued': False, 'reason': 'attention_budget'}
        return versions, fingerprint, None

    def queue_shadow(self, *, request_id: str, candidate: dict,
                     fence: Callable[[sqlite3.Connection], None] | None = None, since_seq: int | None = None) -> dict:
        """Shadow mode: record what the gate would have done, in shadow_insights only. Never reaches the outbox,
        pending_insights, bootstrap, real-insight dedup or the attention budget."""
        if candidate.get('decision') == 'silence':
            return {'status': 'silent', 'queued': False, 'shadow': True}
        qid, evidence = self._candidate(candidate)

        def write(c: sqlite3.Connection) -> dict:
            if fence:
                fence(c)
            versions, _, verdict = self._gate(c, candidate, qid, evidence, since_seq)
            sid = 'shd_' + uuid.uuid4().hex
            c.execute('INSERT INTO shadow_insights VALUES(?,?,?,?,?,?,?)',
                      (sid, qid, dump(candidate), dump(versions), int(verdict is None),
                       verdict and verdict['reason'], utcnow()))
            return {'queued': False, 'shadow': True, 'shadow_id': sid, 'would_queue': verdict is None,
                    'reason': verdict['reason'] if verdict else None}
        return self._mutate(request_id, dict(op='shadow', candidate=candidate), write)

    def pending_insights(self) -> dict:
        """Current, presentable candidates. Stale/expired ones are closed here, never shown."""
        if self.preferences()['proactivity'] == 'off':
            return {'insights': [], 'suppressed': True}
        out = []
        with self.transaction() as c:
            now = utcnow()
            for r in c.execute("SELECT * FROM insights WHERE state='pending' ORDER BY created_at").fetchall():
                row = dict(r)
                versions = json.loads(row['evidence_versions_json'] or '{}')
                if not versions:
                    versions = {x: 'active' for x in json.loads(row['payload_json']).get('evidence_ids', [])}
                reason = None
                if row['expires_at'] and row['expires_at'] < now:
                    reason, state = 'expired', 'expired'
                elif (row['question_id'] and not self._question_open(c, row['question_id'])) or \
                        self._stale_refs(c, versions):
                    reason, state = 'evidence_or_question_changed', 'stale'
                if reason:
                    c.execute('UPDATE insights SET state=?, closed_reason=? WHERE id=?', (state, reason, row['id']))
                    continue
                row['payload'] = json.loads(row.pop('payload_json'))
                row.pop('evidence_versions_json')
                out.append(row)
        return {'insights': out[:10], 'suppressed': False}

    def ack_insight(self, *, request_id: str, insight_id: str, disposition: str = 'delivered') -> dict:
        if disposition not in {'delivered', 'dismissed'}:
            raise StoreError('invalid_disposition', 'Use delivered or dismissed.')

        def write(c: sqlite3.Connection) -> dict:
            row = c.execute('SELECT state FROM insights WHERE id=?', (insight_id,)).fetchone()
            if not row:
                raise StoreError('not_found', 'Insight not found.')
            if row['state'] != 'pending':
                raise StoreError('not_pending', f'Insight is {row["state"]}; only pending insights can be acknowledged.')
            c.execute('UPDATE insights SET state=?,delivered_at=? WHERE id=?',
                      (disposition, utcnow(), insight_id))
            return {'insight_id': insight_id, 'disposition': disposition}
        return self._mutate(request_id, dict(op='ack', id=insight_id, disposition=disposition), write)

    # ---- backup / restore / export -------------------------------------------------------
    def backup(self, destination: str | Path) -> dict:
        """Online SQLite snapshot, then every referenced immutable object. Never copy a live WAL DB."""
        dest = Path(destination).expanduser().resolve()
        if dest.exists() or dest == self.root or self.root in dest.parents:
            raise StoreError('invalid_backup_path', 'Use a new directory outside the live data root.')
        dest.mkdir(parents=True, mode=0o700)
        old_umask = os.umask(0o077)  # snapshot files are 0600 from creation, not after the copy
        try:
            return self._backup_into(dest)
        finally:
            os.umask(old_umask)

    def _backup_into(self, dest: Path) -> dict:
        with self.connect() as src:
            target = sqlite3.connect(dest / 'context.sqlite3')
            try:
                src.backup(target)
                refs = target.execute('SELECT sha256 FROM objects').fetchall()
                schema = target.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
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
        (dest / 'PROFILE').write_text(self.profile + '\n')
        manifest = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in dest.rglob('*') if p.is_file()}
        # Written last: a snapshot without a manifest is incomplete and restore refuses it.
        (dest / 'backup_manifest.json').write_text(dump({'created_at': utcnow(), 'schema_version': schema,
                                                         'profile': self.profile, 'sha256': manifest}))
        return {'status': 'snapshot_complete', 'path': str(dest), 'objects': len(refs), 'cloud_synced': False}

    @classmethod
    def verify_snapshot(cls, snapshot: str | Path) -> dict:
        src = Path(snapshot).resolve()
        try:
            m = json.loads((src / 'backup_manifest.json').read_text())
            manifest = m['sha256']
        except (OSError, ValueError, KeyError) as e:
            raise StoreError('backup_invalid', 'Snapshot has no valid completion manifest.') from e
        if 'context.sqlite3' not in manifest:
            raise StoreError('backup_invalid', 'Snapshot has no database.')
        for name, sha in manifest.items():
            p = src / name
            allowed = name in {'context.sqlite3', 'PROFILE'} or (
                name.startswith('objects/') and len(name) == 72 and all(ch in '0123456789abcdef' for ch in name[8:]))
            if not allowed or p.is_symlink() or not p.is_file() or src not in p.resolve().parents:
                raise StoreError('backup_invalid', 'Snapshot contains an invalid path.')
            if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise StoreError('backup_invalid', 'Snapshot checksum failed.')
        db = sqlite3.connect((src / 'context.sqlite3').as_uri() + '?mode=ro', uri=True)
        try:
            if (db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                    or db.execute('PRAGMA foreign_key_check').fetchone() is not None):
                raise StoreError('backup_invalid', 'Snapshot integrity check failed.')
            objects = [sha for (sha,) in db.execute('SELECT sha256 FROM objects')]
            for sha in objects:
                if 'objects/' + sha not in manifest:
                    raise StoreError('backup_invalid', 'Snapshot lacks a referenced original.')
            counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                      for t in ('records', 'observations', 'objects')}
        finally:
            db.close()
        return {'files': len(manifest), 'counts': counts, 'created_at': m.get('created_at'),
                'profile': m.get('profile'), 'manifest': manifest}

    @classmethod
    def restore(cls, snapshot: str | Path, destination: str | Path) -> 'Store':
        """Verify an offline snapshot and restore ONLY into a new root; never overwrite live data."""
        src, dest = Path(snapshot).resolve(), Path(destination).resolve()
        if dest.exists() or src == dest or src in dest.parents:
            raise StoreError('invalid_restore_path', 'Restore into a new directory outside the snapshot.')
        manifest = cls.verify_snapshot(src)['manifest']
        dest.mkdir(parents=True, mode=0o700)
        for name in manifest:
            p = dest / name
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(src / name, p)
            os.chmod(p, 0o600)
        return cls(dest)

    def export(self, destination: str | Path) -> dict:
        """Portable, program-independent copy: JSONL records, CSV observations, originals index + bytes."""
        dest = Path(destination).expanduser().resolve()
        if dest.exists() or self.root in dest.parents or dest == self.root:
            raise StoreError('invalid_export_path', 'Use a new directory outside the live data root.')
        dest.mkdir(parents=True, mode=0o700)
        counts = {}
        with self.connect() as c:
            with open(dest / 'records.jsonl', 'w', encoding='utf-8') as f:
                n = 0
                for r in c.execute('SELECT * FROM records ORDER BY occurred_at, id'):
                    row = self._decorate(c, r)
                    f.write(dump(row) + '\n')
                    n += 1
                counts['records'] = n
            with open(dest / 'observations.csv', 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                cols = ['id', 'source_id', 'native_id', 'metric', 'start_at', 'end_at', 'timezone', 'value_num',
                        'value_text', 'unit', 'deleted', 'source_name', 'bundle_id', 'updated_at']
                w.writerow(cols)
                n = 0
                for r in c.execute(f'SELECT {",".join(cols)} FROM observations ORDER BY start_at, id'):
                    w.writerow(list(r))
                    n += 1
                counts['observations'] = n
            objs = [dict(r) for r in c.execute('SELECT * FROM objects ORDER BY created_at')]
            pages = [dict(r) for r in c.execute('SELECT * FROM object_pages ORDER BY object_sha, page')]
        (dest / 'originals').mkdir(mode=0o700)
        for o in objs:
            (dest / 'originals' / o['sha256']).write_bytes(self.read_object(o['sha256']))
        (dest / 'originals_index.json').write_text(dump(objs))
        (dest / 'extracted_pages.jsonl').write_text(''.join(dump(p) + '\n' for p in pages))
        (dest / 'preferences.json').write_text(dump(self.preferences()))
        shutil.copyfile(Path(__file__).with_name('DATA_DICTIONARY.md'), dest / 'DATA_DICTIONARY.md')
        counts['originals'] = len(objs)
        return {'status': 'exported', 'path': str(dest), 'counts': counts}

    # ---- context map ---------------------------------------------------------------------
    def bootstrap(self) -> dict:
        """Starting index, not a final evidence filter; the model may search/read/query freely."""
        with self.connect() as c:
            c.execute('BEGIN')  # catalog, source status and the maintenance marker from one snapshot
            status = self._source_status(c)
            context = [self._decorate(c, r) for r in c.execute(
                "SELECT * FROM active_records WHERE kind IN ('routine','question','note','preference') "
                "ORDER BY occurred_at DESC LIMIT 20")]
            recent = [self._decorate(c, r) for r in c.execute(
                "SELECT * FROM active_records WHERE kind IN ('event','attachment','analysis') "
                'ORDER BY occurred_at DESC LIMIT 10')]
            counts = {k: n for k, n in c.execute('SELECT kind, count(*) FROM active_records GROUP BY kind')}
            metrics = [] if 'observation_reads' in status else [dict(r) for r in c.execute(
                'SELECT source_id, metric, unit, n, first_at, last_at FROM observation_catalog '
                'ORDER BY source_id, metric LIMIT 300')]
            change_seq = c.execute('SELECT coalesce(max(seq),0) FROM changes').fetchone()[0]
            first, last = c.execute('SELECT min(started_at), max(started_at) FROM worker_runs').fetchone()
        observed = None
        if first:
            observed = round((datetime.now(timezone.utc) - datetime.fromisoformat(first)).total_seconds() / 86400, 2)
        background = {'first_worker_run_at': first, 'last_worker_run_at': last, 'observed_days': observed,
                      'push_to_existing_chat_threads': 'unsupported: proactive items wait in the local outbox and '
                                                       'are offered at the next conversation'}
        return {'preferences': self.preferences(), 'source_status': status,
                'record_counts': counts, 'observation_catalog': metrics,
                'context_index': context, 'recent': recent, 'pending': self.pending_insights(),
                'background': background,
                'change_watermark': change_seq, 'index_complete': False,
                'continuation': 'Use context_search / context_read / context_query / context_read_original for broader '
                                'evidence; this index is a map, not the boundary of investigation.',
                'boundaries': ['No record without a committed receipt.',
                               'No original attachment without its bytes and verified checksum.',
                               'Oura external MCP content is not authorized for local persistence.',
                               'Silence, missing data or no sync is not a normal measurement.']}

    def status(self) -> dict:
        """Operational metadata only; no health content."""
        with self.connect() as c:
            schema = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            counts = {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                      for t in ('records', 'observations', 'objects', 'insights', 'jobs', 'devices')}
            pending = c.execute("SELECT count(*) FROM insights WHERE state='pending'").fetchone()[0]
            run = c.execute('SELECT started_at, finished_at, outcome, model_calls, error_code FROM worker_runs '
                            'ORDER BY started_at DESC LIMIT 1').fetchone()
            queued = c.execute("SELECT count(*) FROM jobs WHERE state IN('queued','running')").fetchone()[0]
        return {'profile': self.profile, 'root': str(self.root), 'schema_version': schema, 'counts': counts,
                'pending_insights': pending, 'queued_jobs': queued, 'last_worker_run': dict(run) if run else None,
                'sources': [{k: s[k] for k in ('id', 'policy', 'state', 'last_success_at', 'latest_sample_at',
                                               'active_observations')} for s in self.source_status()['sources']]}
