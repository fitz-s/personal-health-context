"""Life Dashboard Companion webhook receiver tests (synthetic)."""
import hashlib
import hmac
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from phctx import apple_export, companion
from phctx.store import Store

SECRET = 'SYNTHETIC-companion-secret'


def sign(body: bytes, secret: str = SECRET) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_zip(path: Path, xml: str) -> Path:
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('apple_health_export/export.xml', xml)
    return path


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def read(self):
        return self.body


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'store', 'synthetic')
        self.server = companion.make_server(self.store, '127.0.0.1', 0, SECRET, 'America/Chicago')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.base_url = f'http://127.0.0.1:{self.server.server_address[1]}'
        # The environment sets HTTP_PROXY; a bare urlopen would route this loopback request through
        # it and hang/fail, so bypass any configured proxy for these synthetic requests.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, payload, *, secret=SECRET, header=True):
        body = json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json'}
        if header:
            headers['X-Signature'] = sign(body, secret)
        request = urllib.request.Request(self.base_url + '/companion', data=body, method='POST', headers=headers)
        try:
            with self.opener.open(request, timeout=4) as response:
                return Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            try:
                return Response(error.code, error.read())
            finally:
                error.close()

    def post_json(self, payload, **kw):
        response = self.post(payload, **kw)
        return response.status, json.loads(response.read())

    def payload(self):
        return {
            'timestamp': '2026-09-20T12:00:00Z', 'app_version': '1.0.0', 'source': 'healthkit_ios',
            'steps': [{'count': 120, 'start_time': '2026-09-20T08:00:00Z', 'end_time': '2026-09-20T08:10:00Z',
                      'uuid': 'SYNTHETIC-steps-1', 'source': 'iPhone'}],
            'heart_rate': [{'bpm': 61, 'time': '2026-09-20T08:05:00Z', 'uuid': 'SYNTHETIC-hr-1',
                           'source': 'Apple Watch'}],
            'sleep': [{'session_end_time': '2026-09-20T07:00:00Z', 'duration_seconds': 3600,
                      'stages': [{'stage': 'deep', 'start_time': '2026-09-20T06:00:00Z',
                                  'end_time': '2026-09-20T07:00:00Z', 'duration_seconds': 3600,
                                  'uuid': 'SYNTHETIC-sleep-1', 'source': 'Apple Watch'}]}],
            'exercise': [{'type': 'running', 'start_time': '2026-09-20T09:00:00Z',
                         'end_time': '2026-09-20T09:30:00Z', 'duration_seconds': 1800,
                         'uuid': 'SYNTHETIC-workout-1', 'source': 'Apple Watch'}],
        }

    def test_valid_signature_accepted_and_mapped(self):
        status, ack = self.post_json(self.payload())
        self.assertEqual(status, 200)
        self.assertEqual(ack['upserted'], 4)
        self.assertEqual(ack['unmapped_types'], [])
        with self.store.connect() as c:
            rows = {r['native_id']: (r['metric'], r['unit'], r['value_num'], r['value_text'])
                   for r in c.execute('SELECT native_id, metric, unit, value_num, value_text FROM observations')}
        self.assertEqual(rows['SYNTHETIC-steps-1'], ('HKQuantityTypeIdentifierStepCount', 'count', 120, None))
        self.assertEqual(rows['SYNTHETIC-hr-1'], ('HKQuantityTypeIdentifierHeartRate', 'count/min', 61, None))
        self.assertEqual(rows['SYNTHETIC-sleep-1'],
                         ('HKCategoryTypeIdentifierSleepAnalysis', '', 4, 'HKCategoryValueSleepAnalysisAsleepDeep'))
        workout = rows['SYNTHETIC-workout-1']
        self.assertEqual(workout[:3], ('HKWorkoutTypeIdentifier', 's', 1800))
        self.assertEqual(workout[3], 'HKWorkoutActivityTypeRunning')

    def test_nutrition_splits_combined_record_and_keeps_standalone_uuid(self):
        payload = {'nutrition': [
            {'calories': 500.0, 'protein_grams': 30.0, 'start_time': '2026-09-20T12:00:00Z',
             'end_time': '2026-09-20T12:00:00Z', 'uuid': 'SYNTHETIC-meal-1', 'source': 'iPhone'},
            {'protein_grams': 10.0, 'start_time': '2026-09-20T15:00:00Z', 'end_time': '2026-09-20T15:00:00Z',
             'uuid': 'SYNTHETIC-protein-standalone-1', 'source': 'iPhone'},
        ]}
        status, ack = self.post_json(payload)
        self.assertEqual(status, 200)
        self.assertEqual(ack['upserted'], 3)
        with self.store.connect() as c:
            rows = {r['native_id']: (r['metric'], r['value_num'])
                   for r in c.execute('SELECT native_id, metric, value_num FROM observations')}
        # combined record: calories keeps the real uuid, protein gets a synthesized suffix
        self.assertEqual(rows['SYNTHETIC-meal-1'], ('HKQuantityTypeIdentifierDietaryEnergyConsumed', 500.0))
        self.assertEqual(rows['SYNTHETIC-meal-1:protein'], ('HKQuantityTypeIdentifierDietaryProtein', 30.0))
        # standalone record: its own uuid is genuine and must not be mangled
        self.assertEqual(rows['SYNTHETIC-protein-standalone-1'], ('HKQuantityTypeIdentifierDietaryProtein', 10.0))

    def test_bad_signature_refused_and_nothing_stored(self):
        status, _ = self.post_json(self.payload(), secret='SYNTHETIC-wrong-secret')
        self.assertEqual(status, 401)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 0)

    def test_missing_signature_refused_and_nothing_stored(self):
        status, _ = self.post_json(self.payload(), header=False)
        self.assertEqual(status, 401)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 0)

    def test_identical_replay_is_idempotent(self):
        payload = self.payload()
        first = self.post_json(payload)
        second = self.post_json(payload)
        self.assertEqual(first, second)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 4)

    def test_unmappable_type_stored_as_companion_type(self):
        payload = {'total_calories': [{'calories': 45.0, 'start_time': '2026-09-20T08:00:00Z',
                                       'end_time': '2026-09-20T08:10:00Z', 'uuid': 'SYNTHETIC-total-cal-1',
                                       'source': 'Apple Watch'}]}
        status, ack = self.post_json(payload)
        self.assertEqual(status, 200)
        self.assertEqual(ack['unmapped_types'], ['total_calories'])
        with self.store.connect() as c:
            row = c.execute('SELECT metric, raw_json FROM observations WHERE native_id=?',
                            ('SYNTHETIC-total-cal-1',)).fetchone()
        self.assertEqual(row[0], 'companion.total_calories')
        self.assertEqual(json.loads(row[1])['metadata']['raw']['calories'], 45.0)

    def test_menstruation_period_has_no_uuid_and_is_stored_unmapped(self):
        payload = {'menstruation_period': [{'start_time': '2026-09-18T00:00:00Z',
                                            'end_time': '2026-09-21T00:00:00Z'}]}
        status, ack = self.post_json(payload)
        self.assertEqual(status, 200)
        self.assertEqual(ack['unmapped_types'], ['menstruation_period'])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations WHERE metric="
                                       "'companion.menstruation_period'").fetchone()[0], 1)

    def test_companion_and_export_copies_share_one_canonical_observation(self):
        xml = ('<?xml version="1.0" encoding="UTF-8"?><HealthData>'
              '<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Apple Watch" sourceVersion="1" '
              'unit="kg" value="72.5" startDate="2026-09-20 08:00:00 -0500" '
              'endDate="2026-09-20 08:00:00 -0500" /></HealthData>')
        apple_export.import_export(self.store, make_zip(self.base / 'export.zip', xml))
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM active_observations').fetchone()[0], 1)
        payload = {'weight': [{'kilograms': 72.5, 'time': '2026-09-20T13:00:00Z',
                               'uuid': 'SYNTHETIC-weight-1', 'source': 'Apple Watch'}]}
        status, _ = self.post_json(payload)
        self.assertEqual(status, 200)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM active_observations').fetchone()[0], 2)
            self.assertEqual([r[0] for r in c.execute('SELECT source_name FROM canonical_observations')],
                             ['Apple Watch'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
