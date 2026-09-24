"""Forward-only schema migrations. Each step runs inside the caller's IMMEDIATE transaction."""
from __future__ import annotations

import sqlite3

TARGET = 4


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


STEPS = {2: _v2, 3: _v3, 4: _v4}


def apply(c: sqlite3.Connection, current: int, now: str) -> int:
    for version in range(current + 1, TARGET + 1):
        STEPS[version](c)
        c.execute('INSERT INTO migrations VALUES(?,?)', (version, now))
        c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
    return TARGET
