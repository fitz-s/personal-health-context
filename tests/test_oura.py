"""Oura API v2 sync against a fake HTTP layer; every document is synthetic."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from phctx import oauth, oura
from phctx.store import Store

TODAY = date(2026, 9, 24)


class FakeOura:
    """Serves SYNTHETIC documents by collection; filters by the requested window like the real API."""

    def __init__(self, docs):
        self.docs, self.calls = docs, []

    def __call__(self, token, collection, params):
        self.calls.append((collection, dict(params)))
        rows = self.docs.get(collection, [])
        if collection in oura.SERIES:
            lo, hi = params['start_datetime'][:10], params['end_datetime'][:10]
            rows = [d for d in rows if lo <= d['timestamp'][:10] <= hi]
        else:
            lo, hi = params['start_date'], params['end_date']
            rows = [d for d in rows if lo <= (d.get('day') or d.get('start_day') or '') <= hi]
        if 'next_token' in params:
            return {'data': rows[1:], 'next_token': None}
        return {'data': rows[:1], 'next_token': 'SYNTHETIC-page-2' if len(rows) > 1 else None}


def day(i, score):
    return {'id': f'SYNTHETIC-ds-{i}', 'day': f'2026-09-{i:02d}', 'score': score, 'contributors': {'deep_sleep': 70}}


class OuraSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 'store', 'synthetic')
        self.fake = FakeOura({
            'daily_sleep': [day(20, 81), day(21, 77)],
            'sleep': [{'id': 'SYNTHETIC-sl-1', 'day': '2026-09-21', 'bedtime_start': '2026-09-20T23:10:00-05:00',
                       'bedtime_end': '2026-09-21T06:40:00-05:00', 'total_sleep_duration': 25200, 'type': 'long_sleep'}],
            'heartrate': [{'timestamp': '2026-09-21T06:00:00+00:00', 'bpm': 52, 'source': 'sleep'}],
        })

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kw):
        with patch.object(oura, '_get', self.fake):
            return oura.sync(self.s, token='SYNTHETIC-token', today=TODAY, **kw)

    def rows(self, sql):
        with self.s.connect() as c:
            return [tuple(r) for r in c.execute(sql)]

    def test_backfill_stores_every_document_with_its_raw_body(self):
        out = self.sync()
        self.assertEqual((out['documents']['daily_sleep'], out['documents']['sleep'], out['documents']['heartrate']),
                         (2, 1, 1))
        got = self.rows("SELECT metric, value_num, unit, start_at, end_at FROM active_observations WHERE source_id='oura' "
                        'ORDER BY metric, start_at')
        self.assertEqual(got[0], ('oura.daily_sleep', 81.0, 'score', '2026-09-20T05:00:00.000000+00:00',
                                  '2026-09-21T05:00:00.000000+00:00'))
        self.assertEqual(got[2][:3], ('oura.heartrate', 52.0, 'count/min'))
        self.assertEqual(got[3], ('oura.sleep', 25200.0, 's', '2026-09-21T04:10:00.000000+00:00',
                                  '2026-09-21T11:40:00.000000+00:00'))
        raw = json.loads(self.rows("SELECT raw_json FROM observations WHERE metric='oura.daily_sleep' LIMIT 1")[0][0])
        self.assertEqual(raw['raw']['contributors'], {'deep_sleep': 70})

    def test_rerun_is_idempotent_and_reads_only_the_trailing_window(self):
        self.sync()
        n = self.rows("SELECT count(*) FROM observations WHERE source_id='oura'")
        self.fake.calls.clear()
        self.sync()
        self.assertEqual(self.rows("SELECT count(*) FROM observations WHERE source_id='oura'"), n)
        starts = {p.get('start_date') or p['start_datetime'][:10] for _, p in self.fake.calls}
        self.assertEqual(min(starts), str(date(2026, 9, 10)))  # today - REREAD_DAYS, not the first day

    def test_a_revised_document_updates_and_absence_deletes_nothing(self):
        """R6-03: a re-read window cannot be matched to stored rows exactly, so absence is never a deletion."""
        self.sync()
        self.fake.docs['daily_sleep'] = [day(21, 79)]  # the 20th missing from this response, the 21st rescored
        self.sync()
        got = dict(self.rows("SELECT native_id, value_num FROM active_observations WHERE metric='oura.daily_sleep'"))
        self.assertEqual(got, {'daily_sleep:SYNTHETIC-ds-20': 81.0, 'daily_sleep:SYNTHETIC-ds-21': 79.0})

    def test_a_200_without_a_collection_is_an_error_not_emptiness(self):
        """R6-05"""
        with patch.object(oura, '_get', lambda *a: {}):
            out = oura.sync(self.s, token='SYNTHETIC-token', today=TODAY)
        self.assertEqual(set(out['refused'].values()), {'oura_bad_response'})

    def test_overlapping_syncs_are_refused(self):
        """R6-04: one sync per provider at a time."""
        import fcntl
        with open(self.s.root / 'oura.lock', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(oauth.OAuthError) as e:
                self.sync()
        self.assertEqual(e.exception.code, 'oura_sync_running')

    def test_a_failed_oauth_refresh_still_uses_the_personal_token(self):
        """R6-07"""
        def refused(p):
            raise oauth.OAuthError('oura_auth')
        with patch.object(oura, '_get', self.fake), patch.object(oauth, 'connected', lambda p: True), \
                patch.object(oauth, 'access_token', refused), patch.object(oura, 'keychain_get', lambda *a: 'SYNTHETIC-p'):
            self.assertEqual(oura.sync(self.s, today=TODAY)['status'], 'synced')

    def test_an_expired_token_marks_the_source_and_saves_nothing(self):
        def refused(*a):
            raise oauth.OAuthError('oura_auth')
        with patch.object(oura, '_get', refused):
            out = oura.sync(self.s, token='SYNTHETIC-token', today=TODAY)
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(set(out['refused'].values()), {'oura_auth'})
        self.assertEqual(self.rows("SELECT state FROM sources WHERE id='oura'"), [('error',)])
        self.assertEqual(self.rows("SELECT count(*) FROM observations WHERE source_id='oura'"), [(0,)])

    def test_a_collection_the_first_token_cannot_read_falls_back_to_the_next(self):
        served = []

        def get(token, collection, params):
            if token == 'SYNTHETIC-oauth' and collection == 'daily_sleep':
                raise oauth.OAuthError('oura_auth')  # the OAuth app's scopes do not reach it
            served.append((collection, token))
            return self.fake(token, collection, params)
        with patch.object(oura, '_get', get), patch.object(oauth, 'connected', lambda p: True), \
                patch.object(oauth, 'access_token', lambda p: 'SYNTHETIC-oauth'), \
                patch.object(oura, 'keychain_get', lambda *a: 'SYNTHETIC-personal'):
            out = oura.sync(self.s, today=TODAY)
        self.assertEqual((out['status'], out['documents']['daily_sleep']), ('synced', 2))
        self.assertEqual({t for c, t in served if c == 'daily_sleep'}, {'SYNTHETIC-personal'})
        self.assertEqual({t for c, t in served if c == 'sleep'}, {'SYNTHETIC-oauth'})

if __name__ == '__main__':
    unittest.main()
