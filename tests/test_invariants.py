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



class RepeatRecordTests(Base):
    """R2-06: an export row that repeats an earlier source record is kept but hidden from canonical_observations."""

    def row(self, nid, created, value=5.0, device='SYNTHETIC watch'):
        return dict(native_id=nid, metric='SYNTHETIC.steps', start_at=hour(1), end_at=hour(2), value_num=value,
                    unit='count', source_name='SYNTHETIC', device={'raw': device}, origin_key=KEY,
                    metadata={'creation_date': created})

    def counts(self):
        return (self.rows('SELECT count(*), sum(value_num) FROM canonical_observations')[0],
                self.rows('SELECT count(*) FROM canonical_observations_raw')[0][0])

    def test_repeats_are_hidden_distinct_rows_are_not_and_deletion_unhides(self):
        self.batch(EXPORT, [self.row('x2:a', 't1'), self.row('x2:b', 't2')])  # same sample written twice
        self.assertEqual(self.counts(), ((1, 5.0), 2))
        self.batch(EXPORT, [self.row('x2:c', 't3', device='SYNTHETIC phone')])  # same key, other device: distinct
        self.assertEqual(self.counts(), ((2, 10.0), 3))
        first = min(oid(EXPORT, 'x2:a'), oid(EXPORT, 'x2:b'))
        self.batch(EXPORT, deleted=['x2:a' if first == oid(EXPORT, 'x2:a') else 'x2:b'])  # the kept copy goes away
        self.assertEqual(self.counts(), ((2, 10.0), 2))  # its repeat now stands in for it

    def test_moving_the_representative_to_another_key_unhides_its_old_peer(self):
        a, b = sorted(['x2:a', 'x2:b'], key=lambda n: oid(EXPORT, n))  # a is the representative (smaller id)
        self.batch(EXPORT, [self.row(a, 't1'), self.row(b, 't2')])
        self.assertEqual(self.counts(), ((1, 5.0), 2))
        moved = dict(self.row(a, 't1'), origin_key='SYNTHETIC-other-key', start_at=hour(5), end_at=hour(6))
        self.batch(EXPORT, [moved])
        self.assertEqual(self.counts(), ((2, 10.0), 2))

    def test_timezone_or_other_metadata_differences_are_not_repeats(self):
        base = self.row('x2:a', 't1')
        self.batch(EXPORT, [base, dict(self.row('x2:b', 't2'), timezone='Asia/Tokyo'),
                            dict(self.row('x2:c', 't3'), metadata={'creation_date': 't3', 'HKMotionContext': '1'})])
        self.assertEqual(self.counts(), ((3, 15.0), 3))

    def test_a_marked_repeat_moved_to_no_key_is_visible_again(self):
        a, b = sorted(['x2:a', 'x2:b'], key=lambda n: oid(EXPORT, n))
        self.batch(EXPORT, [self.row(a, 't1'), self.row(b, 't2')])  # b repeats a
        self.batch(EXPORT, [dict(self.row(b, 't2'), origin_key=None)])
        self.assertEqual(self.rows('SELECT repeat_of FROM observations WHERE id=?', (oid(EXPORT, b),)), [(None,)])
        self.assertEqual(self.counts(), ((2, 10.0), 2))

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
        page_v = 'text:' + hashlib.sha256(b'SYNTHETIC page').hexdigest()
        self.assertEqual(pages['versions'], {f'obj:{sha}#p1': page_v})
        info = tools.call('context_read_original', {'object_sha256': sha, 'mode': 'info'}).data
        self.assertEqual(info['versions'], {f'obj:{sha}': 'immutable'})

    def test_re_extracted_page_is_a_new_version(self):
        sha = self.s.put_attachment_bytes(request_id=self.rid(), data=b'SYNTHETIC scan', filename='s.txt',
                                          mime='text/plain', text='SYNTHETIC file', occurred_at=AT)['object_sha256']
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC first reading'])
        ref = f'obj:{sha}#p1'
        v1 = self.s.read_versions([ref])
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC corrected reading'])
        self.assertNotEqual(self.s.read_versions([ref]), v1)



class ReadReceiptTests(Base):
    """R3-01/02/09/10: a derived write is bound to the reads that returned its evidence; its dependency is kept."""

    def setUp(self):
        super().setUp()
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        self.t = Tools(ToolContext(store=self.s))

    def obs(self, nid, h, v):
        self.batch('synthetic:watch', [dict(native_id=nid, metric='SYNTHETIC.m', start_at=hour(h), end_at=hour(h),
                                            value_num=v)])

    def analysis(self, receipts, ids, kind='analysis'):
        args = {'request_id': self.rid(), 'kind': kind, 'text': 'SYNTHETIC daily mean', 'occurred_at': AT,
                'evidence_ids': ids}
        if receipts is not None:
            args['read_receipts'] = receipts
        return self.t.call('context_capture', args)

    def query(self, sql='SELECT id, value_num FROM canonical_observations'):
        return self.t.call('context_query', {'sql': sql}).data

    def test_a_disjoint_new_member_of_the_aggregate_makes_it_stale(self):
        self.obs('a', 9, 10.0)
        read = self.query()  # daily mean over one row = 10
        self.obs('b', 11, 30.0)  # disjoint interval, same day: the mean is now 20
        out = self.analysis([read['read_receipt']], [])
        self.assertEqual(out.data['error'], 'stale_evidence')

    def test_deleting_a_non_cited_member_makes_it_stale(self):
        self.obs('a', 9, 10.0)
        self.obs('b', 11, 30.0)
        read = self.query()
        self.batch('synthetic:watch', deleted=['b'])
        self.assertEqual(self.analysis([read['read_receipt']], []).data['error'], 'stale_evidence')

    def test_a_saved_analysis_turns_stale_when_its_population_changes_later(self):
        self.obs('a', 9, 10.0)
        read = self.query()
        rid = self.analysis([read['read_receipt']], []).data['record_id']
        self.assertTrue(self.s.get_records([rid])['records'][0]['evidence']['current'])
        self.obs('b', 11, 30.0)
        ev = self.s.get_records([rid])['records'][0]['evidence']
        self.assertEqual((ev['current'], ev['stale_ids']), (False, ['observations']))

    def test_derived_writes_need_a_receipt_that_returned_the_evidence(self):
        self.obs('a', 9, 10.0)
        read = self.query()
        unrelated = self.query('SELECT 1')['read_receipt']
        for receipts, code in ((None, 'evidence_unbound'), ([unrelated], 'evidence_unbound'),
                               ([read['read_receipt']], 'evidence_unbound'),  # a query delivers no individual row
                               (['rr_' + '0' * 32], 'evidence_unbound')):
            self.assertEqual(self.analysis(receipts, [read['rows'][0][0]]).data['error'], code, receipts)
        self.assertEqual(self.analysis(None, []).data['error'], 'evidence_unbound')  # an analysis always binds
        self.assertFalse(self.analysis([read['read_receipt']], []).is_error)

    def test_ids_a_record_only_mentions_do_not_bind(self):
        self.obs('a', 9, 10.0)
        a = oid('synthetic:watch', 'a')
        note = self.s.put_record(request_id=self.rid(), kind='note', text='SYNTHETIC', occurred_at=AT,
                                 payload={'cites': [a]})['record_id']
        read = self.t.call('context_read', {'record_ids': [note]}).data  # returns the note, not observation a
        self.assertEqual(self.analysis([read['read_receipt']], [a]).data['error'], 'evidence_unbound')
        self.assertFalse(self.analysis([read['read_receipt']], [note], kind='note').is_error)

    def test_a_query_that_also_reads_records_cannot_certify(self):
        """R6-02: a record revision changes the result without an observation batch, so it stays unverified."""
        self.obs('a', 9, 10.0)
        rec = self.s.put_record(request_id=self.rid(), kind='note', text='SYNTHETIC v1', occurred_at=AT)['record_id']
        read = self.t.call('context_query', {'sql': 'SELECT r.text, count(o.id) FROM active_records r, observations o '
                                                    'WHERE r.id = ?', 'parameters': [rec]}).data
        self.assertEqual(self.ev(self.analysis([read['read_receipt']], [])), (False, False))
        pure = self.query('SELECT avg(value_num) FROM canonical_observations')  # observation-only: still certifies
        self.assertEqual(self.ev(self.analysis([pure['read_receipt']], [])), (True, True))

    def test_a_query_cell_never_delivers_an_id(self):
        """R5-01: table access says nothing about where a cell came from."""
        self.obs('a', 9, 10.0)
        self.obs('b', 11, 30.0)
        a, b = oid('synthetic:watch', 'a'), oid('synthetic:watch', 'b')
        q = self.s.put_record(request_id=self.rid(), kind='note', text='SYNTHETIC q', occurred_at=AT)['record_id']
        p = self.s.put_record(request_id=self.rid(), kind='note', text=q, occurred_at=AT)['record_id']
        for sql, params, cited, kind in (
                ('SELECT ? FROM observations WHERE id = ?', [b, a], b, 'analysis'),
                ("SELECT avg(value_num), group_concat(id, ',') FROM canonical_observations", [], a, 'analysis'),
                ('SELECT text FROM records WHERE id = ?', [p], q, 'note')):
            read = self.t.call('context_query', {'sql': sql, 'parameters': params}).data
            self.assertEqual(self.analysis([read['read_receipt']], [cited], kind=kind).data['error'],
                             'evidence_unbound', sql)

    def test_an_evidence_free_analysis_cannot_verify_its_child(self):
        """R5-02: an analysis whose reads certified nothing stays unverified through inheritance."""
        parent = self.analysis([self.query('SELECT 1')['read_receipt']], []).data['record_id']
        read = self.t.call('context_read', {'record_ids': [parent]}).data
        child = self.analysis([read['read_receipt']], [parent])
        self.assertEqual(self.ev(child), (False, False))
        read = self.t.call('context_read', {'record_ids': [child.data['record_id']]}).data
        self.assertEqual(self.ev(self.analysis([read['read_receipt']], [child.data['record_id']])), (False, False))

    def test_page_text_binds_the_page_not_the_original(self):
        """R5-03: re-extraction must reach an analysis of the page text."""
        sha = self.s.put_attachment_bytes(request_id=self.rid(), data=b'SYNTHETIC scan', filename='s.txt',
                                          mime='text/plain', text='SYNTHETIC file', occurred_at=AT)['object_sha256']
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC first'])
        pages = self.t.call('context_read_original', {'object_sha256': sha, 'mode': 'pages'}).data
        self.assertEqual(self.analysis([pages['read_receipt']], [f'obj:{sha}']).data['error'], 'evidence_unbound')
        out = self.analysis([pages['read_receipt']], [f'obj:{sha}#p1'])
        self.assertEqual(self.ev(out), (True, True))
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC corrected'])
        self.assertFalse(self.s.get_records([out.data['record_id']])['records'][0]['evidence']['current'])

    def ev(self, out):
        self.assertFalse(out.is_error, out.data)
        ev = self.s.get_records([out.data['record_id']])['records'][0]['evidence']
        return ev['bound'], ev['current']

    def test_a_constant_read_cannot_certify_an_analysis(self):
        self.assertEqual(self.ev(self.analysis([self.query('SELECT 1')['read_receipt']], [])), (False, False))
        self.obs('a', 9, 10.0)  # a real aggregate needs no enumerated ids
        self.assertEqual(self.ev(self.analysis([self.query('SELECT avg(value_num) FROM canonical_observations')
                                                ['read_receipt']], [])), (True, True))

    def child_of_parent(self):
        self.obs('a', 9, 10.0)
        parent = self.analysis([self.query()['read_receipt']], []).data['record_id']
        read = self.t.call('context_read', {'record_ids': [parent]}).data
        return parent, self.analysis([read['read_receipt']], [parent])

    def test_a_child_analysis_inherits_its_parents_population(self):
        parent, child = self.child_of_parent()
        self.assertEqual(self.ev(child), (True, True))
        self.obs('b', 11, 30.0)
        got = {r['id']: r['evidence'] for r in self.s.get_records([parent, child.data['record_id']])['records']}
        self.assertFalse(got[parent]['current'])
        self.assertEqual((got[child.data['record_id']]['current'], got[child.data['record_id']]['stale_ids']),
                         (False, [parent]))

    def test_a_stale_parent_cannot_be_cited_again(self):
        parent, _ = self.child_of_parent()
        self.obs('b', 11, 30.0)
        read = self.t.call('context_read', {'record_ids': [parent]}).data  # read after the change: the read is fresh
        self.assertEqual(self.analysis([read['read_receipt']], [parent]).data['error'], 'stale_evidence')

    def test_citing_an_unverified_derivation_leaves_the_child_unverified(self):
        self.obs('a', 9, 10.0)
        legacy = self.s.put_record(request_id=self.rid(), kind='analysis', text='SYNTHETIC legacy', occurred_at=AT,
                                   evidence_ids=[oid('synthetic:watch', 'a')])['record_id']  # no receipt
        read = self.t.call('context_read', {'record_ids': [legacy]}).data
        self.assertEqual(self.ev(self.analysis([read['read_receipt']], [legacy])), (False, False))

    def test_bootstrap_cannot_certify_an_analysis(self):
        """R7-02: bootstrap returns source status, preferences and the record index alongside the catalog; not all of
        it is tracked, so an analysis of it is saved unverified (cite the underlying reads instead)."""
        self.obs('a', 9, 10.0)
        boot = self.t.call('context_bootstrap', {}).data
        self.assertEqual(self.ev(self.analysis([boot['read_receipt']], [])), (False, False))

    def test_a_query_of_source_state_cannot_certify(self):
        """R7-02: source state changes (a failed attempt) without an observation batch."""
        self.obs('a', 9, 10.0)
        read = self.query("SELECT state FROM sources WHERE id = 'synthetic:watch'")
        self.assertEqual(self.ev(self.analysis([read['read_receipt']], [])), (False, False))

    def test_a_page_returned_without_text_is_not_delivered(self):
        """R7-03"""
        sha = self.s.put_attachment_bytes(request_id=self.rid(), data=b'SYNTHETIC scan', filename='s.txt',
                                          mime='text/plain', text='SYNTHETIC file', occurred_at=AT)['object_sha256']
        self.s.set_extraction(sha, status='done', method='fixture', pages=['A' * 60000, 'SYNTHETIC page two'])
        out = self.s.read_pages(sha, 1, 2)
        self.assertEqual(([p['page'] for p in out['pages']], out['continue_from_page']), ([1], 2))
        self.assertEqual(out['evidence_ids'], [f'obj:{sha}#p1'])
        read = self.t.call('context_read_original', {'object_sha256': sha, 'mode': 'pages', 'start_page': 1,
                                                     'end_page': 2}).data
        self.assertEqual(self.analysis([read['read_receipt']], [f'obj:{sha}#p2']).data['error'], 'evidence_unbound')
        again = self.t.call('context_read_original', {'object_sha256': sha, 'mode': 'pages', 'start_page': 2}).data
        self.assertEqual(self.ev(self.analysis([again['read_receipt']], [f'obj:{sha}#p2'])), (True, True))
        partial = self.s.read_pages(sha, 1, 2, max_chars=59990)  # page 1 only partly returned
        self.assertEqual(([p['page'] for p in partial['pages']], partial['evidence_ids'], partial['continue_from_page']),
                         ([1], [], 1))

    def test_one_receipt_can_support_several_analyses(self):
        self.obs('a', 9, 10.0)
        read = self.query()
        for _ in range(2):
            self.assertEqual(self.ev(self.analysis([read['read_receipt']], [])), (True, True))

    def test_returned_original_bytes_are_delivered_evidence(self):
        sha = self.s.put_attachment_bytes(request_id=self.rid(), data=b'SYNTHETIC scan', filename='s.txt',
                                          mime='text/plain', text='SYNTHETIC file', occurred_at=AT)['object_sha256']
        info = self.t.call('context_read_original', {'object_sha256': sha, 'mode': 'info'}).data
        self.assertEqual(self.analysis([info['read_receipt']], [f'obj:{sha}']).data['error'], 'evidence_unbound')
        raw = self.t.call('context_read_original', {'object_sha256': sha, 'mode': 'file'}).data
        self.assertEqual(self.ev(self.analysis([raw['read_receipt']], [f'obj:{sha}'])), (True, True))

    def test_primary_facts_need_no_read(self):
        out = self.analysis(None, [], kind='event')
        self.assertFalse(out.is_error, out.data)
        self.assertNotIn('evidence', self.s.get_records([out.data['record_id']])['records'][0])

    def test_record_evidence_is_stale_when_a_newer_record_supersedes_it_after_the_read(self):
        rec = self.s.put_record(request_id=self.rid(), kind='note', text='SYNTHETIC v1', occurred_at=AT)['record_id']
        read = self.t.call('context_read', {'record_ids': [rec]}).data
        self.s.put_record(request_id=self.rid(), kind='note', text='SYNTHETIC v2', occurred_at=AT, supersedes=rec)
        out = self.analysis([read['read_receipt']], [rec], kind='note')
        self.assertIn(out.data['error'], {'stale_evidence', 'missing_evidence'})

    def test_re_extracted_page_after_the_read_is_refused(self):
        sha = self.s.put_attachment_bytes(request_id=self.rid(), data=b'SYNTHETIC scan', filename='s.txt',
                                          mime='text/plain', text='SYNTHETIC file', occurred_at=AT)['object_sha256']
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC first'])
        read = self.t.call('context_read_original', {'object_sha256': sha, 'mode': 'pages'}).data
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC corrected'])
        self.assertEqual(self.analysis([read['read_receipt']], [f'obj:{sha}#p1']).data['error'], 'stale_evidence')

    def test_every_read_tool_returns_a_receipt_and_writes_do_not(self):
        for name, args in (('context_bootstrap', {}), ('context_search', {}), ('context_query', {'sql': 'SELECT 1'})):
            self.assertTrue(self.t.call(name, args).data['read_receipt'].startswith('rr_'), name)
        out = self.analysis(None, [], kind='note')
        self.assertNotIn('read_receipt', out.data)

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
