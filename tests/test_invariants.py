"""Store invariants against reference oracles; all fixture content is synthetic."""
import hashlib
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phctx import download, migrations
from phctx.store import Store, StoreError, instant
from phctx.tools import ToolContext, Tools

AT = '2026-09-20T12:00:00-05:00'
LIVE = 'apple_health:SYNTHETIC-install'
EXPORT = 'apple_health_export'
KEY = 'SYNTHETIC-origin-key'


def oid(source, native):
    return 'obs_' + hashlib.sha256((source + '\0' + native).encode()).hexdigest()


def hour(h):
    return f'2026-09-{1 + h // 24:02d}T{h % 24:02d}:00:00+00:00'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.s = Store(self.base / 'store', 'synthetic')
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def rid(self):
        self.n += 1
        return f'SYNTHETIC-request-{self.n}'

    def batch(self, source, samples=(), deleted=(), store=None):
        return (store or self.s).ingest_batch(request_id=self.rid(), source_id=source, samples=list(samples),
                                              deleted_ids=list(deleted), cursor='SYNTHETIC cursor')

    def rows(self, sql, args=(), store=None):
        with (store or self.s).connect() as c:
            return [tuple(r) for r in c.execute(sql, args)]


class CatalogOracleTests(Base):
    """F01: observation_catalog and sources.latest_sample_at equal their GROUP BY ground truth after every batch."""

    def check(self, sources):
        truth = self.rows("SELECT source_id, metric, coalesce(unit,''), count(*), min(start_at), max(end_at) "
                          'FROM observations WHERE deleted=0 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3')
        self.assertEqual(self.rows('SELECT * FROM observation_catalog ORDER BY 1, 2, 3'), truth)
        for src in sources:
            want = self.rows('SELECT max(end_at) FROM observations WHERE source_id=? AND deleted=0', (src,))[0][0]
            got = self.rows('SELECT latest_sample_at FROM sources WHERE id=?', (src,))[0][0]
            self.assertEqual(got, want, src)

    def test_sole_row_endpoint_shrink_recomputes_catalog_and_latest(self):
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        one = dict(native_id='a', metric='SYNTHETIC.m', start_at=hour(1), end_at=hour(9), unit='u', value_num=1.0)
        self.batch('synthetic:watch', [one])
        self.batch('synthetic:watch', [{**one, 'start_at': hour(3), 'end_at': hour(5)}])
        self.assertEqual(self.rows('SELECT n, first_at, last_at FROM observation_catalog'),
                         [(1, instant(hour(3)), instant(hour(5)))])
        self.check(['synthetic:watch'])

    def test_randomized_upserts_deletes_and_group_moves_match_ground_truth(self):
        sources = ['synthetic:watch', 'synthetic:phone']
        for src in sources:
            self.s.register_source(src, 'SYNTHETIC ' + src)
        for seed in range(4):
            rnd = random.Random(seed)
            for _ in range(60):
                src = rnd.choice(sources)
                ids = rnd.sample([f'n{seed}-{i}' for i in range(10)], rnd.randint(0, 6))
                cut = rnd.randint(0, len(ids))
                samples = []
                for native in ids[:cut]:
                    a = rnd.randint(0, 40)
                    samples.append(dict(native_id=native, metric=rnd.choice(['SYNTHETIC.a', 'SYNTHETIC.b']),
                                        unit=rnd.choice([None, '', 'u']), start_at=hour(a),
                                        end_at=hour(a + rnd.randint(0, 6)), value_num=float(a)))
                self.batch(src, samples, ids[cut:])
                self.check(sources)


class SupersessionTests(Base):
    """F02/F03: a live copy (or its tombstone) suppresses the export copy regardless of arrival or migration order."""

    def sample(self, native):
        return dict(native_id=native, metric='HKQuantityTypeIdentifierBodyMass', start_at=AT, end_at=AT,
                    unit='kg', value_num=72.5, source_name='SYNTHETIC watch', origin_key=KEY)

    def run_steps(self, steps, store=None):
        store = store or self.s
        store.register_source(LIVE, 'SYNTHETIC HealthKit')
        for step in steps:
            if step == 'export':
                self.batch(EXPORT, [self.sample('x2:SYNTHETIC')], store=store)
            elif step == 'live':
                self.batch(LIVE, [self.sample('SYNTHETIC-uuid')], store=store)
            else:
                self.batch(LIVE, deleted=['SYNTHETIC-uuid'], store=store)
        return {r[0] for r in self.rows('SELECT id FROM canonical_observations', store=store)}

    def fresh(self, name):
        return Store(self.base / name, 'synthetic')

    def test_live_and_export_in_either_order_leave_only_the_live_copy(self):
        orders = [['export', 'live'], ['live', 'export']]
        got = [self.run_steps(o, self.fresh('-'.join(o))) for o in orders]
        self.assertEqual(got, [{oid(LIVE, 'SYNTHETIC-uuid')}] * 2)

    def test_live_tombstone_keeps_export_copy_suppressed_in_every_order(self):
        orders = [['export', 'live', 'delete'], ['live', 'export', 'delete'], ['live', 'delete', 'export']]
        self.assertEqual([self.run_steps(o, self.fresh('-'.join(o))) for o in orders], [set()] * 3)

    def test_v4_database_with_deleted_live_match_migrates_to_same_canonical_set(self):
        root = self.base / 'v4'
        root.mkdir()
        (root / 'PROFILE').write_text('synthetic\n')
        c = sqlite3.connect(root / 'context.sqlite3', isolation_level=None)
        c.executescript((Path(migrations.__file__).with_name('schema.sql')).read_text())
        c.execute('BEGIN')
        for v in (2, 3, 4):
            migrations.STEPS[v](c)
            c.execute('INSERT INTO migrations VALUES(?,?)', (v, AT))
            c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(v),))
        for src in (EXPORT, LIVE):
            c.execute("INSERT INTO sources(id,label,policy) VALUES(?,?,'durable')", (src, 'SYNTHETIC ' + src))
        for src, native, deleted in ((EXPORT, 'x2:SYNTHETIC', 0), (LIVE, 'SYNTHETIC-uuid', 1)):
            c.execute('INSERT INTO observations(id,source_id,native_id,metric,start_at,end_at,timezone,value_num,'
                      "unit,raw_json,deleted,updated_at,origin_key) VALUES(?,?,?,?,?,?,'UTC',72.5,'kg','{}',?,?,?)",
                      (oid(src, native), src, native, 'HKQuantityTypeIdentifierBodyMass', AT, AT, deleted, AT, KEY))
        c.execute('COMMIT')
        c.close()
        migrated = Store(root, 'synthetic')
        got = {r[0] for r in self.rows('SELECT id FROM canonical_observations', store=migrated)}
        self.assertEqual(got, self.run_steps(['live', 'delete', 'export'], self.fresh('ingested')))
        self.assertEqual(got, set())
        # F13: an observations-only database is snapshotted before migrating.
        self.assertEqual(len(list((root / 'migrations').glob('pre-v5-*.sqlite3'))), 1)

    def test_canonical_view_is_readable_through_readonly_query(self):
        self.run_steps(['export', 'live'])
        out = self.s.query_readonly('SELECT count(*) FROM canonical_observations')
        self.assertEqual(out['rows'], [[1]])


class ChangeDetailTests(Base):
    def test_deletion_only_change_names_the_removed_metric(self):
        """F26: a revisit router keyed on metrics must see deletions."""
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        self.batch('synthetic:watch', [dict(native_id='a', metric='SYNTHETIC.weight', start_at=AT, value_num=1.0)])
        self.batch('synthetic:watch', deleted=['a'])
        import json
        detail = json.loads(self.rows("SELECT detail FROM changes WHERE entity='observations' ORDER BY seq DESC")[0][0])
        self.assertEqual(detail['metrics'], ['SYNTHETIC.weight'])


class EvidenceBindingTests(Base):
    """F11: a candidate built from what the model read is rejected when that evidence changed before commit."""

    def candidate(self, obs, versions):
        return dict(decision='surface', topic='SYNTHETIC', why_now='SYNTHETIC', what_changed='SYNTHETIC',
                    unknowns='SYNTHETIC', next_step='SYNTHETIC', evidence_ids=[obs], evidence_versions=versions,
                    source_policies=['durable'])

    def test_changed_observation_between_read_and_commit_is_stale_evidence(self):
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        sample = dict(native_id='a', metric='SYNTHETIC.m', start_at=AT, value_num=1.0)
        self.batch('synthetic:watch', [sample])
        obs = oid('synthetic:watch', 'a')
        with self.s.transaction() as c:
            c.execute("UPDATE observations SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (obs,))
        v1 = self.s.read_versions([obs])
        self.assertEqual(v1, {obs: '2000-01-01T00:00:00+00:00'})
        self.batch('synthetic:watch', [{**sample, 'value_num': 2.0}])
        with self.assertRaises(StoreError) as caught:
            self.s.queue_insight(request_id=self.rid(), candidate=self.candidate(obs, v1))
        self.assertEqual(caught.exception.code, 'stale_evidence')
        self.assertEqual(self.rows('SELECT count(*) FROM insights'), [(0,)])
        self.assertTrue(self.s.queue_insight(request_id=self.rid(),
                                             candidate=self.candidate(obs, self.s.read_versions([obs])))['queued'])

    def test_reads_return_versions_to_echo(self):
        tools = Tools(ToolContext(store=self.s))
        rec = self.s.put_record(request_id=self.rid(), kind='note', text='SYNTHETIC note', occurred_at=AT)['record_id']
        self.assertEqual(tools.call('context_read', {'record_ids': [rec]}).data['versions'], {rec: 'active'})
        sha = self.s.put_attachment_bytes(request_id=self.rid(), data=b'SYNTHETIC text', filename='s.txt',
                                          mime='text/plain', text='SYNTHETIC file', occurred_at=AT)['object_sha256']
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC page'])
        pages = tools.call('context_read_original', {'object_sha256': sha, 'mode': 'pages'}).data
        self.assertEqual(pages['versions'], {f'obj:{sha}#p1': 'immutable'})
        info = tools.call('context_read_original', {'object_sha256': sha, 'mode': 'info'}).data
        self.assertEqual(info['versions'], {f'obj:{sha}': 'immutable'})


class PostCommitTruthTests(Base):
    def test_errors_after_original_commit_still_return_the_committed_receipt(self):
        """F14: extraction/metadata failures after commit must not deny that the original was saved."""
        data = b'SYNTHETIC captured bytes'
        fetch = lambda url, **kw: download.Downloaded(data, 'localhost', 'text/plain', 0)
        tools = Tools(ToolContext(store=self.s, allowed_download_hosts=['localhost'], fetch=fetch))
        boom = sqlite3.OperationalError('SYNTHETIC disk I/O error')
        with patch('phctx.tools.extract.extract', side_effect=boom), \
                patch.object(self.s, 'object_info', side_effect=boom):
            out = tools.call('context_capture_file', {
                'request_id': 'SYNTHETIC-post-commit', 'text': 'SYNTHETIC file', 'occurred_at': AT,
                'file': {'download_url': 'https://localhost/download', 'file_id': 'SYNTHETIC-file-id',
                         'mime_type': 'text/plain', 'file_name': 'synthetic.txt'}})
        self.assertFalse(out.is_error, out.data)
        self.assertEqual(out.data['status'], 'committed')
        self.assertTrue(out.data['original_saved'])
        self.assertEqual(out.data['object_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(out.data['extraction_status'], 'failed')
        self.assertTrue(out.data['extraction_error'])
        self.assertEqual(self.s.read_object(out.data['object_sha256']), data)
        self.assertEqual(self.s.receipt('SYNTHETIC-post-commit')['record_id'], out.data['record_id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
