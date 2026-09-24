PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, policy TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'not_connected', last_attempt_at TEXT,
 last_success_at TEXT, latest_sample_at TEXT, cursor TEXT,
 coverage_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS objects(
 sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64), size INTEGER NOT NULL,
 mime TEXT NOT NULL, filename TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
 timezone TEXT NOT NULL, text TEXT NOT NULL, payload_json TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id), source_key TEXT,
 object_sha TEXT REFERENCES objects(sha256), supersedes TEXT REFERENCES records(id),
 created_at TEXT NOT NULL,
 UNIQUE(source_id,source_key), UNIQUE(supersedes)
);
CREATE INDEX IF NOT EXISTS records_time ON records(occurred_at,kind);
CREATE INDEX IF NOT EXISTS records_source ON records(source_id,source_key);
CREATE TABLE IF NOT EXISTS evidence_links(
 record_id TEXT NOT NULL REFERENCES records(id), evidence_id TEXT NOT NULL REFERENCES records(id),
 PRIMARY KEY(record_id,evidence_id)
);
CREATE TABLE IF NOT EXISTS observations(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), native_id TEXT NOT NULL,
 metric TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL, timezone TEXT NOT NULL,
 value_num REAL, value_text TEXT, unit TEXT, raw_json TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN(0,1)), updated_at TEXT NOT NULL,
 UNIQUE(source_id,native_id)
);
CREATE INDEX IF NOT EXISTS obs_lookup ON observations(metric,source_id,start_at);
CREATE TABLE IF NOT EXISTS receipts(
 request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, result_json TEXT NOT NULL, committed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS insights(
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, question_id TEXT REFERENCES records(id),
 payload_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN('pending','delivered','dismissed')),
 created_at TEXT NOT NULL, delivered_at TEXT
);
CREATE VIEW IF NOT EXISTS active_records AS
 SELECT r.* FROM records r WHERE NOT EXISTS(SELECT 1 FROM records newer WHERE newer.supersedes=r.id);
CREATE VIEW IF NOT EXISTS active_observations AS SELECT * FROM observations WHERE deleted=0;
