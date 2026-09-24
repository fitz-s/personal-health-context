"""Synthetic, offline tests; no vendor records, network, model, or real medical facts."""
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from phctx.store import Store, StoreError, instant

AT = '2026-09-22T20:30:00-05:00'

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.s = Store(self.base / 'live')
        self.seq = 0
    def tearDown(self):
        self.tmp.cleanup()
    def rid(self):
        self.seq += 1
        return 'request-' + str(self.seq)
    def record(self, **kw):
        body = dict(request_id=self.rid(), kind='event', text='SYNTHETIC meal', occurred_at=AT)
        body.update(kw)
        return self.s.put_record(**body)
    def sample(self, native_id='sample-1', **kw):
        x = dict(native_id=native_id, metric='test.sleep_hours', start_at=AT,
                 end_at=AT, timezone='America/Chicago', value_num=7.0, unit='h', synthetic=True)
        x.update(kw)
        return x
    def ingest(self, **kw):
        self.s.register_source('synthetic:watch', 'Synthetic watch')
        x = dict(request_id=self.rid(), source_id='synthetic:watch', samples=[self.sample()],
                 deleted_ids=[], cursor='cursor-1')
        x.update(kw)
        return self.s.ingest_batch(**x)
    def candidate(self, **kw):
        ref = self.record()['record_id']
        x = dict(decision='surface', why_now='New comparable evidence.', what_changed='SYNTHETIC assessment added.',
                 unknowns='Cause not established.', next_step='Review the matched assessment.',
                 evidence_ids=[ref], topic='test')
        x.update(kw)
        return x
    def expect_error(self, code, fn, *args, **kw):
        with self.assertRaises(StoreError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.code, code)
    def test_empty_bootstrap(self):
        b = self.s.bootstrap()
        self.assertFalse(b['index_complete'])
        self.assertEqual(b['pending']['insights'], [])
    def test_reopen_preserves_record(self):
        r = self.record()
        self.assertEqual(len(Store(self.s.root).get_records([r['record_id']])['records']), 1)
    def test_idempotent_same_body(self):
        a = dict(request_id='same', text='SYNTHETIC', kind='note', occurred_at=AT)
        self.assertEqual(self.s.put_record(**a), self.s.put_record(**a))
        self.assertEqual(len(self.s.search()['records']), 1)
    def test_idempotency_conflict(self):
        self.record(request_id='same')
        self.expect_error('idempotency_conflict', self.record, request_id='same', text='different')
    def test_concurrent_same_request(self):
        def write(_):
            return self.s.put_record(request_id='concurrent', text='SYNTHETIC', kind='event', occurred_at=AT)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            rows = list(ex.map(write, range(24)))
        self.assertEqual(len({r['record_id'] for r in rows}), 1)
        self.assertEqual(len(self.s.search()['records']), 1)
    def test_revision_has_history(self):
        a = self.record()
        b = self.record(supersedes=a['record_id'], text='Corrected SYNTHETIC event')
        self.assertEqual([x['id'] for x in self.s.search()['records']], [b['record_id']])
        self.assertEqual(len(self.s.get_records([a['record_id'], b['record_id']])['records']), 2)
    def test_revision_conflict(self):
        a = self.record()
        self.record(supersedes=a['record_id'])
        self.expect_error('revision_conflict', self.record, supersedes=a['record_id'])
    def test_revision_kind_mismatch(self):
        a = self.record()
        self.expect_error('revision_conflict', self.record, supersedes=a['record_id'], kind='analysis')
    def test_missing_evidence_rollback(self):
        self.expect_error('missing_evidence', self.record, evidence_ids=['missing'])
        self.assertEqual(self.s.search()['records'], [])
    def test_source_key_duplicate(self):
        self.record(source_key='external-key')
        self.expect_error('record_conflict', self.record, source_key='external-key')
    def test_timezone_offset_required(self):
        self.expect_error('invalid_time', self.record, occurred_at='2026-09-22T20:30:00')
    def test_invalid_timezone(self):
        self.expect_error('invalid_timezone', self.record, timezone_name='nowhere/nope')
    def test_instant_canonical(self):
        self.assertEqual(instant(AT), instant('2026-09-23T01:30:00Z'))
    def test_chinese_substring(self):
        self.record(text='合成：过去两个月训练与姿势评估')
        self.assertEqual(len(self.s.search(query='姿势')['records']), 1)
    def test_search_escapes_wildcards(self):
        self.record(text='SYNTHETIC: 50% and foo_bar')
        self.record(text='SYNTHETIC: nothing')
        self.assertEqual(len(self.s.search(query='%')['records']), 1)
        self.assertEqual(len(self.s.search(query='_')['records']), 1)
    def test_search_pagination(self):
        for _ in range(7):
            self.record()
        found, after = [], ''
        while True:
            page = self.s.search(limit=2, after_id=after)
            found += [x['id'] for x in page['records']]
            if not page['has_more']:
                break
            after = page['next_after_id']
        self.assertEqual(len(found), 7)
        self.assertEqual(len(set(found)), 7)
    def test_limit_reject_bool(self):
        self.expect_error('invalid_limit', self.s.search, limit=True)
    def test_readonly_aggregate(self):
        self.ingest()
        r = self.s.query_readonly('SELECT count(*), avg(value_num) FROM active_observations')
        self.assertEqual(r['rows'], [[1, 7.0]])
    def test_readonly_parameterized(self):
        self.record(text="SYNTHETIC: O'Reilly")
        r = self.s.query_readonly('SELECT count(*) FROM active_records WHERE text=?', ["SYNTHETIC: O'Reilly"])
        self.assertEqual(r['rows'], [[1]])
    def test_sql_write_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly, "DELETE FROM records")
    def test_sql_pragma_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly, 'PRAGMA database_list')
    def test_sql_attach_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly, "ATTACH DATABASE ':memory:' AS other")
    def test_sql_secret_tables_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly, 'SELECT * FROM receipts')
    def test_sql_extension_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly, "SELECT load_extension('evil')")
    def test_sql_multiple_statements_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly, 'SELECT 1; DROP TABLE records')
    def test_sql_recursive_query_blocked(self):
        self.expect_error('query_rejected', self.s.query_readonly,
                          'WITH RECURSIVE x(a) AS (SELECT 1 UNION ALL SELECT a+1 FROM x) SELECT * FROM x')
    def test_sql_result_truncation_explicit(self):
        for _ in range(3):
            self.record()
        r = self.s.query_readonly('SELECT id FROM records', limit=2)
        self.assertTrue(r['truncated'])
        self.assertEqual(len(r['rows']), 2)
    def test_attachment_bytes_roundtrip(self):
        data = b'SYNTHETIC original\x00\xff'
        r = self.s.put_attachment_bytes(request_id='file', data=data, filename='example.bin',
                                       mime='application/octet-stream', text='Synthetic original', occurred_at=AT)
        self.assertEqual(r['object_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.s.read_object(r['object_sha256']), data)
        self.assertEqual(r['extraction_status'], 'pending')
    def test_attachment_repeat(self):
        args = dict(request_id='file', data=b'SYNTHETIC', filename='sample.txt', mime='text/plain',
                    text='Synthetic', occurred_at=AT)
        self.assertEqual(self.s.put_attachment_bytes(**args), self.s.put_attachment_bytes(**args))
    def test_attachment_path_escape(self):
        self.expect_error('invalid_filename', self.s.put_attachment_bytes, request_id='bad',
                          data=b'x', filename='../secret', mime='text/plain', text='test', occurred_at=AT)
    def test_attachment_empty(self):
        self.expect_error('file_size', self.s.put_attachment_bytes, request_id='bad',
                          data=b'', filename='a', mime='text/plain', text='test', occurred_at=AT)
    def test_attachment_corruption_detected(self):
        r = self.s.put_attachment_bytes(request_id='file', data=b'SYNTHETIC', filename='s.txt',
                                       mime='text/plain', text='test', occurred_at=AT)
        (self.s.blobs / r['object_sha256']).write_bytes(b'BROKEN')
        self.expect_error('object_corrupt', self.s.read_object, r['object_sha256'])
    def test_opaque_file_id_not_original(self):
        self.expect_error('invalid_object_id', self.s.read_object, 'file_claim_only')
    def test_oura_is_a_durable_source(self):
        self.assertEqual(self.ingest(source_id='oura')['status'], 'committed')
    def test_batch_replay_idempotent(self):
        r = self.ingest(request_id='batch')
        self.assertEqual(r, self.ingest(request_id='batch'))
    def test_batch_updates_existing_uuid(self):
        self.ingest()
        self.ingest(samples=[self.sample(value_num=8)])
        r = self.s.query_readonly('SELECT count(*), max(value_num) FROM active_observations')
        self.assertEqual(r['rows'], [[1, 8.0]])
    def test_delete_native_sample(self):
        self.ingest()
        self.ingest(samples=[], deleted_ids=['sample-1'])
        r = self.s.query_readonly('SELECT count(*) FROM active_observations')
        self.assertEqual(r['rows'], [[0]])
    def test_delete_unseen_sample(self):
        self.ingest(samples=[], deleted_ids=['unseen'])
        self.assertEqual(self.s.query_readonly('SELECT count(*) FROM active_observations')['rows'], [[0]])
    def test_boolean_measurement_rejected(self):
        self.expect_error('invalid_value', self.ingest, samples=[self.sample(value_num=True)])
    def test_nonfinite_measurement_rejected(self):
        self.expect_error('invalid_value', self.ingest, samples=[self.sample(value_num=float('nan'))])
    def test_batch_duplicate_id_rejected(self):
        self.expect_error('duplicate_in_batch', self.ingest, samples=[self.sample(), self.sample()])
    def test_batch_upsert_and_delete_rejected(self):
        self.expect_error('ambiguous_batch', self.ingest, deleted_ids=['sample-1'])
    def test_batch_invalid_interval(self):
        self.expect_error('invalid_interval', self.ingest, samples=[self.sample(end_at='2020-01-01T00:00:00Z')])
    def test_cursor_advances_only_with_commit(self):
        self.ingest(cursor='good')
        self.expect_error('missing_evidence', self.record, evidence_ids=['no-such-evidence'])
        src = next(x for x in self.s.source_status()['sources'] if x['id'] == 'synthetic:watch')
        self.assertEqual(src['cursor'], 'good')
    def test_silence_is_valid_and_empty(self):
        x = self.s.queue_insight(request_id='silent', candidate={'decision': 'silence'})
        self.assertFalse(x['queued'])
        self.assertEqual(self.s.pending_insights()['insights'], [])
    def test_off_preference_persists(self):
        self.s.set_preference(request_id='pref', key='proactivity', value='off')
        self.assertEqual(Store(self.s.root).preferences()['proactivity'], 'off')
        x = self.s.queue_insight(request_id='candidate', candidate=self.candidate())
        self.assertEqual(x['reason'], 'preference_off')
    def test_valid_candidate_queued(self):
        r = self.s.queue_insight(request_id='candidate', candidate=self.candidate())
        self.assertTrue(r['queued'])
        self.assertEqual(len(self.s.pending_insights()['insights']), 1)
    def test_candidate_deduplicates_rephrasing(self):
        c = self.candidate()
        self.s.queue_insight(request_id='first', candidate=c)
        c['why_now'] = 'A different wording, same evidence.'
        r = self.s.queue_insight(request_id='second', candidate=c)
        self.assertEqual(r['reason'], 'duplicate')
    def test_attention_budget_not_daily_report(self):
        self.s.queue_insight(request_id='first', candidate=self.candidate())
        r = self.s.queue_insight(request_id='second', candidate=self.candidate(topic='different'))
        self.assertEqual(r['reason'], 'attention_budget')
    def test_superseded_evidence_not_surfaced(self):
        c = self.candidate()
        self.s.queue_insight(request_id='first', candidate=c)
        self.record(supersedes=c['evidence_ids'][0])
        self.assertEqual(self.s.pending_insights()['insights'], [])
    def test_question_reference_must_exist(self):
        c = self.candidate(question_id='missing')
        self.expect_error('missing_question', self.s.queue_insight, request_id='c', candidate=c)
    def test_acknowledge_removes_pending(self):
        r = self.s.queue_insight(request_id='first', candidate=self.candidate())
        self.s.ack_insight(request_id='ack', insight_id=r['insight_id'])
        self.assertEqual(self.s.pending_insights()['insights'], [])
    def test_backup_restore_in_new_directory(self):
        a = self.record()
        r = self.s.put_attachment_bytes(request_id='file', data=b'SYNTHETIC', filename='s.txt',
                                       mime='text/plain', text='test', occurred_at=AT)
        snap = self.base / 'snapshot'
        self.assertFalse(self.s.backup(snap)['cloud_synced'])
        restored = Store.restore(snap, self.base / 'restored')
        self.assertEqual(len(restored.get_records([a['record_id']])['records']), 1)
        self.assertEqual(restored.read_object(r['object_sha256']), b'SYNTHETIC')
    def test_backup_refuses_to_fill_the_disk(self):
        import collections
        from unittest.mock import patch
        tight = collections.namedtuple('usage', 'total used free')(100 * 2**30, 99 * 2**30, 4 * 2**30)
        with patch('phctx.store.shutil.disk_usage', return_value=tight):
            self.expect_error('backup_no_space', self.s.backup, self.base / 'snapshot')
        self.assertFalse((self.base / 'snapshot').exists())
    def test_backup_inside_live_rejected(self):
        self.expect_error('invalid_backup_path', self.s.backup, self.s.root / 'backup')
    def test_restore_existing_root_rejected(self):
        snap = self.base / 'snapshot'
        self.s.backup(snap)
        self.expect_error('invalid_restore_path', Store.restore, snap, self.s.root)
    def test_restore_corrupt_snapshot_rejected(self):
        snap = self.base / 'snapshot'
        self.s.backup(snap)
        (snap / 'context.sqlite3').write_bytes(b'BROKEN')
        self.expect_error('backup_invalid', Store.restore, snap, self.base / 'restored')
    def test_restore_without_manifest_rejected(self):
        snap = self.base / 'snapshot'
        snap.mkdir()
        self.expect_error('backup_invalid', Store.restore, snap, self.base / 'restored')
    def test_failed_batch_rolls_back_values_and_cursor(self):
        self.ingest(cursor='before')
        self.expect_error('invalid_argument', self.ingest, samples=[self.sample(value_num=99)],
                          deleted_ids=[''], cursor='must-not-commit')
        self.assertEqual(self.s.query_readonly('SELECT value_num FROM active_observations')['rows'], [[7.0]])
        src = next(x for x in self.s.source_status()['sources'] if x['id']=='synthetic:watch')
        self.assertEqual(src['cursor'], 'before')
    def test_superseded_question_invalidates_pending(self):
        q = self.record(kind='question')['record_id']
        self.s.queue_insight(request_id='candidate', candidate=self.candidate(question_id=q))
        self.record(kind='question', supersedes=q, text='SYNTHETIC revised question')
        self.assertEqual(self.s.pending_insights()['insights'], [])
    def test_query_incremental_output_cap(self):
        self.expect_error('result_too_large', self.s.query_readonly,
                          "SELECT replace(replace(?, 'a', ?), 'b', ?) FROM sources",
                          ['a'*1000, 'b'*100, 'c'*4])
    def test_query_expression_memory_limit(self):
        self.expect_error('query_rejected', self.s.query_readonly,
                          "SELECT replace(replace(?, 'a', ?), 'b', ?)",
                          ['a'*1000, 'b'*1000, 'c'*10])
    def test_restore_manifest_path_traversal_rejected(self):
        snap=self.base/'snapshot';self.s.backup(snap)
        m=json.loads((snap/'backup_manifest.json').read_text())
        m['sha256']['../outside.txt']='0'*64
        (snap/'backup_manifest.json').write_text(json.dumps(m))
        self.expect_error('backup_invalid', Store.restore, snap, self.base/'restored')
    def test_restore_missing_referenced_blob_rejected(self):
        r=self.s.put_attachment_bytes(request_id='file',data=b'SYNTHETIC',filename='s.txt',
                                     mime='text/plain',text='test',occurred_at=AT)
        snap=self.base/'snapshot';self.s.backup(snap)
        m=json.loads((snap/'backup_manifest.json').read_text())
        del m['sha256']['objects/'+r['object_sha256']]
        (snap/'backup_manifest.json').write_text(json.dumps(m))
        self.expect_error('backup_invalid', Store.restore, snap, self.base/'restored')
    def test_attachment_missing_blob_not_claimed(self):
        r=self.s.put_attachment_bytes(request_id='file',data=b'SYNTHETIC',filename='s.txt',
                                     mime='text/plain',text='test',occurred_at=AT)
        (self.s.blobs/r['object_sha256']).unlink()
        self.expect_error('object_missing', self.s.read_object, r['object_sha256'])
    def test_quiet_blocks_within_week_but_normal_allows_after_three_days(self):
        import datetime
        self.s.queue_insight(request_id='first', candidate=self.candidate())
        past=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=4)).isoformat(timespec='microseconds')
        with self.s.connect() as c:c.execute('UPDATE insights SET created_at=?',(past,))
        self.s.set_preference(request_id='quiet',key='proactivity',value='quiet')
        r=self.s.queue_insight(request_id='quiet-new', candidate=self.candidate(topic='second'))
        self.assertEqual(r['reason'],'attention_budget')
        self.s.set_preference(request_id='normal',key='proactivity',value='normal')
        r=self.s.queue_insight(request_id='normal-new', candidate=self.candidate(topic='third'))
        self.assertTrue(r['queued'])
    def test_database_permissions(self):
        self.assertEqual(os.stat(self.s.db).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.s.root).st_mode & 0o777, 0o700)

if __name__ == '__main__':
    unittest.main(verbosity=2)
