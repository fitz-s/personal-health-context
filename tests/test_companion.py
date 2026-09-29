"""Life Dashboard Companion webhook receiver tests (synthetic)."""
import hashlib
import hmac
import json
import tempfile
import threading
import time
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

    def test_a_retry_arriving_while_the_first_copy_is_writing_waits_and_replays(self):
        """Live: the app timed out after 30 s on a large first sync and re-sent it while the first copy still held the
        write lock; the retry got 503 storage_busy. Both copies must now succeed, the data stored once."""
        from unittest import mock
        real, gate = self.store.ingest_batch, threading.Event()
        inside, peak, count = [0], [0], threading.Lock()

        def slow(**kw):  # the first copy stays inside ingest until the retry has arrived
            with count:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            try:
                gate.wait(5)
                return real(**kw)
            finally:
                with count:
                    inside[0] -= 1
        payload = {'steps': [{'uuid': 'SYNTHETIC-retry', 'start_time': '2026-09-20T08:00:00-05:00',
                              'end_time': '2026-09-20T08:10:00-05:00', 'count': 5, 'source': 'SYNTHETIC'}]}
        out = []
        with mock.patch.object(self.store, 'ingest_batch', side_effect=slow):
            first = threading.Thread(target=lambda: out.append(self.post(payload).status))
            first.start()
            time.sleep(0.3)
            second = threading.Thread(target=lambda: out.append(self.post(payload).status))
            second.start()
            time.sleep(0.3)
            gate.set()
            first.join(5), second.join(5)
        self.assertEqual(sorted(out), [200, 200])
        self.assertEqual(peak[0], 1)  # the retry waited for the first copy instead of racing it for the write lock
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations WHERE native_id='SYNTHETIC-retry'")
                             .fetchone()[0], 1)

    def test_a_write_slower_than_the_apps_timeout_is_acknowledged_in_time_and_applied_later(self):
        """Live: every phone sync took longer than the app's 30 s timeout, so the app showed "Webhook failed" on each
        one although the data was stored. The body is spooled and acknowledged (202) before the timeout."""
        from unittest import mock
        real, gate = self.store.ingest_batch, threading.Event()
        payload = {'steps': [{'uuid': 'SYNTHETIC-slow', 'start_time': '2026-09-20T08:00:00-05:00',
                              'end_time': '2026-09-20T08:10:00-05:00', 'count': 5, 'source': 'SYNTHETIC'}]}
        with mock.patch.object(companion, 'ACK_WAIT_S', 0.3), \
                mock.patch.object(self.store, 'ingest_batch', side_effect=lambda **kw: gate.wait(5) and real(**kw)):
            started = time.monotonic()
            status, ack = self.post_json(payload)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual((status, ack), (202, {'accepted': True, 'queued': True}))
            self.assertEqual(len(list((self.store.root / 'companion-spool').glob('*.json'))), 1)
            gate.set()
            for _ in range(50):
                if not list((self.store.root / 'companion-spool').glob('*.json')):
                    break
                time.sleep(0.1)
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM observations WHERE native_id='SYNTHETIC-slow'")
                             .fetchone()[0], 1)
        self.assertEqual(self.post_json(payload)[0], 200)  # the app's re-send of a queued body replays

    def test_a_body_spooled_before_a_restart_is_applied_at_startup(self):
        body = json.dumps({'steps': [{'uuid': 'SYNTHETIC-spooled', 'start_time': '2026-09-20T08:00:00-05:00',
                                      'end_time': '2026-09-20T08:10:00-05:00', 'count': 7,
                                      'source': 'SYNTHETIC'}]}).encode()
        spool = self.store.root / 'companion-spool'
        (spool / (hashlib.sha256(body).hexdigest() + '.json')).write_bytes(body)
        writer = companion._Writer(self.store, 'America/Chicago')
        self.addCleanup(writer.stop)
        for _ in range(50):
            if not list(spool.glob('*.json')):
                break
            time.sleep(0.1)
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT value_num FROM observations WHERE native_id='SYNTHETIC-spooled'")
                             .fetchone()[0], 7)

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

    def test_active_and_total_calories_keep_separate_identities(self):
        """R7-04: one HealthKit sample sent as active_calories and inside total_calories."""
        from phctx import companion
        rec = {'uuid': 'SYNTHETIC-energy-1', 'calories': 12.5, 'start_time': '2026-09-20T10:00:00Z',
               'end_time': '2026-09-20T10:05:00Z', 'source': 'SYNTHETIC Watch'}
        samples, _, _ = companion.translate({'active_calories': [rec], 'total_calories': [rec]}, 'America/Chicago')
        self.assertEqual(sorted(s['native_id'] for s in samples),
                         ['SYNTHETIC-energy-1', 'companion:total_calories:SYNTHETIC-energy-1'])
        status, _ = self.post_json({'active_calories': [rec]})
        status2, _ = self.post_json({'total_calories': [rec]})
        with self.store.connect() as c:
            rows = dict(c.execute("SELECT native_id, metric FROM active_observations").fetchall())
        self.assertEqual(rows['SYNTHETIC-energy-1'], 'HKQuantityTypeIdentifierActiveEnergyBurned')

    def test_uuidless_record_identity_ignores_array_position(self):
        """R7-06"""
        from phctx import companion
        a = {'start_time': '2026-09-01T00:00:00Z', 'end_time': '2026-09-05T00:00:00Z', 'SYNTHETIC': 1}
        b = {'start_time': '2026-08-01T00:00:00Z', 'end_time': '2026-08-05T00:00:00Z', 'SYNTHETIC': 2}
        first, _, _ = companion.translate({'menstruation_period': [a, b]}, 'America/Chicago')
        second, _, _ = companion.translate({'menstruation_period': [b, a]}, 'America/Chicago')
        self.assertEqual({s['native_id'] for s in first}, {s['native_id'] for s in second})

    def test_a_payload_larger_than_one_store_page_is_applied_whole(self):
        """R7-07"""
        recs = [{'uuid': f'SYNTHETIC-s{i}', 'count': 1, 'start_time': f'2026-09-20T10:{i // 60 % 60:02d}:{i % 60:02d}Z',
                 'end_time': f'2026-09-20T10:{i // 60 % 60:02d}:{i % 60:02d}Z', 'source': 'SYNTHETIC'} for i in range(5001)]
        status, body = self.post_json({'steps': recs})
        self.assertEqual((status, body['upserted']), (200, 5001))

    def test_truncated_heart_rate_gets_no_equivalence_key(self):
        """R7-05: the app sends heart rate as an integer, so it cannot be proven equal to its export copy."""
        from phctx import companion
        [row] = companion.HANDLERS['heart_rate']({'uuid': 'SYNTHETIC-hr', 'bpm': 59, 'time': '2026-09-20T10:00:00Z',
                                                  'source': 'SYNTHETIC'}, 'America/Chicago')
        self.assertIsNone(row['origin_key'])

    def test_malformed_signature_header_is_refused_not_a_crash(self):
        from phctx import companion
        self.assertFalse(companion._verify(b'{}', 'sha256=\u00e9' * 3, SECRET))
        self.assertFalse(companion._verify(b'{}', 'sha1=00', SECRET))
        self.assertTrue(companion._verify(b'{}', sign(b'{}'), SECRET))

    def test_the_apps_catch_all_other_is_not_named_as_healthkit_other(self):
        from phctx import companion
        [row] = companion._exercise({'uuid': 'SYNTHETIC-w', 'type': 'other', 'start_time': '2026-09-20T10:00:00Z',
                                     'end_time': '2026-09-20T11:00:00Z', 'duration_seconds': 3600}, 'America/Chicago')
        self.assertIsNone(row['value_text'])

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
                            ('companion:total_calories:SYNTHETIC-total-cal-1',)).fetchone()
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

    # ---- healthkit_samples: the phctx-local build's generic "All other HealthKit types" records -------------------

    def generic(self, uuid, hk_type, value, unit=None, value_text=None, start='2026-09-20T13:00:00.250Z',
                end='2026-09-20T13:05:00.750Z', source='SYNTHETIC Watch', **extra):
        record = {'uuid': uuid, 'hk_type': hk_type, 'value': value, 'start_time': start, 'end_time': end,
                  'source': source, 'source_bundle_id': 'com.apple.health.SYNTHETIC', **extra}
        if unit is not None:
            record['unit'] = unit
        if value_text is not None:
            record['value_text'] = value_text
        return record

    def export_keys(self, xml_records):
        xml = f'<?xml version="1.0" encoding="UTF-8"?><HealthData>{xml_records}</HealthData>'
        apple_export.import_export(self.store, make_zip(self.base / 'export.zip', xml))
        with self.store.connect() as c:
            return {r[0]: r[1] for r in c.execute("SELECT metric, origin_key FROM observations "
                                                  "WHERE source_id='apple_health_export'")}

    def test_generic_quantity_category_and_workout_share_the_exports_equivalence_key(self):
        times = 'startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:05:00 -0500"'
        exported = self.export_keys(
            f'<Record type="HKQuantityTypeIdentifierVO2Max" sourceName="SYNTHETIC Watch" unit="mL/min·kg" '
            f'value="41.25" {times}/>'
            f'<Record type="HKQuantityTypeIdentifierWalkingSpeed" sourceName="SYNTHETIC Watch" unit="mi/hr" '
            f'value="2.90782" {times}/>'
            f'<Record type="HKCategoryTypeIdentifierAppleStandHour" sourceName="SYNTHETIC Watch" '
            f'value="HKCategoryValueAppleStandHourIdle" {times}/>'
            f'<Workout workoutActivityType="HKWorkoutActivityTypeRowing" duration="5" durationUnit="min" '
            f'sourceName="SYNTHETIC Watch" {times}/>')
        payload = {'healthkit_samples': [
            self.generic('SYNTHETIC-vo2', 'HKQuantityTypeIdentifierVO2Max', 41.25, 'mL/min·kg'),
            self.generic('SYNTHETIC-speed', 'HKQuantityTypeIdentifierWalkingSpeed', 2.907821, 'mi/hr'),
            self.generic('SYNTHETIC-stand', 'HKCategoryTypeIdentifierAppleStandHour', 1,
                         value_text='HKCategoryValueAppleStandHourIdle'),
            self.generic('SYNTHETIC-row', 'HKWorkoutTypeIdentifier', 300.2, 's',
                         value_text='HKWorkoutActivityTypeRowing', metadata={'total_distance_m': 1000.0}),
        ]}
        live, _, skipped = companion.translate(payload, 'America/Chicago')
        self.assertEqual(skipped, 0)
        self.assertEqual({r['metric']: r['origin_key'] for r in live}, exported)
        status, ack = self.post_json(payload)
        self.assertEqual((status, ack['upserted'], ack['skipped']), (200, 4, 0))
        with self.store.connect() as c:
            rows = {r[0]: tuple(r)[1:] for r in c.execute(
                "SELECT native_id, metric, value_num, value_text, unit, bundle_id FROM observations "
                "WHERE source_id=?", (companion.SOURCE_ID,))}
            canonical = c.execute('SELECT count(*) FROM canonical_observations').fetchone()[0]
        self.assertEqual(rows['SYNTHETIC-vo2'], ('HKQuantityTypeIdentifierVO2Max', 41.25, None, 'mL/min·kg',
                                                 'com.apple.health.SYNTHETIC'))
        self.assertEqual(rows['SYNTHETIC-stand'][:4], ('HKCategoryTypeIdentifierAppleStandHour', 1,
                                                       'HKCategoryValueAppleStandHourIdle', ''))
        self.assertEqual(rows['SYNTHETIC-row'][:4], ('HKWorkoutTypeIdentifier', 300.2, 'HKWorkoutActivityTypeRowing',
                                                     's'))
        self.assertEqual(canonical, 4)  # each live sample and its export copy resolve to one canonical observation

    def test_generic_energy_in_the_exports_cal_matches_a_kcal_export_row(self):
        exported = self.export_keys(
            '<Record type="HKQuantityTypeIdentifierBasalEnergyBurned" sourceName="SYNTHETIC Watch" unit="Cal" '
            'value="1.5" startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:05:00 -0500"/>')
        live, _, _ = companion.translate({'healthkit_samples': [
            self.generic('SYNTHETIC-basal', 'HKQuantityTypeIdentifierBasalEnergyBurned', 1.5, 'kcal')]},
            'America/Chicago')
        self.assertEqual(live[0]['origin_key'], exported['HKQuantityTypeIdentifierBasalEnergyBurned'])

    def test_malformed_generic_records_are_skipped_and_counted_not_fatal(self):
        good = self.generic('SYNTHETIC-good', 'HKQuantityTypeIdentifierFlightsClimbed', 2, 'count')
        bad = [
            'not a record',
            {**good, 'uuid': ''},
            {**good, 'uuid': 'SYNTHETIC-b1', 'hk_type': None},
            {**good, 'uuid': 'SYNTHETIC-b2', 'value': 'high'},
            {**good, 'uuid': 'SYNTHETIC-b3', 'value': True},
            {**good, 'uuid': 'SYNTHETIC-b4', 'start_time': '2026-09-20T13:00:00'},  # no offset
            {**good, 'uuid': 'SYNTHETIC-b5', 'end_time': '2026-09-20T12:00:00Z'},  # ends before it starts
            {**good, 'uuid': 'SYNTHETIC-b6', 'unit': 7},
            {**good, 'uuid': 'SYNTHETIC-b7', 'metadata': {'x': float('nan')}},
            good,  # a repeated uuid in one payload would make the store refuse the whole page
        ]
        body = json.dumps({'healthkit_samples': [good, *bad]}).encode()  # NaN is sent as the literal NaN
        request = urllib.request.Request(self.base_url + '/companion', data=body, method='POST',
                                         headers={'X-Signature': sign(body)})
        with self.opener.open(request, timeout=4) as response:
            ack = json.loads(response.read())
        self.assertEqual((ack['upserted'], ack['skipped']), (1, 10))
        with self.store.connect() as c:
            self.assertEqual([r[0] for r in c.execute('SELECT native_id FROM observations')], ['SYNTHETIC-good'])

    def test_a_generic_record_repeating_a_dedicated_keys_uuid_is_skipped(self):
        payload = self.payload()
        payload['healthkit_samples'] = [self.generic('SYNTHETIC-steps-1', 'HKQuantityTypeIdentifierStepCount', 120,
                                                     'count')]
        status, ack = self.post_json(payload)
        self.assertEqual((status, ack['upserted'], ack['skipped']), (200, 4, 1))

    def test_generic_samples_need_a_valid_signature(self):
        payload = {'healthkit_samples': [self.generic('SYNTHETIC-g', 'HKQuantityTypeIdentifierVO2Max', 40, 'mL/min·kg')]}
        self.assertEqual(self.post_json(payload, secret='SYNTHETIC-wrong-secret')[0], 401)
        self.assertEqual(self.post_json(payload, header=False)[0], 401)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 0)

    def test_exercise_activity_type_names_what_the_apps_other_hides(self):
        [row] = companion._exercise({'uuid': 'SYNTHETIC-w', 'type': 'other', 'start_time': '2026-09-20T10:00:00Z',
                                     'end_time': '2026-09-20T11:00:00Z', 'duration_seconds': 3600,
                                     'activity_type': 'HKWorkoutActivityTypePickleball', 'total_energy_kcal': 410.5},
                                    'America/Chicago')
        self.assertEqual((row['value_text'], row['metadata']['totals']),
                         ('HKWorkoutActivityTypePickleball', {'total_energy_kcal': 410.5}))


if __name__ == '__main__':
    unittest.main(verbosity=2)


class LanAddressTests(unittest.TestCase):
    def test_a_proxy_tunnel_default_route_is_not_offered_to_the_phone(self):
        """Live: a proxy client's utun (198.18.0.1) took the default route, so the webhook URL named an address the
        phone cannot reach; the Wi-Fi address must be offered instead."""
        from unittest import mock
        from phctx import ingest
        ifconfig = ('lo0: flags=8049\n\tinet 127.0.0.1 netmask 0xff000000\n'
                    'en0: flags=8863\n\tinet 192.168.0.85 netmask 0xffffff00 broadcast 192.168.0.255\n'
                    'utun10: flags=8051\n\tinet 198.18.0.1 --> 198.18.0.1 netmask 0xfffe0000\n'
                    'en12: flags=8863\n\tinet 169.254.79.112 netmask 0xffff0000\n')
        sock = mock.MagicMock()
        sock.getsockname.return_value = ('198.18.0.1', 0)
        with mock.patch.object(ingest.socket, 'socket', return_value=sock), \
                mock.patch.object(ingest.subprocess, 'run', return_value=mock.Mock(stdout=ifconfig)):
            self.assertEqual(ingest.lan_addresses(), ['127.0.0.1', '192.168.0.85'])
