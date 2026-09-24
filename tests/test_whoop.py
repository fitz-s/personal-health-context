"""WHOOP API v2 sync and OAuth token rotation against fakes; every record is synthetic."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phctx import oauth, whoop
from phctx.store import Store


def cycle(i, strain, end=True):
    return {'id': 1000 + i, 'user_id': 1, 'created_at': f'2026-09-{i:02d}T05:00:00Z', 'updated_at': f'2026-09-{i:02d}T05:00:00Z',
            'start': f'2026-09-{i:02d}T05:00:00Z', 'end': f'2026-09-{i + 1:02d}T05:00:00Z' if end else None,
            'timezone_offset': '-05:00', 'score_state': 'SCORED', 'score': {'strain': strain, 'kilojoule': 8000.0}}


class FakeWhoop:
    def __init__(self, data):
        self.data, self.calls = data, []

    def __call__(self, token, path, params):
        self.calls.append((path, dict(params)))
        rows = [r for r in self.data.get(path, []) if (r.get('start') or r['created_at']) >= params['start']]
        if 'nextToken' in params:
            return {'records': rows[1:], 'next_token': None}
        return {'records': rows[:1], 'next_token': 'SYNTHETIC-next' if len(rows) > 1 else None}


class WhoopSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 'store', 'synthetic')
        self.fake = FakeWhoop({
            '/v2/cycle': [cycle(20, 12.5), cycle(21, 8.1, end=False)],
            '/v2/recovery': [{'cycle_id': 1020, 'sleep_id': 'SYNTHETIC-sleep-a', 'user_id': 1,
                              'created_at': '2026-09-20T12:00:00Z', 'updated_at': '2026-09-20T12:00:00Z',
                              'score_state': 'SCORED', 'score': {'recovery_score': 66.0, 'resting_heart_rate': 52.0,
                                                                 'hrv_rmssd_milli': 61.2}}],
            '/v2/activity/sleep': [{'id': 'SYNTHETIC-sleep-a', 'start': '2026-09-20T04:00:00Z',
                                    'end': '2026-09-20T11:30:00Z', 'created_at': '2026-09-20T11:31:00Z', 'nap': False,
                                    'score_state': 'SCORED', 'score': {'sleep_performance_percentage': 88.0}}],
            '/v2/activity/workout': [{'id': 'SYNTHETIC-w-1', 'start': '2026-09-20T22:00:00Z', 'end': '2026-09-20T23:00:00Z',
                                      'created_at': '2026-09-20T23:01:00Z', 'sport_name': 'running',
                                      'score_state': 'SCORED', 'score': {'strain': 10.2}}],
        })

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kw):
        with patch.object(whoop, '_get', self.fake):
            return whoop.sync(self.s, token='SYNTHETIC-token', **kw)

    def rows(self, sql):
        with self.s.connect() as c:
            return [tuple(r) for r in c.execute(sql)]

    def test_backfill_keeps_every_record_with_its_headline_and_raw_body(self):
        self.assertEqual(self.sync()['records'], {'cycle': 2, 'recovery': 1, 'sleep': 1, 'workout': 1})
        got = dict(self.rows("SELECT native_id, value_num FROM active_observations WHERE source_id='whoop'"))
        self.assertEqual(got, {'cycle:1020': 12.5, 'cycle:1021': 8.1, 'recovery:SYNTHETIC-sleep-a': 66.0,
                               'sleep:SYNTHETIC-sleep-a': 88.0, 'workout:SYNTHETIC-w-1': 10.2})
        open_cycle = self.rows("SELECT start_at, end_at FROM observations WHERE native_id='cycle:1021'")[0]
        self.assertEqual(open_cycle[0], open_cycle[1])  # not closed yet: a point, never an invented end
        raw = json.loads(self.rows("SELECT raw_json FROM observations WHERE native_id='recovery:SYNTHETIC-sleep-a'")[0][0])
        self.assertEqual(raw['raw']['score']['hrv_rmssd_milli'], 61.2)
        self.assertEqual(self.rows("SELECT value_text FROM observations WHERE native_id='workout:SYNTHETIC-w-1'"),
                         [('running',)])

    def test_rerun_reads_the_trailing_window_updates_and_deletes(self):
        self.sync()
        self.fake.calls.clear()
        self.fake.data['/v2/cycle'] = [cycle(21, 9.4)]  # the 20th deleted on WHOOP, the 21st closed and rescored
        with patch.object(whoop, 'REREAD_DAYS', 10000):  # the synthetic records predate the real trailing window
            self.sync()
        self.assertTrue(all(p['start'] != whoop.FIRST_DAY for _, p in self.fake.calls))  # incremental, not a backfill
        self.assertEqual(self.rows("SELECT native_id, value_num FROM active_observations WHERE metric='whoop.cycle'"),
                         [('cycle:1021', 9.4)])

    def test_refresh_stores_the_rotated_token_before_use(self):
        stored = {'phctx-whoop-client-id': 'SYNTHETIC-id', 'phctx-whoop-client-secret': 'SYNTHETIC-secret',
                  'phctx-whoop-refresh-token': 'SYNTHETIC-r1'}
        sent = []
        with patch.object(oauth, 'keychain_get', stored.get), \
                patch.object(oauth, 'keychain_set', lambda k, v: stored.__setitem__(k, v)), \
                patch.object(oauth, '_post', lambda p, form: sent.append(form) or
                             {'access_token': 'SYNTHETIC-a2', 'refresh_token': 'SYNTHETIC-r2'}):
            self.assertEqual(oauth.access_token(whoop.PROVIDER), 'SYNTHETIC-a2')
        self.assertEqual((sent[0]['refresh_token'], sent[0]['scope']), ('SYNTHETIC-r1', 'offline'))
        self.assertEqual(stored['phctx-whoop-refresh-token'], 'SYNTHETIC-r2')

    def test_missing_client_secret_is_a_bounded_error(self):
        with patch.object(oauth, 'keychain_get', lambda k: None), self.assertRaises(oauth.OAuthError) as e:
            oauth.access_token(whoop.PROVIDER)
        self.assertEqual(e.exception.code, 'whoop_client_missing')


if __name__ == '__main__':
    unittest.main()


class ConsentServerTests(unittest.TestCase):
    """One localhost server answers every provider; each consent request is created when its start URL is opened."""

    def run_login(self, providers, approve, stored, post=None):
        import threading
        import urllib.parse
        import urllib.request
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # bypass any HTTP proxy
        pages, seen = [], []

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        no_follow = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

        def browse(start):  # stands in for the owner: open the start URL, then answer the vendor's consent page
            def go():
                for attempt in approve[start.rstrip('/').split('/')[-2]]:
                    try:
                        no_follow.open(start.replace('localhost', '127.0.0.1'), timeout=10)
                    except urllib.error.HTTPError as e:  # the 302 to the vendor
                        auth = e.headers['Location']
                    seen.append(auth)
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(auth).query)
                    cb = q['redirect_uri'][0] + '?' + urllib.parse.urlencode({**attempt, 'state': q['state'][0]})
                    pages.append(direct.open(cb.replace('localhost', '127.0.0.1'), timeout=10).read().decode())
            threading.Thread(target=go).start()
        with patch.object(oauth, 'keychain_get', stored.get), \
                patch.object(oauth, 'keychain_set', lambda k, v: stored.__setitem__(k, v)), \
                patch.object(oauth, '_post', post or (lambda p, form: {'access_token': 'SYNTHETIC-a',
                                                                       'refresh_token': f'SYNTHETIC-r-{p.name}'})), \
                patch.object(oauth, 'PORT', 47897), patch('builtins.print'):
            out = oauth.login(providers, open_browser=browse, timeout=30)
        return out, pages, seen

    def test_two_providers_on_one_port_each_exchange_their_own_code(self):
        import urllib.parse
        from phctx import oura
        oura = type('P', (), {'PROVIDER': oauth.Provider('oura', 'https://example.invalid/auth',
                                                         'https://example.invalid/token', 'daily')})
        stored = {f'phctx-{n}-{k}': f'SYNTHETIC-{n}-{k}' for n in ('whoop', 'oura') for k in ('client-id', 'client-secret')}
        exchanged = []
        out, pages, seen = self.run_login(
            [whoop.PROVIDER, oura.PROVIDER], {'whoop': [{'code': 'SYNTHETIC-w'}], 'oura': [{'code': 'SYNTHETIC-o'}]},
            stored, post=lambda p, form: exchanged.append((p.name, form['code'])) or
            {'access_token': 'SYNTHETIC-a', 'refresh_token': f'SYNTHETIC-r-{p.name}'})
        self.assertEqual(out, {'whoop': 'connected', 'oura': 'connected'})
        self.assertEqual(sorted(exchanged), [('oura', 'SYNTHETIC-o'), ('whoop', 'SYNTHETIC-w')])
        self.assertEqual(stored['phctx-oura-refresh-token'], 'SYNTHETIC-r-oura')
        self.assertTrue(all('consent received' in page for page in pages))
        whoop_auth = next(u for u in seen if 'whoop' in u)
        self.assertEqual(len(urllib.parse.parse_qs(urllib.parse.urlsplit(whoop_auth).query)['state'][0]), 8)

    def test_a_refused_attempt_says_so_and_a_fresh_start_still_connects(self):
        stored = {f'phctx-whoop-{k}': f'SYNTHETIC-{k}' for k in ('client-id', 'client-secret')}
        out, pages, seen = self.run_login(
            [whoop.PROVIDER], {'whoop': [{'error': 'request_unauthorized'}, {'code': 'SYNTHETIC-w'}]}, stored)
        self.assertEqual(out, {'whoop': 'connected'})
        self.assertIn('NOT connected (request_unauthorized)', pages[0])
        self.assertNotEqual(seen[0], seen[1])  # each start creates a new request


class UserAgentTests(unittest.TestCase):
    def test_every_vendor_request_names_a_user_agent(self):
        """WHOOP's front refuses urllib's default User-Agent with HTTP 403 (error code 1010)."""
        from phctx import oura
        sent = []

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"records": [], "data": [], "access_token": "a", "refresh_token": "r"}'

        def urlopen(req, timeout=None):
            sent.append(req.get_header('User-agent'))
            return Response()
        with patch('urllib.request.urlopen', urlopen):
            whoop._get('SYNTHETIC', '/v2/cycle', {})
            oura._get('SYNTHETIC', 'daily_sleep', {})
            oauth._post(whoop.PROVIDER, {'grant_type': 'refresh_token'})
        self.assertEqual(sent, [oauth.USER_AGENT] * 3)
