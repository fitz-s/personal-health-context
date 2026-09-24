"""Synthetic Apple export and local TLS ingest protocol tests."""
import hashlib
import http.client
import json
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from phctx import apple_export, ingest
from phctx.store import Store, StoreError, utc_in


XML = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [<!ATTLIST Record sourceName CDATA #IMPLIED>]>
<HealthData>
<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Apple Watch" sourceVersion="1" unit="kg" value="72.5" startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:00:00 -0500" />
<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Oura" sourceVersion="1" unit="kg" value="70.0" startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:00:00 -0500" />
<Workout workoutActivityType="SYNTHETIC.walk" duration="30" durationUnit="min" sourceName="Apple Watch" startDate="2026-09-20 09:00:00 -0500" endDate="2026-09-20 09:30:00 -0500">
<MetadataEntry key="HKTimeZone" value="America/Denver" />
</Workout>
</HealthData>'''


def make_zip(path: Path, xml=XML, member='apple_health_export/export.xml'):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, xml)
    return path


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def read(self):
        return self.body


class AppleExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'store', 'synthetic')
        self.export = make_zip(self.base / 'export.zip')

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_import_filters_restricted_origin_and_is_idempotent(self):
        first = apple_export.import_export(self.store, self.export)
        self.assertEqual(first['counts']['records_seen'], 3)
        self.assertEqual(first['counts']['filtered_restricted'], 1)
        self.assertEqual(first['counts']['imported'], 2)
        with self.store.connect() as c:
            rows = c.execute('SELECT metric, source_name, timezone FROM active_observations ORDER BY metric').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all('Oura' not in row['source_name'] for row in rows))
            self.assertEqual(c.execute("SELECT cursor FROM sources WHERE id='apple_health_export'").fetchone()[0],
                             f"export:v{apple_export.PARSER_VERSION}:{first['export_sha256'][:16]}:0")
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT timezone FROM observations WHERE metric='HKWorkoutTypeIdentifier'")
                             .fetchone()[0], 'America/Denver')
        second = apple_export.import_export(self.store, self.export)
        self.assertEqual(second['counts'], first['counts'])
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 2)

    def test_dry_run_does_not_persist_observations_or_source_cursor(self):
        result = apple_export.import_export(self.store, self.export, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['counts']['imported'], 2)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT cursor FROM sources WHERE id='apple_health_export'").fetchone()[0], None)
            self.assertEqual(c.execute('SELECT count(*) FROM import_runs').fetchone()[0], 0)

    def test_entity_declaration_is_rejected(self):
        unsafe = make_zip(self.base / 'entity.zip', XML.replace('<!ATTLIST Record sourceName CDATA #IMPLIED>',
                                                                '<!ENTITY synthetic "SYNTHETIC">'),
                          'apple_health_export/export.xml')
        with self.assertRaises(StoreError) as caught:
            apple_export.import_export(self.store, unsafe)
        self.assertEqual(caught.exception.code, 'invalid_export')
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 0)

    def test_zip_traversal_member_is_rejected(self):
        unsafe = make_zip(self.base / 'traversal.zip', '<HealthData/>', '../evil')
        with self.assertRaises(StoreError) as caught:
            apple_export.import_export(self.store, unsafe)
        self.assertEqual(caught.exception.code, 'invalid_export')

    def test_zip_without_export_xml_is_rejected(self):
        unsafe = make_zip(self.base / 'missing.zip', '<HealthData/>', 'readme.txt')
        with self.assertRaises(StoreError) as caught:
            apple_export.import_export(self.store, unsafe)
        self.assertEqual(caught.exception.code, 'invalid_export')

    def test_live_and_export_copies_share_one_canonical_observation(self):
        canonical_xml = XML.replace('<Workout workoutActivityType="SYNTHETIC.walk" duration="30" durationUnit="min" sourceName="Apple Watch" startDate="2026-09-20 09:00:00 -0500" endDate="2026-09-20 09:30:00 -0500">\n<MetadataEntry key="HKTimeZone" value="America/Denver" />\n</Workout>', '')
        canonical_export = make_zip(self.base / 'canonical-export.zip', canonical_xml)
        apple_export.import_export(self.store, canonical_export)
        self.store.register_source('apple_health:synthetic-install', 'SYNTHETIC HealthKit')
        sample = dict(native_id='SYNTHETIC-healthkit-id', metric='HKQuantityTypeIdentifierBodyMass',
                      start_at='2026-09-20T08:00:00-05:00', end_at='2026-09-20T08:00:00-05:00',
                      timezone='America/Chicago', value_num=72.5, value_text=None, unit='kg',
                      source_name='Apple Watch', source_bundle_id='SYNTHETIC.healthkit', device={}, metadata={})
        sample['origin_key'] = apple_export.origin_key(sample['metric'], sample['start_at'], sample['end_at'],
                                                       sample['value_num'], sample['value_text'], sample['source_name'])
        self.store.ingest_batch(request_id='SYNTHETIC-live-sample', source_id='apple_health:synthetic-install',
                                samples=[sample], deleted_ids=[], cursor='SYNTHETIC live cursor')
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM active_observations').fetchone()[0], 2)
            self.assertEqual(c.execute('SELECT count(*) FROM canonical_observations').fetchone()[0], 1)


class IngestTLSTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'store', 'synthetic')
        self.cert, self.key, self.fingerprint = ingest.ensure_cert(self.base / 'tls', ['127.0.0.1'])
        der = ssl.PEM_cert_to_DER_cert(self.cert.read_text())
        self.assertEqual(hashlib.sha256(der).hexdigest(), self.fingerprint)
        self.server = ingest.make_server(self.store, '127.0.0.1', 0, self.cert, self.key)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.context = ssl.create_default_context(cafile=str(self.cert))
        self.context.check_hostname = False
        self.base_url = f'https://127.0.0.1:{self.server.server_address[1]}'
        self.installation = 'SYNTHETIC-install-0001'
        self.pairing_code = ingest.new_pairing_code(self.store)
        response = self.request('POST', '/v1/pair', {'pairing_code': self.pairing_code,
                                                    'installation_id': self.installation,
                                                    'device_name': 'SYNTHETIC phone'})
        self.assertEqual(response.status, 200)
        self.paired = json.loads(response.read())
        self.token = self.paired['device_token']
        self.device = ingest.device_for(self.store, self.token)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, payload=None, token=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base_url + path, data=body, method=method,
                                         headers={'Content-Type': 'application/json',
                                                  **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=4) as response:
                return Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            try:
                return Response(error.code, error.read())
            finally:
                error.close()

    def batch(self, sequence, batch_id, *, previous=None, samples=None, deleted=None, installation=None):
        return {'protocol_version': 1, 'installation_id': installation or self.installation,
                'stream': 'HKQuantityTypeIdentifierBodyMass', 'batch_id': batch_id, 'sequence': sequence,
                'previous_batch_id': previous, 'next_anchor_b64': 'SYNTHETIC-anchor',
                'query_completed_at': '2026-09-20T12:00:00Z',
                'samples': samples if samples is not None else [], 'deleted_ids': deleted or [],
                'coverage': {'kind': 'SYNTHETIC complete query'}}

    def sample(self, native_id='SYNTHETIC-sample-1', value=72.5, source_name='Apple Watch'):
        return {'native_id': native_id, 'metric': 'HKQuantityTypeIdentifierBodyMass',
                'start_at': '2026-09-20T08:00:00-05:00', 'end_at': '2026-09-20T08:00:00-05:00',
                'timezone': 'America/Chicago', 'value_num': value, 'value_text': None, 'unit': 'kg',
                'source_bundle_id': 'SYNTHETIC.healthkit', 'source_name': source_name,
                'device': {}, 'metadata': {}}

    def post_batch(self, batch, token=None):
        response = self.request('POST', '/v1/batches', batch, token or self.token)
        return response.status, json.loads(response.read())

    def test_pairing_code_is_single_use_and_expired_code_is_forbidden(self):
        reuse = self.request('POST', '/v1/pair', {'pairing_code': self.pairing_code,
                                                  'installation_id': 'SYNTHETIC-install-0002'})
        self.assertEqual(reuse.status, 403)
        expired = 'SYNTHETIC-EXPIRED'
        with self.store.transaction() as c:
            c.execute('INSERT INTO pairing_codes VALUES(?,?,NULL)', (ingest.sha(expired), utc_in(-60)))
        response = self.request('POST', '/v1/pair', {'pairing_code': expired,
                                                      'installation_id': 'SYNTHETIC-install-0003'})
        self.assertEqual(response.status, 403)

    def test_batch_ack_replay_conflict_gap_and_status(self):
        batch = self.batch(0, 'SYNTHETIC-batch-0', samples=[self.sample()])
        status, ack = self.post_batch(batch)
        self.assertEqual(status, 200)
        self.assertTrue(ack['committed'])
        self.assertEqual(ack, self.post_batch(batch)[1])
        changed = self.batch(0, 'SYNTHETIC-batch-0', samples=[self.sample(value=73.0)])
        self.assertEqual(self.post_batch(changed)[0], 409)
        gap_status, gap = self.post_batch(self.batch(2, 'SYNTHETIC-batch-2', previous='SYNTHETIC-batch-1'))
        self.assertEqual(gap_status, 409)
        self.assertEqual(gap['expected_sequence'], 1)
        status_response = self.request('GET', '/v1/status', token=self.token)
        self.assertEqual(status_response.status, 200)
        stream = json.loads(status_response.read())['streams']['HKQuantityTypeIdentifierBodyMass']
        self.assertEqual(stream['last_sequence'], 0)
        self.assertEqual(stream['last_batch_id'], 'SYNTHETIC-batch-0')

    def test_batch_rejects_wrong_token_installation_and_schema(self):
        batch = self.batch(0, 'SYNTHETIC-auth-batch')
        self.assertEqual(self.post_batch(batch, token='SYNTHETIC-wrong-token')[0], 401)
        mismatch = self.batch(0, 'SYNTHETIC-mismatch', installation='SYNTHETIC-other-install')
        self.assertEqual(self.post_batch(mismatch)[0], 403)
        invalid = self.batch(0, 'SYNTHETIC-invalid')
        del invalid['samples']
        self.assertEqual(self.post_batch(invalid)[0], 422)

    def test_restricted_vendor_sample_is_filtered_and_not_stored(self):
        status, ack = self.post_batch(self.batch(0, 'SYNTHETIC-oura', samples=[self.sample(source_name='Oura')]))
        self.assertEqual(status, 200)
        self.assertEqual(ack['filtered_restricted'], 1)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0], 0)

    def test_deleted_ids_create_tombstones_and_revoked_device_loses_access(self):
        first = self.batch(0, 'SYNTHETIC-store', samples=[self.sample()])
        self.assertEqual(self.post_batch(first)[0], 200)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM active_observations').fetchone()[0], 1)
        deletion = self.batch(1, 'SYNTHETIC-delete', previous='SYNTHETIC-store', deleted=['SYNTHETIC-sample-1'])
        self.assertEqual(self.post_batch(deletion)[0], 200)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM active_observations').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT deleted FROM observations WHERE native_id=?',
                                       ('SYNTHETIC-sample-1',)).fetchone()[0], 1)
        ingest.revoke(self.store, self.installation)
        self.assertEqual(self.request('GET', '/v1/status', token=self.token).status, 401)
        self.assertEqual(self.post_batch(self.batch(2, 'SYNTHETIC-after-revoke', previous='SYNTHETIC-delete'))[0], 401)


if __name__ == '__main__':
    unittest.main(verbosity=2)
