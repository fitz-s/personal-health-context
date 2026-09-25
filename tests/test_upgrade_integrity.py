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

    def test_imports_are_exclusive_and_only_the_same_signature_may_resume(self):
        import fcntl
        s = Store(self.base / 'live', 'synthetic')
        e = export_zip(self.base / 'e.zip', REC + REC.replace('500', '501'))
        with open(s.root / 'import.lock', 'a') as held:  # another process is importing right now
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(StoreError) as err:
                apple_export.import_export(s, e, tz='America/Chicago')
            self.assertEqual(err.exception.code, 'import_in_progress')
        from unittest import mock
        real = s.ingest_batch
        with mock.patch.object(s, 'ingest_batch', side_effect=lambda **kw: (real(**kw), (_ for _ in ()).throw(KeyboardInterrupt))):
            with self.assertRaises(KeyboardInterrupt):
                apple_export.import_export(s, e, tz='America/Chicago')  # a full import dies mid-way
        for other in ({'since': '2026-09-21', 'tz': 'America/Chicago'}, {'tz': 'Asia/Tokyo'}):
            with self.assertRaises(StoreError) as err:  # a narrower or differently-zoned run cannot clear it
                apple_export.import_export(s, e, **other)
            self.assertEqual(err.exception.code, 'import_in_progress')
        apple_export.import_export(s, e, tz='America/Chicago')  # the exact same run reconciles
        self.assertEqual(s.query_readonly('SELECT count(*) FROM canonical_observations')['rows'], [[2]])

    def test_gate_and_query_share_one_snapshot(self):
        s = Store(self.base / 'live', 'synthetic')
        apple_export.import_export(s, export_zip(self.base / 'e.zip'), tz='America/Chicago')
        armed = []

        class Reader(sqlite3.Connection):  # a writer commits right after the reader's gate check
            def execute(self, sql, *a):
                out = super().execute(sql, *a)
                if "key='import_in_progress'" in sql and not armed:
                    armed.append(1)
                    w = sqlite3.connect(s.db)
                    w.execute("INSERT INTO meta VALUES('import_in_progress', '{}')")
                    w.execute("UPDATE observations SET value_num=value_num+1000")
                    w.commit()
                    w.close()
                return out
        from unittest import mock
        real_connect = sqlite3.connect
        with mock.patch('phctx.store.sqlite3.connect', lambda *a, **k: real_connect(*a, factory=Reader, **k)):
            rows = s.query_readonly('SELECT sum(value_num) FROM canonical_observations')['rows']
        self.assertTrue(armed)
        self.assertEqual(rows, [[500.0]])  # the snapshot from before the writer, never the mixed state

    def test_bootstrap_and_status_withhold_observation_numbers_during_an_import(self):
        s = Store(self.base / 'live', 'synthetic')
        apple_export.import_export(s, export_zip(self.base / 'e.zip'), tz='America/Chicago')
        self.assertTrue(s.bootstrap()['observation_catalog'])
        with s.transaction() as c:
            c.execute("INSERT INTO meta VALUES('import_in_progress', '{}')")
        b = s.bootstrap()
        self.assertEqual(b['observation_catalog'], [])
        src = next(x for x in b['source_status']['sources'] if x['id'] == apple_export.SOURCE)
        self.assertEqual((src['active_observations'], src['latest_sample_at']), (None, None))
        with self.assertRaises(StoreError) as err:
            s.query_readonly('SELECT latest_sample_at FROM sources')
        self.assertEqual(err.exception.code, 'import_in_progress')

    def test_profile_a_then_b_then_a_ends_with_a_active_and_keeps_every_revision(self):
        s = Store(self.base / 'live', 'synthetic')

        def export(sex, n):
            z = self.base / f'p{n}.zip'
            with zipfile.ZipFile(z, 'w') as zf:
                zf.writestr('apple_health_export/export.xml', HEAD + f'<Me HKCharacteristicTypeIdentifierBiologicalSex='
                            f'"{sex}"/>\n' + REC + f'<!-- {n} --></HealthData>')
            apple_export.import_export(s, z, tz='America/Chicago')
        for n, sex in enumerate(('A', 'B', 'A', 'A')):
            export(sex, n)
        with s.connect() as c:
            active = [json.loads(p)['apple_health_characteristics'] for (p,) in c.execute(
                "SELECT payload_json FROM active_records WHERE source_key LIKE 'apple-export:profile:%'")]
            total = c.execute("SELECT count(*) FROM records WHERE source_key LIKE 'apple-export:profile:%'").fetchone()[0]
        self.assertEqual(active, [{'BiologicalSex': 'A'}])
        self.assertEqual(total, 3)  # A, B, A again; the repeated A is not a fourth

    def test_v11_unverifies_certifications_the_new_binding_rules_cannot_reproduce(self):
        s = Store(self.base / 'live', 'synthetic')
        s.register_source('synthetic:watch', 'SYNTHETIC watch')
        s.ingest_batch(request_id='SYNTHETIC-b', source_id='synthetic:watch', cursor='c', deleted_ids=[], samples=[
            dict(native_id='a', metric='SYNTHETIC.m', start_at='2026-09-01T00:00:00+00:00', value_num=1.0)])
        obs = 'obs_' + hashlib.sha256(b'synthetic:watch\0a').hexdigest()
        page = s.put_attachment_bytes(request_id='SYNTHETIC-o', data=b'SYNTHETIC scan', filename='s.txt',
                                      mime='text/plain', text='SYNTHETIC', occurred_at='2026-09-20T12:00:00-05:00')
        s.set_extraction(page['object_sha256'], status='done', method='fixture', pages=['SYNTHETIC p1'])
        kept = {}
        for name, ev in (('obs', [obs]), ('original', [f"obj:{page['object_sha256']}"]),
                         ('page', [f"obj:{page['object_sha256']}#p1"]), ('aggregate', [])):
            rid = s.put_record(request_id=f'SYNTHETIC-{name}', kind='analysis', text='SYNTHETIC', evidence_ids=ev,
                               occurred_at='2026-09-20T12:00:00-05:00')['record_id']
            with s.transaction() as c:  # a dependency the v10 rules stored
                c.execute('INSERT INTO record_dependencies(record_id,seq,observations,policy) VALUES(?, 0, 1, 14)', (rid,))
            kept[name] = rid
        with s.transaction() as c:
            c.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>10')
        s = Store(self.base / 'live', 'synthetic')
        got = {r['id']: r['evidence']['bound'] for r in s.get_records(list(kept.values()))['records']}
        # v11 unverifies the observation/original citations; v13 then unverifies every certification made before v11
        # (none can show which read delivered it), so all four read unverified after the full upgrade.
        self.assertEqual({n: got[r] for n, r in kept.items()},
                         {'obs': False, 'original': False, 'page': False, 'aggregate': False})

    def test_v12_makes_the_oura_placeholder_a_durable_source(self):
        s = Store(self.base / 'live', 'synthetic')
        with s.transaction() as c:
            c.execute("UPDATE sources SET policy='ephemeral', label='Oura official MCP' WHERE id='oura'")
            c.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>11')
        s = Store(self.base / 'live', 'synthetic')
        with s.connect() as c:
            self.assertEqual(tuple(c.execute("SELECT policy, label FROM sources WHERE id='oura'").fetchone()),
                             ('durable', 'Oura API v2'))

    def test_concurrent_openers_take_one_pre_migration_snapshot(self):
        import subprocess
        import sys
        s = Store(self.base / 'live', 'synthetic')
        s.put_record(request_id='SYNTHETIC-r', kind='note', text='SYNTHETIC', occurred_at='2026-09-20T12:00:00-05:00')
        with s.transaction() as c:
            c.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>11')
        with s.transaction() as c:  # enough pages that a snapshot takes long enough for openers to overlap
            c.execute('CREATE TABLE ballast(x BLOB)')
            c.executemany('INSERT INTO ballast VALUES(randomblob(4096))', [()] * 40000)
        # Each opener names its snapshot with its own clock (same-second names would hide duplicates).
        code = ("import os, time as t\nfrom phctx import store\n"
                "store.time = type('T', (), {'time': staticmethod(lambda: t.time() + os.getpid() * 1000),"
                " 'monotonic': staticmethod(t.monotonic), 'sleep': staticmethod(t.sleep)})\n"
                f"store.Store({str(self.base / 'live')!r}, 'synthetic')")
        procs = [subprocess.Popen([sys.executable, '-c', code], env={'PYTHONPATH': 'src', 'PATH': '/usr/bin:/bin'})
                 for _ in range(4)]
        self.assertEqual([p.wait(60) for p in procs], [0] * 4)
        self.assertEqual(len(list((self.base / 'live' / 'migrations').glob('pre-v12-*.sqlite3'))), 1)

    def test_v13_voids_old_receipts_and_keeps_post_v11_certifications(self):
        """R6-01: a receipt issued under the old rules cannot bind after the upgrade."""
        s = Store(self.base / 'live', 'synthetic')
        old = s.issue_receipt({'obs_' + 'a' * 64}, True, 0)
        with s.transaction() as c:
            c.execute("UPDATE meta SET value='12' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>12')
        s = Store(self.base / 'live', 'synthetic')
        with s.connect() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM read_receipts WHERE id=?', (old,)).fetchone())

    def test_v14_unverifies_certifications_made_before_v13(self):
        """R7-01: a certification bound under rules without receipt completeness reads unverified after upgrade."""
        s = Store(self.base / 'live', 'synthetic')
        rid = s.put_record(request_id='SYNTHETIC-a', kind='analysis', text='SYNTHETIC mixed', evidence_ids=[],
                           occurred_at='2026-09-20T12:00:00-05:00')['record_id']
        with s.transaction() as c:  # a dependency stored by v11/v12 (its mixed query tracked observations only)
            c.execute('INSERT INTO record_dependencies(record_id,seq,observations,policy) VALUES(?, 0, 1, 14)', (rid,))
            c.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>=13')
            c.execute("INSERT INTO migrations VALUES(13, '9999-01-01T00:00:00+00:00')")  # v13 applied after it
            c.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
        s = Store(self.base / 'live', 'synthetic')
        self.assertFalse(s.get_records([rid])['records'][0]['evidence']['bound'])

    def seq(self, s):
        with s.connect() as c:
            return c.execute('SELECT coalesce(max(seq), 0) FROM changes').fetchone()[0]

    def downgrade_to_v14(self, s):
        with s.transaction() as c:
            c.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>14')
            c.execute('UPDATE read_receipts SET policy=0')
            c.execute('UPDATE record_dependencies SET policy=14')
        return Store(self.base / 'live', 'synthetic')

    def test_v15_retires_certifications_and_receipts_issued_under_older_rules(self):
        """R8-01: a v13-era certification (e.g. from a source-state query marked complete) reads unverified after the
        upgrade, and its surviving receipt cannot certify a new write."""
        s = Store(self.base / 'live', 'synthetic')
        t = Tools(ToolContext(store=s))
        receipt = s.issue_receipt(set(), True, self.seq(s))  # as v13 issued it for SELECT state FROM sources: complete
        rid = t.call('context_capture', {'request_id': 'SYNTHETIC-a', 'kind': 'analysis', 'text': 'SYNTHETIC',
                                         'occurred_at': '2026-09-20T12:00:00-05:00',
                                         'read_receipts': [receipt]}).data['record_id']
        self.assertTrue(s.get_records([rid])['records'][0]['evidence']['bound'])
        s = self.downgrade_to_v14(s)
        self.assertFalse(s.get_records([rid])['records'][0]['evidence']['bound'])
        again = Tools(ToolContext(store=s)).call('context_capture', {
            'request_id': 'SYNTHETIC-b', 'kind': 'analysis', 'text': 'SYNTHETIC',
            'occurred_at': '2026-09-20T12:00:00-05:00', 'read_receipts': [receipt]})
        self.assertEqual(again.data['error'], 'evidence_unbound')

    def test_a_revoked_derived_note_never_becomes_a_primary_fact(self):
        """R8-02: a note written from an aggregate keeps its derived status when its certification is retired, so a
        child citing it is unverified, not current."""
        s = Store(self.base / 'live', 'synthetic')
        t = Tools(ToolContext(store=s))
        note = t.call('context_capture', {'request_id': 'SYNTHETIC-n', 'kind': 'note', 'text': 'SYNTHETIC mean 10',
                                          'occurred_at': '2026-09-20T12:00:00-05:00',
                                          'read_receipts': [s.issue_receipt(set(), True, self.seq(s))]}).data['record_id']
        s = self.downgrade_to_v14(s)
        t = Tools(ToolContext(store=s))
        self.assertIn('evidence', s.get_records([note])['records'][0])  # still derived, not a primary fact
        read = t.call('context_read', {'record_ids': [note]}).data
        child = t.call('context_capture', {'request_id': 'SYNTHETIC-c', 'kind': 'analysis', 'text': 'SYNTHETIC',
                                           'occurred_at': '2026-09-20T12:00:00-05:00', 'evidence_ids': [note],
                                           'read_receipts': [read['read_receipt']]}).data['record_id']
        self.assertFalse(s.get_records([child])['records'][0]['evidence']['bound'])

    def test_an_actual_v14_layout_upgrades_and_retires_its_certifications(self):
        """R9-03: the real v14 shape (three-column dependencies, no receipt policy column), not current tables relabelled."""
        s = Store(self.base / 'live', 'synthetic')
        t = Tools(ToolContext(store=s))
        receipt = s.issue_receipt(set(), True, self.seq(s))
        rid = t.call('context_capture', {'request_id': 'SYNTHETIC-a', 'kind': 'note', 'text': 'SYNTHETIC',
                                         'occurred_at': '2026-09-20T12:00:00-05:00',
                                         'read_receipts': [receipt]}).data['record_id']
        with s.transaction() as c:
            c.execute('CREATE TABLE deps14(record_id TEXT PRIMARY KEY REFERENCES records(id), seq INTEGER NOT NULL, '
                      'observations INTEGER NOT NULL CHECK(observations IN(0,1)))')
            c.execute('INSERT INTO deps14 SELECT record_id, seq, observations FROM record_dependencies')
            c.execute('DROP TABLE record_dependencies')
            c.execute('ALTER TABLE deps14 RENAME TO record_dependencies')
            c.execute('ALTER TABLE read_receipts DROP COLUMN policy')
            c.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
            c.execute('DELETE FROM migrations WHERE version>14')
        s = Store(self.base / 'live', 'synthetic')
        ev = s.get_records([rid])['records'][0]['evidence']
        self.assertEqual((ev['bound'], ev['current']), (False, False))  # still derived, certification retired
        again = Tools(ToolContext(store=s)).call('context_capture', {
            'request_id': 'SYNTHETIC-b', 'kind': 'analysis', 'text': 'SYNTHETIC',
            'occurred_at': '2026-09-20T12:00:00-05:00', 'read_receipts': [receipt]})
        self.assertEqual(again.data['error'], 'evidence_unbound')

    def test_a_note_derived_without_certification_is_not_primary(self):
        """R8-02: a note written with a receipt that certified nothing (SELECT 1) reads unverified, not primary."""
        s = Store(self.base / 'live', 'synthetic')
        t = Tools(ToolContext(store=s))
        const = t.call('context_query', {'sql': 'SELECT 1'}).data['read_receipt']
        note = t.call('context_capture', {'request_id': 'SYNTHETIC-n', 'kind': 'note', 'text': 'SYNTHETIC',
                                          'occurred_at': '2026-09-20T12:00:00-05:00',
                                          'read_receipts': [const]}).data['record_id']
        ev = s.get_records([note])['records'][0]['evidence']
        self.assertEqual((ev['bound'], ev['current']), (False, False))

    def test_an_older_program_waiting_on_the_lock_refuses_a_newer_schema(self):
        """R6-08: the schema is re-read after the lock; one migrated past this program's target is refused."""
        import fcntl
        import threading
        import time
        s = Store(self.base / 'live', 'synthetic')
        with s.transaction() as c:  # this program sees an older schema and will wait to migrate it
            c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(migrations.TARGET - 1),))
        lock = open(s.root / 'migrate.lock', 'a')
        fcntl.flock(lock, fcntl.LOCK_EX)  # a newer program holds the lock, then leaves a newer schema
        errors = []
        t = threading.Thread(target=lambda: errors.append(self._open(s.root)))
        t.start()
        time.sleep(0.5)
        with sqlite3.connect(s.root / 'context.sqlite3') as c:
            c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(migrations.TARGET + 1),))
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        t.join(10)
        self.assertEqual(errors, ['schema_mismatch'])

    @staticmethod
    def _open(root):
        try:
            Store(root, 'synthetic')
            return 'opened'
        except StoreError as e:
            return e.code

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
