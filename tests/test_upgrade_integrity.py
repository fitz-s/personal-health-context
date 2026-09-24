"""Cross-version integrity: rows written under the old key formula must still dedupe after upgrade (v6)."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from phctx import apple_export, ingest, migrations
from phctx.store import Store, StoreError
from phctx.tools import Tools, ToolContext
from phctx.download import Downloaded

HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n'
REC = ('<Record type="HKQuantityTypeIdentifierStepCount" sourceName="SYNTHETIC Watch" unit="count" '
       'startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:10:00 -0500" value="500"/>\n')


def export_zip(path: Path, body: str = REC) -> Path:
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('apple_health_export/export.xml', HEAD + body + '</HealthData>')
    return path


def canonical(s: Store) -> tuple:
    with s.connect() as c:
        return tuple(c.execute("SELECT count(*), sum(value_num) FROM canonical_observations "
                               "WHERE metric='HKQuantityTypeIdentifierStepCount'").fetchone())


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def v5_style_db(self) -> Store:
        """An archive whose export rows carry legacy (pre-normalization) keys, as production had before v6."""
        s = Store(self.base / 'live', 'synthetic')
        apple_export.import_export(s, export_zip(self.base / 'e.zip'), tz='America/Chicago')
        with s.transaction() as c:  # emulate the old formula: keys that no longer match anything new code makes
            c.execute("UPDATE observations SET origin_key='legacy-'||substr(origin_key,1,20) "
                      "WHERE source_id='apple_health_export'")
            c.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>5')
        return s

    def test_live_sample_after_upgrade_supersedes_legacy_export_copy(self):
        self.v5_style_db()
        s = Store(self.base / 'live', 'synthetic')  # reopen: runs v6, recomputes keys
        self.assertEqual(s.status()['schema_version'], str(migrations.TARGET))
        s.register_source('apple_health:dev1', 'live')
        s.ingest_batch(request_id='live1', source_id='apple_health:dev1', deleted_ids=[], cursor='c', samples=[{
            'native_id': 'U1', 'metric': 'HKQuantityTypeIdentifierStepCount', 'start_at': '2026-09-20T08:00:00.345-05:00',
            'end_at': '2026-09-20T08:10:00.120-05:00', 'value_num': 500, 'unit': 'count', 'source_name': 'SYNTHETIC Watch',
            'origin_key': apple_export.origin_key('HKQuantityTypeIdentifierStepCount', '2026-09-20T08:00:00.345-05:00',
                                                  '2026-09-20T08:10:00.120-05:00', 500, None, 'count',
                                                  'SYNTHETIC Watch')}])
        self.assertEqual(canonical(s), (1, 500.0))

    def test_historical_supersession_survives_the_rekey(self):
        s = self.v5_style_db()  # export row with a legacy key
        with s.transaction() as c:
            k1 = c.execute("SELECT origin_key FROM observations WHERE source_id='apple_health_export'").fetchone()[0]
            # v5 history: a live row once carried K1 (superseding the export copy), then was updated to another key
            c.execute("INSERT OR IGNORE INTO sources(id,label,policy,state) VALUES('apple_health:dev1','live','durable','ready')")
            c.execute("INSERT INTO observations(id,source_id,native_id,metric,start_at,end_at,timezone,value_num,"
                      "raw_json,deleted,updated_at,origin_key) VALUES('obs_live','apple_health:dev1','U1',"
                      "'HKQuantityTypeIdentifierStepCount','2026-09-20T14:00:00.000000+00:00',"
                      "'2026-09-20T14:10:00.000000+00:00','America/Chicago',777,'{}',0,'t','K2-other')")
            c.execute("INSERT INTO supersessions VALUES(?, 'obs_live')", (k1,))
        self.assertEqual(canonical(s), (1, 777.0))  # before: export copy hidden
        s = Store(self.base / 'live', 'synthetic')  # v6 re-keys everything
        self.assertEqual(canonical(s), (1, 777.0))  # after: still hidden, not resurrected

    def test_since_reimport_after_upgrade_does_not_duplicate_legacy_rows(self):
        s = Store(self.base / 'live', 'synthetic')
        z = export_zip(self.base / 'e.zip')
        sha = hashlib.sha256(z.read_bytes()).hexdigest()
        # a pre-v4 archive: one legacy x: row for this sample (no import tag, old key) from a done import run
        s.ingest_batch(request_id='legacy', source_id=apple_export.SOURCE, deleted_ids=[], cursor='old', samples=[{
            'native_id': 'x:' + 'a' * 40, 'metric': 'HKQuantityTypeIdentifierStepCount', 'unit': 'count',
            'start_at': '2026-09-20T08:00:00-05:00', 'end_at': '2026-09-20T08:10:00-05:00', 'value_num': 500.0,
            'source_name': 'SYNTHETIC Watch', 'origin_key': 'legacy-key'}])
        with s.transaction() as c:
            c.execute("INSERT INTO import_runs VALUES('imp_old','apple_export',?,'t',NULL,'done','{}')", (sha,))
            c.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>5')
        s = Store(self.base / 'live', 'synthetic')  # v6: recompute keys, attribute legacy rows to this export
        apple_export.import_export(s, z, tz='America/Chicago', since='2026-09-01')
        self.assertEqual(canonical(s), (1, 500.0))

    def test_reimport_with_other_timezone_is_not_an_idempotency_conflict(self):
        s = Store(self.base / 'live', 'synthetic')
        body = REC.replace('/>', '><MetadataEntry key="X" value="1"/></Record>', 1).replace(
            '<Record ', '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="10" durationUnit="min" '
            'sourceName="SYNTHETIC Watch" startDate="2026-09-20 07:00:00 -0500" endDate="2026-09-20 07:10:00 -0500">'
            '<WorkoutRoute sourceName="SYNTHETIC Watch"><FileReference path="/workout-routes/route_x.gpx"/></WorkoutRoute>'
            '</Workout>\n<Record ', 1)
        z = self.base / 'e.zip'
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('apple_health_export/export.xml', HEAD + body + '</HealthData>')
            zf.writestr('apple_health_export/workout-routes/route_x.gpx', '<gpx>SYNTHETIC</gpx>')
        apple_export.import_export(s, z, tz='America/Chicago')
        apple_export.import_export(s, z, tz='Europe/Berlin')
        with s.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM active_records WHERE kind='attachment'").fetchone()[0], 1)

    def test_reimports_record_each_original_and_profile_once_and_v7_chains_old_duplicates(self):
        me = '<Me HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"/>\n'
        z = self.base / 'att.zip'
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('apple_health_export/export.xml', HEAD + me + REC + '</HealthData>')
            zf.writestr('apple_health_export/electrocardiograms/ecg_1.csv', 'Name,SYNTHETIC\n\nx\n')
        s = Store(self.base / 'live', 'synthetic')
        apple_export.import_export(s, z, tz='America/Chicago')
        z2 = self.base / 'att2.zip'  # a later export (different archive hash) carrying the same original and profile
        with zipfile.ZipFile(z, 'r') as a, zipfile.ZipFile(z2, 'w') as b:
            for n in a.namelist():
                b.writestr(n, a.read(n) + (b'<!-- later -->' if n.endswith('export.xml') else b''))
        apple_export.import_export(s, z2, tz='America/Chicago')

        def active():
            with s.connect() as c:
                return sorted(r[0] for r in c.execute("SELECT kind FROM active_records WHERE source_id='user'"))
        self.assertEqual(active(), ['attachment', 'note'])
        # An archive written by older request-id formats: the same original/profile recorded twice more.
        with s.transaction() as c:
            for rid, in c.execute("SELECT id FROM records WHERE source_id='user'").fetchall():
                for i in range(2):
                    c.execute("INSERT INTO records SELECT ?||?, kind, occurred_at, timezone, text, payload_json, source_id, "
                              "NULL, object_sha, NULL, created_at FROM records WHERE id=?", (rid, f'dup{i}', rid))
            c.execute('UPDATE records SET source_key=NULL')
            c.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>6')
        s = Store(self.base / 'live', 'synthetic')
        self.assertEqual(active(), ['attachment', 'note'])
        with s.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM records").fetchone()[0], 6)  # nothing deleted
        apple_export.import_export(s, z, tz='America/Chicago')
        self.assertEqual(active(), ['attachment', 'note'])

    def test_observation_reads_refuse_while_an_import_is_unfinished_and_imports_serialize(self):
        s = Store(self.base / 'live', 'synthetic')
        apple_export.import_export(s, export_zip(self.base / 'e.zip'), tz='America/Chicago')
        q = "SELECT count(*) FROM canonical_observations"
        self.assertEqual(s.query_readonly(q)['rows'], [[1]])
        real = s.ingest_batch

        def crash_after_first_page(**kw):  # a new-parser page commits, then the process dies before retirement
            real(**kw)
            raise KeyboardInterrupt
        from unittest import mock
        e2 = export_zip(self.base / 'e2.zip', REC + REC.replace('500', '501'))
        with mock.patch.object(s, 'ingest_batch', side_effect=crash_after_first_page):
            with self.assertRaises(KeyboardInterrupt):
                apple_export.import_export(s, e2, tz='America/Chicago')
        for sql in (q, 'SELECT n FROM observation_catalog', 'SELECT count(*) FROM active_observations'):
            with self.assertRaises(StoreError) as e:
                s.query_readonly(sql)
            self.assertEqual(e.exception.code, 'import_in_progress')
        self.assertEqual(s.query_readonly("SELECT count(*) FROM records")['rows'][0][0] >= 0, True)  # records still work
        self.assertIn('observation_reads', s.source_status())
        with self.assertRaises(StoreError) as e:  # a different import cannot start over the unfinished one
            apple_export.import_export(s, export_zip(self.base / 'e3.zip', REC.replace('500', '502')),
                                       tz='America/Chicago')
        self.assertEqual(e.exception.code, 'import_in_progress')
        apple_export.import_export(s, e2, tz='America/Chicago')  # re-running the same export reconciles and reopens
        self.assertEqual(s.query_readonly(q)['rows'], [[2]])
        self.assertNotIn('observation_reads', s.source_status())

    def test_legacy_shadow_insights_leave_the_outbox_on_upgrade(self):
        s = Store(self.base / 'live', 'synthetic')
        q = s.put_record(request_id='q', kind='question', text='SYNTHETIC q', occurred_at='2026-01-01T00:00:00Z')
        with s.transaction() as c:
            c.execute("INSERT INTO insights(id,fingerprint,question_id,payload_json,state,evidence_versions_json,"
                      "created_at) VALUES('ins_old','fp',?,?, 'pending','{}','2026-09-01T00:00:00Z')",
                      (q['record_id'], json.dumps({'decision': 'surface', 'shadow': True})))
            c.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>5')
        s = Store(self.base / 'live', 'synthetic')
        self.assertEqual(s.pending_insights()['insights'], [])
        with s.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM shadow_insights WHERE id='ins_old'").fetchone()[0], 1)
            self.assertIsNone(c.execute('PRAGMA table_info(model_calls)').fetchall()[8][3] or None)


class ReplayTruthTests(unittest.TestCase):
    def test_replay_with_missing_original_is_an_error_not_a_saved_claim(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / 'x', 'synthetic')
            t = Tools(ToolContext(store=s, allowed_download_hosts=['h.test'],
                                  fetch=lambda u, **k: Downloaded(b'SYNTHETIC bytes', 'h.test', 'text/plain', 0)))
            args = {'request_id': 'r1', 'file': {'download_url': 'https://h.test/a', 'file_id': 'f1'},
                    'text': 'SYNTHETIC', 'occurred_at': '2026-09-23T09:00:00-05:00'}
            first = t.call('context_capture_file', args)
            self.assertFalse(first.is_error)
            (s.blobs / first.data['object_sha256']).unlink()
            again = t.call('context_capture_file', args)
            self.assertTrue(again.is_error)
            self.assertEqual(again.data['error'], 'object_missing')


if __name__ == '__main__':
    unittest.main()
