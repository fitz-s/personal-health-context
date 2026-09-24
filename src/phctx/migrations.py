"""Forward-only schema migrations. Each step runs inside the caller's IMMEDIATE transaction."""
from __future__ import annotations

import hashlib
import json
import sqlite3

TARGET = 10


def statements(sql: str):
    """Split a script into complete statements (trigger bodies stay whole).

    executescript() would COMMIT the caller's transaction first, so migrations run statement by statement.
    """
    buf = ''
    for part in sql.split(';'):
        buf += part + ';'
        if sqlite3.complete_statement(buf):
            if buf.strip(' \n;'):
                yield buf
            buf = ''
    if buf.strip(' \n;'):
        raise ValueError('incomplete SQL statement')


def run(c: sqlite3.Connection, sql: str) -> None:
    for stmt in statements(sql):
        c.execute(stmt)


def _v2(c: sqlite3.Connection) -> None:
    run(c, '''
CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

ALTER TABLE observations ADD COLUMN source_name TEXT;
ALTER TABLE observations ADD COLUMN bundle_id TEXT;
ALTER TABLE observations ADD COLUMN origin_key TEXT;
CREATE INDEX IF NOT EXISTS obs_origin ON observations(origin_key);
CREATE INDEX IF NOT EXISTS obs_time ON observations(start_at);
CREATE INDEX IF NOT EXISTS obs_source_end ON observations(source_id, deleted, end_at);

CREATE TABLE IF NOT EXISTS evidence_refs(
 record_id TEXT NOT NULL REFERENCES records(id), ref_id TEXT NOT NULL, ref_version TEXT NOT NULL,
 PRIMARY KEY(record_id, ref_id)
);

CREATE TABLE IF NOT EXISTS changes(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS record_change AFTER INSERT ON records BEGIN
 INSERT INTO changes(entity, entity_id, detail, at) VALUES('record', NEW.id, NEW.kind, NEW.created_at);
END;

CREATE TABLE IF NOT EXISTS object_pages(
 object_sha TEXT NOT NULL REFERENCES objects(sha256), page INTEGER NOT NULL CHECK(page >= 1),
 text TEXT NOT NULL, method TEXT NOT NULL, PRIMARY KEY(object_sha, page)
);
CREATE TABLE IF NOT EXISTS extractions(
 object_sha TEXT PRIMARY KEY REFERENCES objects(sha256),
 status TEXT NOT NULL CHECK(status IN('pending','done','partial','failed','not_applicable')),
 method TEXT, page_count INTEGER, error_code TEXT, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, type TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN('queued','running','done','failed','cancelled')),
 payload_json TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
 next_run_at TEXT NOT NULL, lease_owner TEXT, lease_expires_at TEXT,
 last_error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state, next_run_at);
CREATE TABLE IF NOT EXISTS worker_runs(
 id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT,
 changes_seen INTEGER NOT NULL DEFAULT 0, jobs_run INTEGER NOT NULL DEFAULT 0,
 model_calls INTEGER NOT NULL DEFAULT 0, surfaced INTEGER NOT NULL DEFAULT 0, error_code TEXT
);
CREATE TABLE IF NOT EXISTS model_calls(
 id TEXT PRIMARY KEY, job_id TEXT, backend TEXT NOT NULL, model_id TEXT NOT NULL,
 prompt_sha256 TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT,
 cost_usd REAL NOT NULL DEFAULT 0, error_code TEXT
);

CREATE TABLE IF NOT EXISTS devices(
 installation_id TEXT PRIMARY KEY, device_name TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE,
 source_id TEXT NOT NULL REFERENCES sources(id), paired_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS pairing_codes(code_sha256 TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used_at TEXT);
CREATE TABLE IF NOT EXISTS sync_streams(
 installation_id TEXT NOT NULL REFERENCES devices(installation_id), stream TEXT NOT NULL,
 last_sequence INTEGER NOT NULL, last_batch_id TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(installation_id, stream)
);
CREATE TABLE IF NOT EXISTS import_runs(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, input_sha256 TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, counts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE insights_v2(
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, question_id TEXT REFERENCES records(id),
 payload_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('pending','delivered','dismissed','stale','expired')),
 evidence_versions_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 expires_at TEXT, delivered_at TEXT, closed_reason TEXT
);
INSERT INTO insights_v2(id, fingerprint, question_id, payload_json, state, created_at, delivered_at)
 SELECT id, fingerprint, question_id, payload_json, state, created_at, delivered_at FROM insights;
DROP TABLE insights;
ALTER TABLE insights_v2 RENAME TO insights;

DROP VIEW IF EXISTS active_observations;
CREATE VIEW active_observations AS SELECT * FROM observations WHERE deleted=0;
CREATE VIEW canonical_observations AS
 SELECT o.* FROM observations o WHERE o.deleted=0 AND NOT (
  o.source_id='apple_health_export' AND o.origin_key IS NOT NULL AND EXISTS(
   SELECT 1 FROM observations l WHERE l.origin_key=o.origin_key AND l.source_id LIKE 'apple_health:%'));
''')


def _v3(c: sqlite3.Connection) -> None:
    # Full Apple history (~2M rows) made per-metric time-window queries and the bootstrap catalog scan the
    # whole table. Metric/time index serves the model's common queries; the catalog is maintained per source/
    # metric/unit instead of recomputed from every row.
    run(c, '''
CREATE INDEX IF NOT EXISTS obs_metric_time ON observations(metric, start_at);
CREATE TABLE IF NOT EXISTS observation_catalog(
 source_id TEXT NOT NULL, metric TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', n INTEGER NOT NULL,
 first_at TEXT, last_at TEXT, PRIMARY KEY(source_id, metric, unit)
);
INSERT OR REPLACE INTO observation_catalog
 SELECT source_id, metric, coalesce(unit, ''), count(*), min(start_at), max(end_at)
 FROM observations WHERE deleted=0 GROUP BY source_id, metric, coalesce(unit, '');
''')


def _v4(c: sqlite3.Connection) -> None:
    # canonical_observations ran a correlated origin_key probe per backfill row (7 s for one year of steps).
    # Mark superseded backfill rows once, at ingest, instead: `superseded_by_live` is set when a live row with the
    # same origin_key arrives, so the view is a plain indexed filter.
    run(c, '''
ALTER TABLE observations ADD COLUMN superseded_by_live INTEGER NOT NULL DEFAULT 0;
UPDATE observations SET superseded_by_live=1 WHERE source_id='apple_health_export' AND origin_key IN (
 SELECT origin_key FROM observations WHERE source_id LIKE 'apple_health:%' AND origin_key IS NOT NULL AND deleted=0);
DROP VIEW IF EXISTS canonical_observations;
CREATE VIEW canonical_observations AS SELECT * FROM observations WHERE deleted=0 AND superseded_by_live=0;
''')


def _v5(c: sqlite3.Connection) -> None:
    # superseded_by_live was set only when the live row arrived second, and the v4 backfill ignored tombstones.
    # Supersession is now its own fact: once any live row (deleted or not) carried an origin_key, the export copy with
    # that key is never canonical, in either arrival order. superseded_by_live stays as an unused column.
    # Catalog extrema and source latest times left stale by endpoint shrinks are rebuilt once from ground truth.
    # Shadow-mode candidates get their own table so they never reach the outbox, dedup or attention budget.
    run(c, '''
CREATE TABLE supersessions(origin_key TEXT PRIMARY KEY, live_observation_id TEXT NOT NULL);
INSERT OR IGNORE INTO supersessions SELECT origin_key, id FROM observations
 WHERE source_id LIKE 'apple_health:%' AND origin_key IS NOT NULL ORDER BY updated_at, id;
DROP VIEW IF EXISTS canonical_observations;
CREATE VIEW canonical_observations AS SELECT * FROM observations WHERE deleted=0 AND NOT (
 source_id='apple_health_export' AND origin_key IS NOT NULL
 AND origin_key IN (SELECT origin_key FROM supersessions));
CREATE INDEX IF NOT EXISTS obs_group_end ON observations(metric, source_id, end_at);
DELETE FROM observation_catalog;
INSERT INTO observation_catalog
 SELECT source_id, metric, coalesce(unit, ''), count(*), min(start_at), max(end_at)
 FROM observations WHERE deleted=0 GROUP BY source_id, metric, coalesce(unit, '');
UPDATE sources SET latest_sample_at=(SELECT max(end_at) FROM observations o WHERE o.source_id=sources.id AND deleted=0);
CREATE TABLE shadow_insights(
 id TEXT PRIMARY KEY, question_id TEXT, payload_json TEXT NOT NULL, evidence_versions_json TEXT NOT NULL,
 would_queue INTEGER NOT NULL CHECK(would_queue IN(0,1)), reason TEXT, created_at TEXT NOT NULL
);
''')


def _v6(c: sqlite3.Connection) -> None:
    # The equivalence-key formula changed (normalization v1: unit, category strings, workout [type, seconds], whole
    # seconds). A stored key is only meaningful under the formula that made it, so every key is recomputed and
    # supersessions rebuilt from the recomputed live keys (tombstoned live rows keep authority: their stored fields
    # still yield the key). Legacy shadow candidates written into `insights` move to shadow_insights. cost_usd
    # becomes nullable so an unmeasured cost is recorded as unknown, not zero.
    from .apple_export import origin_key_of_row
    # Attribute untagged (pre-v4) export rows to their export: only when every export import this database ever ran
    # was the same archive is the attribution certain; otherwise leave them unattributed (never retired by guess).
    # Every later import tags its rows, so "untagged" names exactly this set from here on.
    shas = [r[0] for r in c.execute("SELECT DISTINCT input_sha256 FROM import_runs WHERE kind='apple_export'")]
    if len(shas) == 1:
        c.execute("INSERT OR REPLACE INTO meta VALUES('export_legacy_owner', ?)", (shas[0][:16],))
    # A supersession is history: the live row that once held a key may since carry another one. Carry every existing
    # relation over to the recomputed key of each export row it suppressed, before the keys change.
    held = c.execute("SELECT o.id, s.live_observation_id FROM supersessions s JOIN observations o "
                     "ON o.origin_key=s.origin_key AND o.source_id='apple_health_export'").fetchall()
    rows = c.execute("SELECT id, metric, start_at, end_at, value_num, value_text, unit, raw_json FROM observations "
                     "WHERE metric != 'tombstone'")
    batch = []
    for oid, metric, start, end, vn, vt, unit, raw in rows:
        batch.append((origin_key_of_row(metric, start, end, vn, vt, unit, raw), oid))
    for i in range(0, len(batch), 20000):
        c.executemany('UPDATE observations SET origin_key=? WHERE id=?', batch[i:i + 20000])
    c.execute('DELETE FROM supersessions')
    c.executemany('INSERT OR IGNORE INTO supersessions SELECT origin_key, ? FROM observations WHERE id=? '
                  'AND origin_key IS NOT NULL', [(live, oid) for oid, live in held])
    run(c, '''
INSERT OR IGNORE INTO supersessions SELECT origin_key, id FROM observations
 WHERE source_id LIKE 'apple_health:%' AND origin_key IS NOT NULL ORDER BY updated_at, id;
INSERT OR IGNORE INTO shadow_insights(id, question_id, payload_json, evidence_versions_json, would_queue, reason, created_at)
 SELECT id, question_id, payload_json, evidence_versions_json, 1, 'migrated_from_insights_v5', created_at
 FROM insights WHERE json_extract(payload_json, '$.shadow') = 1;
DELETE FROM insights WHERE json_extract(payload_json, '$.shadow') = 1;
CREATE TABLE model_calls_v6(
 id TEXT PRIMARY KEY, job_id TEXT, backend TEXT NOT NULL, model_id TEXT NOT NULL,
 prompt_sha256 TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT,
 cost_usd REAL, error_code TEXT
);
INSERT INTO model_calls_v6 SELECT id, job_id, backend, model_id, prompt_sha256, started_at, finished_at, status,
 NULL, error_code FROM model_calls;
DROP TABLE model_calls;
ALTER TABLE model_calls_v6 RENAME TO model_calls;
''')


def _v7(c: sqlite3.Connection) -> None:
    # Importer records had no identity beyond their request_id, so every request-id format change re-inserted the
    # same GPX/ECG attachment and profile note. Duplicates are chained as revisions (oldest → newest; nothing is
    # deleted) and the surviving record gets the source_key the importer now checks.
    from .apple_export import OBJECT_KEY, PROFILE_KEY
    from .store import dump
    groups: dict[str, list[tuple[str, str]]] = {}
    for rid, sha, payload in c.execute(
            "SELECT id, object_sha, payload_json FROM records WHERE source_id='user' AND source_key IS NULL AND "
            "supersedes IS NULL AND json_extract(payload_json, '$.source')='apple_health_export' ORDER BY created_at, id"):
        chars = json.loads(payload).get('apple_health_characteristics')
        if sha:
            key = OBJECT_KEY + sha
        elif chars is not None:
            key = PROFILE_KEY
        else:
            continue
        groups.setdefault(key, []).append((rid, dump(chars) if chars is not None else ''))
    for key, rows in groups.items():
        for (older, _), (newer, _) in zip(rows, rows[1:]):
            c.execute('UPDATE records SET supersedes=? WHERE id=?', (older, newer))
        last, chars = rows[-1]
        if key == PROFILE_KEY:
            key += hashlib.sha256(chars.encode()).hexdigest()[:12]
        c.execute('UPDATE records SET source_key=? WHERE id=?', (key, last))


def _v8(c: sqlite3.Connection) -> None:
    # 1. Page text is derived and can be re-extracted, so a page reference is versioned by its content hash, not
    #    'immutable'. 2. An export row that exactly repeats an earlier source record (the source app wrote the sample
    #    twice: every measured field and the device equal, only creation metadata differs) is marked repeat_of=<that
    #    row's id>; canonical_observations hides it, canonical_observations_raw keeps every permitted source row. The
    #    mark is set at ingest (Store.ingest_batch), so the view stays an indexed filter.
    if 'sha256' not in {r[1] for r in c.execute('PRAGMA table_info(object_pages)')}:
        c.execute('ALTER TABLE object_pages ADD COLUMN sha256 TEXT')
    if 'repeat_of' not in {r[1] for r in c.execute('PRAGMA table_info(observations)')}:
        c.execute('ALTER TABLE observations ADD COLUMN repeat_of TEXT')
    c.execute('CREATE INDEX IF NOT EXISTS obs_repeat ON observations(origin_key) WHERE repeat_of IS NOT NULL')
    run(c, """
DROP VIEW IF EXISTS canonical_observations_raw;
DROP VIEW IF EXISTS canonical_observations;
CREATE VIEW canonical_observations_raw AS SELECT * FROM observations WHERE deleted=0 AND NOT (
 source_id='apple_health_export' AND origin_key IS NOT NULL
 AND origin_key IN (SELECT origin_key FROM supersessions));
CREATE VIEW canonical_observations AS SELECT * FROM canonical_observations_raw WHERE repeat_of IS NULL;
UPDATE observations SET repeat_of = (SELECT d.id FROM observations d INDEXED BY obs_origin
  WHERE d.origin_key=observations.origin_key AND d.id<observations.id AND d.deleted=0
  AND d.source_id=observations.source_id AND d.start_at=observations.start_at AND d.end_at=observations.end_at
  AND d.value_num IS observations.value_num AND d.value_text IS observations.value_text
  AND d.unit IS observations.unit AND d.source_name IS observations.source_name
  AND json_extract(d.raw_json, '$.device') IS json_extract(observations.raw_json, '$.device') ORDER BY d.id LIMIT 1)
 WHERE source_id='apple_health_export' AND deleted=0 AND origin_key IN (
  SELECT origin_key FROM observations WHERE source_id='apple_health_export' AND deleted=0
  GROUP BY origin_key HAVING count(*) > 1);
""")
    for sha, page, text in c.execute('SELECT object_sha, page, text FROM object_pages').fetchall():
        c.execute('UPDATE object_pages SET sha256=? WHERE object_sha=? AND page=?',
                  (hashlib.sha256(text.encode()).hexdigest(), sha, page))


def _v9(c: sqlite3.Connection) -> None:
    # v8 marked repeats on measured columns + device only, which also hid rows differing in timezone or other metadata.
    # The shared signature (store.repeat_signature: every field except creation/import bookkeeping) is strictly
    # narrower, so only rows v8 marked can change: re-resolve just the groups that hold one (one table pass).
    # A change of canonical membership is an observation change.
    from .store import mark_repeats, utcnow
    c.execute('CREATE INDEX IF NOT EXISTS obs_repeat ON observations(origin_key) WHERE repeat_of IS NOT NULL')
    keys = [k for (k,) in c.execute("SELECT DISTINCT origin_key FROM observations INDEXED BY obs_repeat "
                                    "WHERE repeat_of IS NOT NULL")]
    for k in keys:
        mark_repeats(c, 'apple_health_export', k)
    c.execute("INSERT INTO changes(entity, entity_id, detail, at) VALUES('observations', 'apple_health_export', ?, ?)",
              (json.dumps({'repeat_resolution': 'v9', 'groups': len(keys)}), utcnow()))


def _v10(c: sqlite3.Connection) -> None:
    # Evidence authority: a read issues a receipt (the change-log sequence before it ran, the evidence ids it returned,
    # whether it read observation data); a derived write names its receipts and its dependency is kept, so a later read
    # can tell an analysis whose observation population changed after it was written.
    run(c, """
CREATE TABLE IF NOT EXISTS read_receipts(
 id TEXT PRIMARY KEY, seq INTEGER NOT NULL, observations INTEGER NOT NULL CHECK(observations IN(0,1)),
 refs_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_dependencies(
 record_id TEXT PRIMARY KEY REFERENCES records(id), seq INTEGER NOT NULL,
 observations INTEGER NOT NULL CHECK(observations IN(0,1))
);
CREATE INDEX IF NOT EXISTS changes_entity_seq ON changes(entity, seq);
""")


STEPS = {2: _v2, 3: _v3, 4: _v4, 5: _v5, 6: _v6, 7: _v7, 8: _v8, 9: _v9, 10: _v10}


def apply(c: sqlite3.Connection, current: int, now: str) -> int:
    for version in range(current + 1, TARGET + 1):
        STEPS[version](c)
        c.execute('INSERT INTO migrations VALUES(?,?)', (version, now))
        c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
    return TARGET
