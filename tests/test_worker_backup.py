"""Synthetic worker scheduling and backup/restore integration tests."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from phctx import backup, model, worker
from phctx.config import Config
from phctx.store import Store, StoreError, utcnow

AT = '2026-09-20T12:00:00-05:00'


class WorkerBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cfg = Config(profile='synthetic', root=self.base / 'data', model_enabled=False,
                          model_backend='none', worker_mode='shadow')
        self.store = Store(self.cfg.root, self.cfg.profile)
        self.seq = 0

    def tearDown(self):
        self.tmp.cleanup()

    def request(self):
        self.seq += 1
        return f'SYNTHETIC-request-{self.seq}'

    def record(self, **kwargs):
        args = dict(request_id=self.request(), kind='event', text='SYNTHETIC event', occurred_at=AT)
        args.update(kwargs)
        return self.store.put_record(**args)

    def question(self, *, term='posture', metric='HKQuantityTypeIdentifierBodyMass'):
        result = self.store.put_record(request_id=self.request(), kind='question',
                                       text='SYNTHETIC open question about posture', occurred_at=AT,
                                       payload={'state': 'open', 'watch_terms': [term], 'watch_metrics': [metric]})
        with self.store.transaction() as c:
            seq = c.execute('SELECT coalesce(max(seq),0) FROM changes').fetchone()[0]
            c.execute("INSERT INTO meta(key,value) VALUES('worker_watermark',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (str(seq),))
            c.execute("INSERT INTO meta(key,value) VALUES('last_review_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (utcnow(),))
        return result['record_id']

    def candidate(self, question_id, record_id):
        return {'decision': 'surface', 'question_id': question_id, 'topic': 'SYNTHETIC posture review',
                'why_now': 'SYNTHETIC new evidence arrived', 'what_changed': 'SYNTHETIC record added',
                'unknowns': 'SYNTHETIC explanation unknown', 'next_step': 'SYNTHETIC review the measurement',
                'evidence_ids': [record_id], 'evidence_versions': {}, 'source_policies': ['durable']}

    def count(self, sql, args=()):
        with self.store.connect() as c:
            return c.execute(sql, args).fetchone()[0]

    def test_disabled_model_records_run_watermark_without_model_calls(self):
        self.record(text='SYNTHETIC record without an open question')
        first = worker.run_once(self.cfg)
        self.assertIn(first['outcome'], {'idle', 'ran'})
        self.assertEqual(self.count('SELECT count(*) FROM model_calls'), 0)
        with self.store.connect() as c:
            run = c.execute('SELECT outcome, model_calls FROM worker_runs ORDER BY started_at DESC LIMIT 1').fetchone()
            watermark = int(c.execute("SELECT value FROM meta WHERE key='worker_watermark'").fetchone()[0])
            maximum = c.execute('SELECT max(seq) FROM changes').fetchone()[0]
        self.assertIn(run['outcome'], {'idle', 'ran'})
        self.assertEqual(run['model_calls'], 0)
        self.assertEqual(watermark, maximum)
        second = worker.run_once(self.cfg)
        self.assertEqual(second['plan']['changes'], 0)
        self.assertEqual(self.count('SELECT count(*) FROM model_calls'), 0)

    def test_unrelated_record_does_not_enqueue_question_revisit(self):
        self.question()
        self.record(text='SYNTHETIC unrelated grocery note')
        result = worker.run_once(self.cfg)
        self.assertEqual(result['plan']['enqueued'], [])
        self.assertEqual(self.count("SELECT count(*) FROM jobs WHERE type='revisit'"), 0)

    def test_matching_record_enqueues_revisit_and_disabled_model_keeps_job_queued(self):
        question_id = self.question()
        self.record(text='SYNTHETIC posture measurement was noted')
        result = worker.run_once(self.cfg)
        self.assertEqual(len(result['plan']['enqueued']), 1)
        job = self.store.connect()
        with job as c:
            row = c.execute("SELECT state, last_error_code FROM jobs WHERE type='revisit'").fetchone()
        self.assertEqual(row['state'], 'queued')
        self.assertEqual(row['last_error_code'], 'model_disabled')
        self.assertEqual(self.count('SELECT count(*) FROM insights'), 0)

    def test_scripted_surface_candidate_is_queued_in_shadow_mode(self):
        self.cfg.model_enabled = True
        self.cfg.model_backend = 'scripted'
        self.cfg.model_id = 'SYNTHETIC-scripted'
        question_id = self.question()
        record_id = self.record(text='SYNTHETIC posture assessment added')['record_id']
        result = worker.run_once(self.cfg, scripted=lambda task: self.candidate(question_id, record_id))
        self.assertEqual(len(result['jobs']), 1)
        self.assertTrue(result['jobs'][0]['gate']['queued'])
        pending = self.store.pending_insights()['insights']
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]['payload']['shadow'])
        self.assertEqual(pending[0]['payload']['evidence_ids'], [record_id])

    def test_scripted_silence_completes_job_and_second_run_has_no_new_call(self):
        self.cfg.model_enabled = True
        self.cfg.model_backend = 'scripted'
        self.cfg.model_id = 'SYNTHETIC-scripted'
        self.question()
        self.record(text='SYNTHETIC posture assessment added')
        calls = []
        result = worker.run_once(self.cfg, scripted=lambda task: (calls.append(task) or {'decision': 'silence'}))
        self.assertEqual(result['jobs'][0]['outcome'], 'silence')
        self.assertEqual(self.count("SELECT state FROM jobs WHERE type='revisit'"), 'done')
        self.assertEqual(self.count('SELECT count(*) FROM insights'), 0)
        call_count = self.count('SELECT count(*) FROM model_calls')
        second = worker.run_once(self.cfg, scripted=lambda task: self.fail('no new model call expected'))
        self.assertEqual(second['plan']['changes'], 0)
        self.assertEqual(second['jobs'], [])
        self.assertEqual(self.count('SELECT count(*) FROM model_calls'), call_count)
        self.assertEqual(len(calls), 1)

    def test_zero_daily_call_cap_defers_job_without_completing_it(self):
        self.cfg.model_enabled = True
        self.cfg.model_backend = 'scripted'
        self.cfg.daily_call_cap = 0
        question_id = self.question()
        self.record(text='SYNTHETIC posture measurement added')
        result = worker.run_once(self.cfg, scripted=lambda task: self.fail('budget cap must skip model'))
        self.assertEqual(result['jobs'][0]['error'], 'budget_exhausted')
        with self.store.connect() as c:
            job = c.execute("SELECT state, last_error_code, next_run_at FROM jobs WHERE type='revisit'").fetchone()
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['last_error_code'], 'budget_exhausted')
        self.assertGreater(datetime.fromisoformat(job['next_run_at']), datetime.now(timezone.utc))
        self.assertEqual(self.count('SELECT count(*) FROM insights'), 0)

    def test_scripted_model_error_requeues_with_backoff_without_user_record(self):
        self.cfg.model_enabled = True
        self.cfg.model_backend = 'scripted'
        self.question()
        self.record(text='SYNTHETIC posture measurement added')
        def fail_model(task):
            raise model.ModelError('model_call_failed')
        result = worker.run_once(self.cfg, scripted=fail_model)
        self.assertEqual(result['jobs'][0]['error'], 'model_call_failed')
        with self.store.connect() as c:
            job = c.execute("SELECT state, attempts, next_run_at, last_error_code FROM jobs WHERE type='revisit'").fetchone()
            model_call = c.execute('SELECT status,error_code FROM model_calls').fetchone()
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['attempts'], 1)
        self.assertGreater(datetime.fromisoformat(job['next_run_at']), datetime.now(timezone.utc))
        self.assertEqual(job['last_error_code'], 'model_call_failed')
        self.assertEqual(tuple(model_call), ('failed', 'model_call_failed'))
        self.assertEqual(self.count('SELECT count(*) FROM records'), 2)
        self.assertEqual(self.count('SELECT count(*) FROM insights'), 0)

    def test_off_preference_blocks_surfacing_and_suppresses_pending(self):
        self.cfg.model_enabled = True
        self.cfg.model_backend = 'scripted'
        question_id = self.question()
        record_id = self.record(text='SYNTHETIC posture measurement added')['record_id']
        self.store.set_preference(request_id=self.request(), key='proactivity', value='off')
        result = worker.run_once(self.cfg, scripted=lambda task: self.candidate(question_id, record_id))
        self.assertFalse(result['jobs'][0]['gate']['queued'])
        self.assertEqual(result['jobs'][0]['gate']['reason'], 'preference_off')
        pending = self.store.pending_insights()
        self.assertTrue(pending['suppressed'])
        self.assertEqual(pending['insights'], [])
        self.assertEqual(self.count('SELECT count(*) FROM insights'), 0)

    def test_worker_lease_rejects_second_owner_until_released(self):
        self.assertTrue(worker.acquire(self.store, 'worker', 'SYNTHETIC-owner-a'))
        self.assertFalse(worker.acquire(self.store, 'worker', 'SYNTHETIC-owner-b'))
        worker.release(self.store, 'worker', 'SYNTHETIC-owner-a')
        self.assertTrue(worker.acquire(self.store, 'worker', 'SYNTHETIC-owner-b'))

    def test_backup_restore_preserves_counts_and_original_object_bytes(self):
        self.record(text='SYNTHETIC backup record')
        original = b'SYNTHETIC backup object\x00\xff'
        attachment = self.store.put_attachment_bytes(request_id=self.request(), data=original,
                                                     filename='synthetic.bin', mime='application/octet-stream',
                                                     text='SYNTHETIC object', occurred_at=AT)
        destination = self.base / 'backups'
        result = backup.backup(self.cfg, dest=destination)
        snapshot = Path(result['snapshot']['path'])
        restored = backup.restore(self.cfg, snapshot, self.base / 'restored')
        self.assertEqual(restored['counts'], {'records': 2, 'observations': 0, 'objects': 1})
        self.assertEqual(restored['originals_verified'], 1)
        self.assertEqual(Store(self.base / 'restored').read_object(attachment['object_sha256']), original)

    def test_encrypted_snapshot_decrypts_and_restores_with_fixed_passphrase(self):
        self.record(text='SYNTHETIC encrypted backup record')
        snapshot = self.base / 'snapshot'
        self.store.backup(snapshot)
        archive = self.base / 'snapshot.phbk'
        backup.encrypt_dir(snapshot, archive, 'SYNTHETIC fixed test passphrase')
        decrypted_root = backup.decrypt_to(archive, self.base / 'decrypted', 'SYNTHETIC fixed test passphrase')
        restored = Store.restore(decrypted_root, self.base / 'encrypted-restored')
        self.assertEqual(len(restored.search()['records']), 1)

    def _encrypted_snapshot(self, name):
        snapshot = self.base / (name + '-snapshot')
        self.store.backup(snapshot)
        archive = self.base / (name + '.phbk')
        backup.encrypt_dir(snapshot, archive, 'SYNTHETIC fixed test passphrase')
        return archive

    def test_encrypted_snapshot_rejects_wrong_passphrase(self):
        archive = self._encrypted_snapshot('wrong-key')
        with self.assertRaises(StoreError) as caught:
            backup.decrypt_to(archive, self.base / 'wrong-dest', 'SYNTHETIC incorrect passphrase')
        self.assertEqual(caught.exception.code, 'backup_invalid')

    def test_encrypted_snapshot_rejects_tampered_archive(self):
        archive = self._encrypted_snapshot('tampered')
        data = bytearray(archive.read_bytes())
        data[-1] ^= 1
        archive.write_bytes(data)
        with self.assertRaises(StoreError) as caught:
            backup.decrypt_to(archive, self.base / 'tampered-dest', 'SYNTHETIC fixed test passphrase')
        self.assertEqual(caught.exception.code, 'backup_invalid')

    def test_encrypted_snapshot_rejects_truncated_archive(self):
        archive = self._encrypted_snapshot('truncated')
        archive.write_bytes(archive.read_bytes()[:-5])
        with self.assertRaises(StoreError) as caught:
            backup.decrypt_to(archive, self.base / 'truncated-dest', 'SYNTHETIC fixed test passphrase')
        self.assertEqual(caught.exception.code, 'backup_invalid')

    def test_rotation_keeps_seven_newest_and_weekly_representatives(self):
        snapshots = self.base / 'rotation'
        snapshots.mkdir()
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        created = []
        for day in range(28):
            stamp = (start + timedelta(days=day)).strftime('%Y%m%dT%H%M%SZ')
            path = snapshots / f'snapshot-{stamp}'
            path.mkdir()
            (path / 'backup_manifest.json').write_text('{}')
            created.append(path.name)
        removed = backup.rotate(snapshots)
        remaining = {path.name for path in snapshots.iterdir()}
        newest = set(sorted(created, reverse=True)[:7])
        weekly = {}
        for name in sorted(created, reverse=True):
            date = datetime.strptime(name.split('-', 1)[1][:15], '%Y%m%dT%H%M%S')
            week = date.isocalendar()[:2]
            weekly.setdefault(week, name)
        expected_weeks = set(list(weekly.values())[:backup.KEEP_WEEKLY])
        self.assertTrue(newest <= remaining)
        self.assertTrue(expected_weeks <= remaining)
        self.assertEqual(remaining, newest | expected_weeks)
        self.assertEqual(set(removed), set(created) - remaining)


if __name__ == '__main__':
    unittest.main(verbosity=2)
