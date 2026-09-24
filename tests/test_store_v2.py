"""Production-facing store v2 regression tests; all fixture content is synthetic."""
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phctx import migrations
from phctx.store import Store, StoreError, utcnow

AT = '2026-09-20T12:00:00-05:00'
LEGACY_SCHEMA = Path(__file__).resolve().parents[1] / 'package_ref' / 'implementation_package_v2' / 'reference_core' / 'phctx' / 'schema.sql'


class StoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.s = Store(self.base / 'store', 'synthetic')
        self.seq = 0

    def tearDown(self):
        self.tmp.cleanup()

    def rid(self):
        self.seq += 1
        return f'request-{self.seq}'

    def record(self, **kw):
        args = dict(request_id=self.rid(), kind='event', text='SYNTHETIC event', occurred_at=AT)
        args.update(kw)
        return self.s.put_record(**args)

    def candidate(self, **kw):
        evidence = self.record()['record_id']
        out = dict(decision='surface', topic='SYNTHETIC review', why_now='SYNTHETIC new evidence',
                   what_changed='SYNTHETIC change', unknowns='SYNTHETIC unknown',
                   next_step='SYNTHETIC review', evidence_ids=[evidence], source_policies=['durable'])
        out.update(kw)
        return out

    def expect_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_original_schema_migrates_records_insight_and_snapshots_once(self):
        root = self.base / 'legacy'
        root.mkdir()
        (root / 'PROFILE').write_text('synthetic\n')
        db = root / 'context.sqlite3'
        with sqlite3.connect(db) as c:
            c.executescript(LEGACY_SCHEMA.read_text())
            c.execute("INSERT INTO sources(id,label,policy,state) VALUES('user','Synthetic user source','durable','ready')")
            c.execute('''INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      ('rec_legacy', 'event', '2026-09-20T17:00:00+00:00', 'UTC', 'SYNTHETIC legacy record',
                       '{}', 'user', None, None, None, '2026-09-20T17:00:00+00:00'))
            c.execute('''INSERT INTO insights VALUES(?,?,?,?,?,?,?)''',
                      ('ins_legacy', 'legacy-fingerprint', None,
                       '{"decision":"surface","topic":"SYNTHETIC legacy"}', 'pending',
                       '2026-09-20T17:00:00+00:00', None))
        script = '''
import json, sqlite3, sys
from pathlib import Path
from phctx import migrations
from phctx.store import Store
root = Path(sys.argv[1])
s = Store(root, 'synthetic')
assert s.status()['schema_version'] == str(migrations.TARGET)
assert s.get_records(['rec_legacy'])['records'][0]['text'] == 'SYNTHETIC legacy record'
with s.connect() as c:
    assert c.execute('SELECT id FROM insights').fetchone()[0] == 'ins_legacy'
    assert [v for (v,) in c.execute('SELECT version FROM migrations ORDER BY version')] == list(range(2, migrations.TARGET + 1))
snaps = list((root / 'migrations').glob('pre-v2-*.sqlite3'))
assert len(snaps) == 1
with sqlite3.connect(snaps[0]) as c:
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '1'
    assert c.execute('SELECT id FROM records').fetchone()[0] == 'rec_legacy'
Store(root, 'synthetic')
assert list((root / 'migrations').glob('pre-v2-*.sqlite3')) == snaps
with s.connect() as c:
    assert c.execute('SELECT count(*) FROM migrations').fetchone()[0] == migrations.TARGET - 1
print(json.dumps({'schema_version': s.status()['schema_version'], 'snapshot': str(snaps[0])}))
'''
        try:
            proc = subprocess.run([sys.executable, '-c', script, str(root)], capture_output=True, text=True,
                                  timeout=2, env={**os.environ,
                                                 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
        except subprocess.TimeoutExpired as error:
            self.fail(f'Store migration exceeded 2 seconds instead of completing: {error}')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['schema_version'], str(migrations.TARGET))

    def test_newer_schema_version_is_rejected_without_downgrade(self):
        with self.s.transaction() as c:
            c.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        self.expect_error('schema_mismatch', Store, self.s.root, 'synthetic')
        with sqlite3.connect(self.s.db) as c:
            self.assertEqual(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '99')

    def test_profile_root_binding_rejects_a_different_profile(self):
        self.expect_error('profile_mismatch', Store, self.s.root, 'production')

    def test_production_profile_rejects_synthetic_source_registration(self):
        production = Store(self.base / 'production', 'production')
        self.expect_error('source_restricted', production.register_source, 'synthetic:device', 'SYNTHETIC device')

    def test_search_cursor_pages_newest_first_without_duplicates(self):
        ids = [self.record(text=f'SYNTHETIC page {i}', occurred_at=f'2026-09-{i + 1:02d}T12:00:00Z')['record_id']
               for i in range(7)]
        actual, cursor = [], None
        while True:
            page = self.s.search(limit=2, cursor=cursor)
            actual.extend(row['id'] for row in page['records'])
            if not page['has_more']:
                break
            cursor = page['next_cursor']
        self.assertEqual(actual, list(reversed(ids)))
        self.assertEqual(len(set(actual)), 7)

    def test_search_date_bounds_are_half_open(self):
        ids = [self.record(text=f'SYNTHETIC date {day}', occurred_at=f'2026-09-{day:02d}T12:00:00Z')['record_id']
               for day in (1, 2, 3)]
        rows = self.s.search(start_at='2026-09-02T00:00:00Z', end_at='2026-09-03T00:00:00Z')['records']
        self.assertEqual([row['id'] for row in rows], [ids[1]])

    def test_search_supports_cjk_substrings(self):
        self.record(text='SYNTHETIC 合成姿势记录')
        self.assertEqual(len(self.s.search(query='姿势')['records']), 1)

    def test_search_matches_extracted_object_pages_and_reports_page_numbers(self):
        original = b'SYNTHETIC original bytes'
        attachment = self.s.put_attachment_bytes(request_id='attachment', data=original, filename='synthetic.txt',
                                                 mime='text/plain', text='SYNTHETIC attachment', occurred_at=AT)
        self.s.set_extraction(attachment['object_sha256'], status='done', method='fixture',
                              pages=['SYNTHETIC page one', 'SYNTHETIC posture note'])
        result = self.s.search(query='posture')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['matched_pages'], [2])

    def test_search_hides_superseded_records_unless_requested(self):
        old = self.record(text='SYNTHETIC original version')
        new = self.record(text='SYNTHETIC replacement version', supersedes=old['record_id'])
        self.assertEqual([r['id'] for r in self.s.search(query='version')['records']], [new['record_id']])
        both = self.s.search(query='version', include_superseded=True)['records']
        self.assertEqual({r['id'] for r in both}, {old['record_id'], new['record_id']})

    def _observation(self, native_id='synthetic-obs', value=70.0):
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        sample = dict(native_id=native_id, metric='SYNTHETIC.metric', start_at=AT, end_at=AT, timezone='UTC',
                      value_num=value, unit='unit', source_name='SYNTHETIC device')
        self.s.ingest_batch(request_id=self.rid(), source_id='synthetic:watch', samples=[sample], deleted_ids=[],
                            cursor='SYNTHETIC cursor')
        with self.s.connect() as c:
            return c.execute('SELECT id FROM observations WHERE native_id=?', (native_id,)).fetchone()[0]

    def test_observation_evidence_becomes_stale_after_reingest(self):
        obs_id = self._observation()
        self.record(kind='analysis', text='SYNTHETIC analysis cites observation', evidence_ids=[obs_id])
        with self.s.connect() as c:
            c.execute('UPDATE observations SET updated_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', obs_id))
        sample = dict(native_id='synthetic-obs', metric='SYNTHETIC.metric', start_at=AT, end_at=AT, timezone='UTC',
                      value_num=71.0, unit='unit', source_name='SYNTHETIC device')
        self.s.ingest_batch(request_id=self.rid(), source_id='synthetic:watch', samples=[sample], deleted_ids=[],
                            cursor='SYNTHETIC updated cursor')
        analysis = self.s.search(query='cites observation')['records'][0]
        self.assertFalse(analysis['evidence']['current'])
        self.assertEqual(analysis['evidence']['stale_ids'], [obs_id])

    def test_observation_evidence_becomes_stale_after_deletion(self):
        obs_id = self._observation()
        self.record(kind='analysis', text='SYNTHETIC analysis cites deleted observation', evidence_ids=[obs_id])
        self.s.ingest_batch(request_id=self.rid(), source_id='synthetic:watch', samples=[], deleted_ids=['synthetic-obs'],
                            cursor='SYNTHETIC deletion cursor')
        analysis = self.s.search(query='deleted observation')['records'][0]
        self.assertFalse(analysis['evidence']['current'])
        self.assertEqual(analysis['evidence']['stale_ids'], [obs_id])

    def test_evidence_reference_to_missing_object_page_is_rejected(self):
        attachment = self.s.put_attachment_bytes(request_id='one-page', data=b'SYNTHETIC page',
                                                 filename='one.txt', mime='text/plain', text='SYNTHETIC file',
                                                 occurred_at=AT)
        sha = attachment['object_sha256']
        self.s.set_extraction(sha, status='done', method='fixture', pages=['SYNTHETIC only page'])
        self.expect_error('missing_evidence', self.record, kind='analysis', evidence_ids=[f'obj:{sha}#p2'])

    def test_zero_ttl_insight_expires_and_is_not_pending(self):
        queued = self.s.queue_insight(request_id='zero-ttl', candidate=self.candidate(), ttl_days=0)
        self.assertTrue(queued['queued'])
        self.assertEqual(self.s.pending_insights()['insights'], [])
        with self.s.connect() as c:
            row = c.execute('SELECT state FROM insights WHERE id=?', (queued['insight_id'],)).fetchone()
        self.assertEqual(row['state'], 'expired')

    def test_closed_question_marks_pending_insight_stale(self):
        question = self.record(kind='question', text='SYNTHETIC open question')['record_id']
        candidate = self.candidate(question_id=question)
        queued = self.s.queue_insight(request_id='question-insight', candidate=candidate)
        self.s.put_record(request_id='close-question', kind='question', text='SYNTHETIC closed question',
                          occurred_at=AT, payload={'state': 'closed'}, supersedes=question)
        self.assertEqual(self.s.pending_insights()['insights'], [])
        with self.s.connect() as c:
            state = c.execute('SELECT state FROM insights WHERE id=?', (queued['insight_id'],)).fetchone()[0]
        self.assertEqual(state, 'stale')

    def test_acknowledging_a_nonpending_insight_is_rejected(self):
        queued = self.s.queue_insight(request_id='ack-first', candidate=self.candidate())
        self.s.ack_insight(request_id='ack-deliver', insight_id=queued['insight_id'])
        self.expect_error('not_pending', self.s.ack_insight, request_id='ack-again',
                          insight_id=queued['insight_id'])

    def test_ephemeral_source_policy_blocks_insight_queueing(self):
        self.expect_error('source_restricted', self.s.queue_insight, request_id='ephemeral',
                          candidate=self.candidate(source_policies=['ephemeral']))

    def test_same_evidence_and_version_is_deduplicated(self):
        candidate = self.candidate()
        self.s.queue_insight(request_id='first-candidate', candidate=candidate)
        duplicate = self.s.queue_insight(request_id='second-candidate', candidate=candidate)
        self.assertEqual(duplicate['reason'], 'duplicate')

    def test_updated_observation_version_is_not_a_duplicate(self):
        obs_id = self._observation()
        candidate = dict(decision='surface', topic='SYNTHETIC observation', why_now='SYNTHETIC evidence',
                         what_changed='SYNTHETIC updated observation', unknowns='SYNTHETIC unknown',
                         next_step='SYNTHETIC review', evidence_ids=[obs_id], source_policies=['durable'])
        self.s.queue_insight(request_id='observation-first', candidate=candidate)
        updated = dict(native_id='synthetic-obs', metric='SYNTHETIC.metric', start_at=AT, end_at=AT,
                       timezone='UTC', value_num=71.0, unit='unit', source_name='SYNTHETIC device')
        self.s.ingest_batch(request_id='observation-update', source_id='synthetic:watch', samples=[updated],
                            deleted_ids=[], cursor='SYNTHETIC updated cursor')
        changed = self.s.queue_insight(request_id='observation-second', candidate=candidate)
        self.assertNotEqual(changed.get('reason'), 'duplicate')

    def test_history_returns_complete_revision_chain_oldest_first(self):
        first = self.record(text='SYNTHETIC history one')
        second = self.record(text='SYNTHETIC history two', supersedes=first['record_id'])
        third = self.record(text='SYNTHETIC history three', supersedes=second['record_id'])
        self.assertEqual([row['id'] for row in self.s.history(third['record_id'])],
                         [first['record_id'], second['record_id'], third['record_id']])

    def test_export_writes_portable_files_and_byte_identical_original(self):
        self.record(text='SYNTHETIC export record')
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        sample = dict(native_id='export-sample', metric='SYNTHETIC.metric', start_at=AT, end_at=AT,
                      timezone='UTC', value_num=7.0, source_name='SYNTHETIC watch')
        self.s.ingest_batch(request_id='export-batch', source_id='synthetic:watch', samples=[sample], deleted_ids=[],
                            cursor='SYNTHETIC cursor')
        original = b'SYNTHETIC exported original\x00\xff'
        attachment = self.s.put_attachment_bytes(request_id='export-original', data=original,
                                                 filename='synthetic.bin', mime='application/octet-stream',
                                                 text='SYNTHETIC attachment', occurred_at=AT)
        destination = self.base / 'export'
        result = self.s.export(destination)
        self.assertTrue((destination / 'records.jsonl').is_file())
        self.assertTrue((destination / 'observations.csv').is_file())
        self.assertEqual((destination / 'originals' / attachment['object_sha256']).read_bytes(), original)
        self.assertTrue((destination / 'DATA_DICTIONARY.md').is_file())
        shipped = Path(__file__).resolve().parents[1] / 'delivery' / 'DATA_DICTIONARY.md'
        self.assertEqual((destination / 'DATA_DICTIONARY.md').read_text(), shipped.read_text())
        self.assertEqual(result['counts'], {'records': 2, 'observations': 1, 'originals': 1})

    def test_concurrent_same_request_id_commits_one_record(self):
        def write(_):
            return self.s.put_record(request_id='SYNTHETIC-concurrent-same', kind='event',
                                     text='SYNTHETIC concurrent record', occurred_at=AT)
        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(write, range(24)))
        self.assertEqual(len({row['record_id'] for row in results}), 1)
        self.assertEqual(len(self.s.search()['records']), 1)

    def test_concurrent_distinct_request_ids_preserve_integrity(self):
        def write(i):
            return self.s.put_record(request_id=f'SYNTHETIC-concurrent-{i}', kind='event',
                                     text=f'SYNTHETIC concurrent {i}', occurred_at=AT)
        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(write, range(24)))
        self.assertEqual(len({row['record_id'] for row in results}), 24)
        self.assertEqual(len(self.s.search()['records']), 24)
        with self.s.connect() as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_disk_full_during_blob_replace_leaves_no_object_row(self):
        with patch('phctx.store.os.replace', side_effect=OSError(28, 'No space')):
            self.expect_error('storage_unavailable', self.s.put_attachment_bytes, request_id='disk-full',
                              data=b'SYNTHETIC bytes', filename='synthetic.bin', mime='application/octet-stream',
                              text='SYNTHETIC attachment', occurred_at=AT)
        with self.s.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM objects').fetchone()[0], 0)


    def test_directory_sync_failure_is_retried_on_an_existing_blob(self):
        args = dict(data=b'SYNTHETIC dir-sync bytes', filename='s.bin', mime='application/octet-stream',
                    text='SYNTHETIC attachment', occurred_at=AT)
        real = os.fsync
        blobs_fd = []

        blobs_ino = os.stat(self.s.blobs).st_ino

        def failing(fd):  # the objects directory never syncs: file renamed into place, entry not durable
            if os.fstat(fd).st_ino == blobs_ino:
                blobs_fd.append(fd)
                raise OSError(5, 'injected EIO')
            return real(fd)
        with patch('phctx.store.os.fsync', side_effect=failing):
            self.expect_error('storage_unavailable', self.s.put_attachment_bytes, request_id='d1', **args)
            self.expect_error('storage_unavailable', self.s.put_attachment_bytes, request_id='d1', **args)
        self.assertGreaterEqual(len(blobs_fd), 2)  # the retry synced again instead of trusting the existing file
        with self.s.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM objects').fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM receipts WHERE request_id='d1'").fetchone()[0], 0)
        self.assertTrue(self.s.put_attachment_bytes(request_id='d1', **args)['original_saved'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
