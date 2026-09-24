"""Apple export mapping: identity, equivalence, preservation, calendar days, paging, originals, CDA (all synthetic)."""
import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from phctx import apple_export
from phctx.store import Store, StoreError, instant

HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<HealthData>\n<ExportDate value="2026-09-21 10:00:00 -0500"/>\n'
T = 'startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:00:00 -0500"'
ok = apple_export.origin_key


def rec(value='72.5', unit='kg', src='SYNTHETIC Watch', extra='', body=''):
    return (f'<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="{src}" unit="{unit}" value="{value}" '
            f'{T} {extra}>{body}</Record>\n')


def summary(day):
    return (f'<ActivitySummary dateComponents="{day}" activeEnergyBurned="400" activeEnergyBurnedGoal="500" '
            'activeEnergyBurnedUnit="Cal" appleExerciseTime="30" appleExerciseTimeGoal="30" '
            'appleStandHours="10" appleStandHoursGoal="12"/>\n')


def make(path: Path, body: str, members: dict | None = None) -> Path:
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('apple_health_export/export.xml', HEAD + body + '</HealthData>')
        for name, data in (members or {}).items():
            z.writestr('apple_health_export/' + name, data)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'store', 'synthetic')
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, body, members=None, store=None, tz='America/Chicago', **kw):
        self.n += 1
        return apple_export.import_export(store or self.store, make(self.base / f'e{self.n}.zip', body, members),
                                          tz=tz, **kw)

    def rows(self, where='1=1', table='active_observations', store=None):
        with (store or self.store).connect() as c:
            return [dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY native_id')]

    def meta(self, where):
        return json.loads(self.rows(where)[0]['raw_json'])['metadata']

    def attachments(self):
        with self.store.connect() as c:
            return [(json.loads(r[0]), r[1]) for r in
                    c.execute("SELECT payload_json, occurred_at FROM records WHERE kind='attachment'")]


class OriginKeyTests(Base):
    """The importer's origin_key equals the key of the iPhone helper's encoding of the same sample."""
    SLEEP = 'HKCategoryTypeIdentifierSleepAnalysis'

    def test_category_live_without_unit_matches_export(self):
        live = ok(self.SLEEP, '2026-09-20T01:00:00-05:00', '2026-09-20T02:00:00-05:00', 3,
                  'HKCategoryValueSleepAnalysisAsleepCore', unit=None, source_name='SYNTHETIC Watch')
        export = ok(self.SLEEP, '2026-09-20T06:00:00+00:00', '2026-09-20T07:00:00+00:00', None,
                    'HKCategoryValueSleepAnalysisAsleepCore', '', ' SYNTHETIC Watch ')
        self.assertEqual(live, export)
        self.assertNotEqual(live, ok(self.SLEEP, '2026-09-20T01:00:00-05:00', '2026-09-20T02:00:00-05:00', 3,
                                     'HKCategoryValueSleepAnalysisInBed', None, 'SYNTHETIC Watch'))

    def test_unit_normalization_and_none_equals_empty(self):
        k = lambda u: ok('M', '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 1.0, None, u, 'S')
        self.assertEqual(k('Cal'), k('kcal'))
        self.assertEqual(k(None), k(''))
        self.assertNotEqual(k('kg'), k('lb'))

    def test_workout_key_uses_activity_type_and_rounded_seconds(self):
        k = lambda v, s, u=None: ok('HKWorkoutTypeIdentifier', '2026-09-20T09:00:00-05:00',
                                    '2026-09-20T09:30:00-05:00', s, v, u, 'SYNTHETIC Watch')
        self.assertEqual(k('HKWorkoutActivityTypeRunning', 1800.4), k('HKWorkoutActivityTypeRunning', 1799.6, 's'))
        self.assertNotEqual(k('HKWorkoutActivityTypeRunning', 1800), k('HKWorkoutActivityTypeWalking', 1800))

    def test_export_rows_match_live_encoding(self):
        self.run_import(
            f'<Record type="{self.SLEEP}" sourceName="SYNTHETIC Watch" value="HKCategoryValueSleepAnalysisAsleepCore" '
            'startDate="2026-09-20 01:00:00 -0500" endDate="2026-09-20 02:00:00 -0500"/>\n'
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30.0067" durationUnit="min" '
            'sourceName="SYNTHETIC Watch" startDate="2026-09-20 09:00:00 -0500" endDate="2026-09-20 09:30:00 -0500"/>\n'
            f'<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" sourceName="SYNTHETIC app" unit="Cal" '
            f'value="250" {T}/>\n')
        keys = {r['metric']: r['origin_key'] for r in self.rows()}
        self.assertEqual(keys[self.SLEEP], ok(self.SLEEP, '2026-09-20T01:00:00-05:00', '2026-09-20T02:00:00-05:00', 3,
                                              'HKCategoryValueSleepAnalysisAsleepCore', None, 'SYNTHETIC Watch'))
        self.assertEqual(keys['HKWorkoutTypeIdentifier'], ok(
            'HKWorkoutTypeIdentifier', '2026-09-20T09:00:00-05:00', '2026-09-20T09:30:00-05:00', 1800.4,
            'HKWorkoutActivityTypeRunning', 's', 'SYNTHETIC Watch'))
        self.assertEqual(keys['HKQuantityTypeIdentifierDietaryEnergyConsumed'], ok(
            'HKQuantityTypeIdentifierDietaryEnergyConsumed', '2026-09-20T13:00:00Z', '2026-09-20T13:00:00Z', 250.0,
            None, 'kcal', 'SYNTHETIC app'))


class IdentityTests(Base):
    def test_records_differing_in_unit_device_metadata_or_unrounded_value_stay_distinct(self):
        body = (rec() + rec(unit='lb') + rec(extra='device="SYNTHETIC device B"') +
                rec(body='<MetadataEntry key="SYNTHETIC" value="1"/>') + rec(value='72.50001') +
                rec(value='72.50002') + rec())  # the last is an exact duplicate of the first: kept once
        out = self.run_import(body)
        ids = [r['native_id'] for r in self.rows()]
        self.assertEqual(len(ids), 6)
        self.assertTrue(all(i.startswith('x2:') and len(i) == 43 for i in ids))
        self.assertEqual(out['counts']['imported'], 7)
        self.run_import(body)
        self.assertEqual([r['native_id'] for r in self.rows()], ids)

    def test_any_child_difference_changes_identity(self):
        hrv = lambda bpm: rec(body='<HeartRateVariabilityMetadataList><InstantaneousBeatsPerMinute '
                                   f'bpm="{bpm}" time="8:00:01.00 AM"/></HeartRateVariabilityMetadataList>')
        self.run_import(hrv(60) + hrv(61))
        self.assertEqual(len(self.rows()), 2)


class PreservationTests(Base):
    def test_workout_and_heartbeat_children_are_preserved_and_unsupported_counted(self):
        events = ''.join(f'<WorkoutEvent type="HKWorkoutEventTypeLap" date="2026-09-20 09:{i // 10:02d}:00 -0500" '
                         f'duration="{i}" durationUnit="s"/>' for i in range(250))
        body = ('<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" '
                'totalDistance="5.1" totalDistanceUnit="km" totalEnergyBurned="300" totalEnergyBurnedUnit="kcal" '
                'sourceName="SYNTHETIC Watch" startDate="2026-09-20 09:00:00 -0500" endDate="2026-09-20 09:30:00 -0500">'
                '<MetadataEntry key="HKIndoorWorkout" value="0"/>' + events +
                '<WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" startDate="2026-09-20 09:00:00 -0500" '
                'endDate="2026-09-20 09:30:00 -0500" average="150" minimum="90" maximum="180" unit="count/min" '
                'synthetic="kept"/>'
                '<WorkoutRoute sourceName="SYNTHETIC Watch"><MetadataEntry key="HKMetadataKeySyncVersion" value="2"/>'
                '<FileReference path="/workout-routes/route_1.gpx"/></WorkoutRoute>'
                '<WorkoutActivity uuid="SYNTHETIC"><WorkoutStatistics type="X"/></WorkoutActivity>'
                '</Workout>\n' +
                rec(body='<HeartRateVariabilityMetadataList>' +
                    ''.join(f'<InstantaneousBeatsPerMinute bpm="{60 + i}" time="8:00:0{i}.00 AM"/>' for i in range(5)) +
                    '</HeartRateVariabilityMetadataList>') +
                f'<Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="SYNTHETIC cuff" {T}>'
                f'<Record type="HKQuantityTypeIdentifierBloodPressureSystolic" sourceName="SYNTHETIC cuff" '
                f'unit="mmHg" value="118" {T}/></Correlation>\n')
        out = self.run_import(body)
        w = self.meta("metric='HKWorkoutTypeIdentifier'")
        self.assertEqual(w['HKIndoorWorkout'], '0')
        self.assertEqual(len(w['events']), 250)
        self.assertEqual(w['events'][249]['duration'], '249')
        self.assertEqual(w['statistics'][0]['synthetic'], 'kept')
        self.assertEqual(w['statistics'][0]['startDate'], '2026-09-20 09:00:00 -0500')
        self.assertEqual(w['totals'], {'duration': {'value': '30', 'unit': 'min'},
                                       'totalDistance': {'value': '5.1', 'unit': 'km'},
                                       'totalEnergyBurned': {'value': '300', 'unit': 'kcal'}})
        self.assertEqual(w['routes'][0]['children'][0]['attrs'], {'key': 'HKMetadataKeySyncVersion', 'value': '2'})
        self.assertEqual(w['route_files'], ['/workout-routes/route_1.gpx'])
        self.assertEqual(w['other_children'][0]['tag'], 'WorkoutActivity')
        r = self.meta("metric='HKQuantityTypeIdentifierBodyMass'")
        self.assertEqual(r['hrv_metadata_lists'][0][4], {'bpm': '64', 'time': '8:00:04.00 AM'})
        self.assertEqual(len(self.rows("metric='HKQuantityTypeIdentifierBloodPressureSystolic'")), 1)
        c = out['counts']
        self.assertEqual(c['unsupported_elements'], {'WorkoutActivity': 1, 'Correlation': 1})
        self.assertEqual(c['elements']['WorkoutEvent'], 250)
        self.assertEqual(c['elements']['InstantaneousBeatsPerMinute'], 5)
        self.assertEqual(c['elements']['Record'], 2)

    def test_metadata_entry_colliding_with_a_derived_field_is_not_overwritten(self):
        self.run_import(rec(body='<MetadataEntry key="import" value="SYNTHETIC app value"/>'
                                 '<MetadataEntry key="source_version" value="SYNTHETIC 2"/>'))
        md = self.meta("metric='HKQuantityTypeIdentifierBodyMass'")
        self.assertEqual(md['import']['parser'], apple_export.PARSER_VERSION)
        self.assertEqual(md['colliding_metadata_entries'], {'import': 'SYNTHETIC app value',
                                                           'source_version': 'SYNTHETIC 2'})


class ActivitySummaryTests(Base):
    def test_local_day_window_on_ordinary_and_dst_days(self):
        zone = ZoneInfo('America/Chicago')
        days = {'2026-06-10': 24, '2026-03-08': 23, '2026-11-01': 25}
        self.run_import(''.join(summary(d) for d in days))
        rows = self.rows("metric LIKE 'ActivitySummary.%'")
        self.assertEqual(len(rows), 9)
        for r in rows:
            day = r['native_id'].split(':')[1]
            s = datetime.fromisoformat(r['start_at']).astimezone(zone)
            e = datetime.fromisoformat(r['end_at']).astimezone(zone)
            self.assertEqual((s.date().isoformat(), s.hour, s.minute, e.hour, e.minute), (day, 0, 0, 0, 0))
            elapsed = datetime.fromisoformat(r['end_at']) - datetime.fromisoformat(r['start_at'])  # UTC instants
            self.assertEqual(elapsed.total_seconds(), days[day] * 3600)
            md = json.loads(r['raw_json'])['metadata']
            self.assertEqual((md['local_date'], md['import']['parser']), (day, apple_export.PARSER_VERSION))
            self.assertEqual(r['timezone'], 'America/Chicago')
        tokyo = Store(self.base / 'tokyo', 'synthetic')
        self.run_import(''.join(summary(d) for d in days), store=tokyo, tz='Asia/Tokyo')
        other = self.rows("metric LIKE 'ActivitySummary.%'", store=tokyo)
        self.assertEqual([r['native_id'] for r in other], [r['native_id'] for r in rows])
        self.assertNotEqual(other[0]['start_at'], rows[0]['start_at'])


class PagingTests(Base):
    def test_summary_after_4999_rows_never_exceeds_batch_limit(self):
        sizes = []
        real = self.store.ingest_batch

        def spy(**kw):
            sizes.append(len(kw['samples']))
            return real(**kw)
        with mock.patch.object(self.store, 'ingest_batch', spy):
            out = self.run_import(''.join(rec(value=str(i)) for i in range(4999)) + summary('2026-09-19'))
        self.assertEqual(out['counts']['imported'], 5002)
        self.assertLessEqual(max(sizes), 5000)
        self.assertEqual(len(self.rows()), 5002)

    def test_date_only_and_naive_since_are_local_to_the_user(self):
        # A date or naive time is read in the user's timezone, not UTC. The sample is 08:00 -0500 (13:00Z).
        self.assertEqual(apple_export._since('2026-09-20', 'Asia/Tokyo'), instant('2026-09-20T00:00:00+09:00'))
        self.assertEqual(apple_export._since('2026-09-20T12:30:00', 'America/Chicago'),
                         instant('2026-09-20T12:30:00-05:00'))
        out = self.run_import(rec(), since='2026-09-20T07:30:00', tz='America/Chicago')  # 12:30Z < 13:00Z
        self.assertEqual(out['counts']['imported'], 1)
        out = self.run_import(rec(), since='2026-09-20T08:30:00', tz='America/Chicago')  # 13:30Z > 13:00Z
        self.assertEqual(out['counts']['skipped_before_since'], 1)

    def test_since_is_compared_as_instant_and_bound_into_request_ids(self):
        path = make(self.base / 'since.zip', rec() + (
            '<Workout workoutActivityType="HKWorkoutActivityTypeWalking" duration="30" durationUnit="min" '
            'sourceName="SYNTHETIC Watch" startDate="2026-09-20 09:00:00 -0500" endDate="2026-09-20 09:30:00 -0500"/>\n'))
        a = apple_export.import_export(self.store, path, since='2026-09-20T13:30:00Z')
        self.assertEqual((a['counts']['imported'], a['counts']['skipped_before_since']), (1, 1))
        self.assertEqual(len(self.rows()), 1)
        b = apple_export.import_export(self.store, path, since='2026-09-20T12:00:00Z')
        self.assertEqual(b['counts']['imported'], 2)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(apple_export.import_export(self.store, path)['counts']['imported'], 2)


class RetirementTests(Base):
    def legacy(self, native_id, value, metadata=None):
        at = '2026-09-20T08:00:00-05:00'  # the key migration v6 would have recomputed for this row
        return dict(native_id=native_id, metric='HKQuantityTypeIdentifierBodyMass', start_at=at, end_at=at,
                    timezone='America/Chicago', value_num=value, value_text=None, unit='kg',
                    source_name='SYNTHETIC Watch', source_bundle_id='SYNTHETIC Watch', device={},
                    metadata=metadata or {},
                    origin_key=ok('HKQuantityTypeIdentifierBodyMass', at, at, value, None, 'kg', 'SYNTHETIC Watch'))

    def seed_v3(self):
        other = {'import': {'parser': apple_export.PARSER_VERSION, 'export': 'f' * 16}}
        self.store.ingest_batch(request_id='SYNTHETIC-v3', source_id=apple_export.SOURCE, deleted_ids=[],
                                cursor='SYNTHETIC v3', samples=[self.legacy('x:' + 'a' * 40, 72.5),
                                                                self.legacy('x:' + 'b' * 40, 71.0),
                                                                self.legacy('x2:' + 'c' * 40, 70.0, other)])

    def attribute_legacy_to(self, body):
        """What migration v6 records when the DB only ever imported this one export."""
        import hashlib
        sha = hashlib.sha256(make(self.base / 'probe.zip', body).read_bytes()).hexdigest()
        with self.store.transaction() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES('export_legacy_owner', ?)", (sha[:16],))

    def test_attributed_v3_rows_are_retired_and_other_exports_kept(self):
        self.seed_v3()
        body = rec() + rec(value='71.0')
        self.attribute_legacy_to(body)
        self.assertEqual(self.run_import(body, since='2026-09-21T00:00:00Z')['counts']['retired'], 0)
        self.assertEqual(self.run_import(body)['counts']['retired'], 2)
        active = [r['native_id'] for r in self.rows()]
        self.assertEqual(len(active), 3)
        self.assertTrue(all(i.startswith('x2:') for i in active))
        self.assertIn('x2:' + 'c' * 40, active)
        self.assertEqual(len(self.rows("native_id LIKE 'x:%' AND deleted=1", 'observations')), 2)
        with self.store.connect() as c:
            n = c.execute('SELECT n FROM observation_catalog WHERE source_id=? AND metric=?',
                          (apple_export.SOURCE, 'HKQuantityTypeIdentifierBodyMass')).fetchone()[0]
        self.assertEqual(n, 3)
        self.assertEqual(self.run_import(body)['counts']['retired'], 0)

    def test_an_older_row_the_new_parser_did_not_reproduce_is_kept(self):
        self.seed_v3()
        body = rec()  # the new parse reproduces only the 72.5 row; the 71.0 row has no replacement
        self.attribute_legacy_to(body)
        c = self.run_import(body)['counts']
        self.assertEqual((c['retired'], c['unreplaced_older_rows']), (1, 1))
        self.assertIn('x:' + 'b' * 40, [r['native_id'] for r in self.rows()])

    def test_unattributed_v3_rows_of_another_export_are_never_retired(self):
        self.seed_v3()
        self.assertEqual(self.run_import(rec() + rec(value='71.0'))['counts']['retired'], 0)
        self.assertEqual(len(self.rows("native_id LIKE 'x:%' AND deleted=0", 'observations')), 2)


class AttachmentTests(Base):
    GPX = '<?xml version="1.0"?><gpx><trk><name>SYNTHETIC</name></trk></gpx>'

    def workout(self, src, route, start='09:00:00'):
        return (f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" '
                f'sourceName="{src}" startDate="2026-09-20 {start} -0500" endDate="2026-09-20 09:30:00 -0500">'
                f'<WorkoutRoute sourceName="{src}"><FileReference path="/workout-routes/{route}"/></WorkoutRoute>'
                '</Workout>\n')

    def ecg(self, device, recorded=True):
        head = ('Name,SYNTHETIC\n' + ('Recorded Date,2026-09-19 07:15:00 -0500\n' if recorded else '') +
                f'Classification,Sinus Rhythm\nSoftware Version,1.0\nDevice,"{device}"\nSample Rate,512 hertz\n\n')
        return head + '\n'.join(str(i) for i in range(20))

    def test_originals_follow_parent_provenance_and_event_time(self):
        members = {'workout-routes/route_ok.gpx': self.GPX.replace('SYNTHETIC', 'SYNTHETIC ok'),
                   'workout-routes/route_bad.gpx': self.GPX.replace('SYNTHETIC', 'SYNTHETIC bad'),
                   'workout-routes/route_orphan.gpx': self.GPX.replace('SYNTHETIC', 'SYNTHETIC orphan'),
                   'electrocardiograms/ecg_ok.csv': self.ecg('SYNTHETIC Watch'),
                   'electrocardiograms/ecg_bad.csv': self.ecg('SYNTHETIC Oura Ring'),
                   'electrocardiograms/ecg_notime.csv': self.ecg('SYNTHETIC Watch', recorded=False)}
        body = self.workout('SYNTHETIC Watch', 'route_ok.gpx', '09:05:00') + self.workout('Oura', 'route_bad.gpx')
        out = self.run_import(body, members)
        got = {Path(p['export_path']).name: (p, at) for p, at in self.attachments()}
        self.assertEqual(sorted(got), ['ecg_bad.csv', 'ecg_notime.csv', 'ecg_ok.csv', 'route_bad.gpx', 'route_ok.gpx'])
        self.assertEqual(got['route_ok.gpx'][1], instant('2026-09-20T09:05:00-05:00'))
        self.assertEqual(got['ecg_ok.csv'][1], instant('2026-09-19T07:15:00-05:00'))
        self.assertNotIn('event_time_unknown', got['ecg_ok.csv'][0])
        self.assertTrue(got['ecg_notime.csv'][0]['event_time_unknown'])
        self.assertEqual(got['ecg_notime.csv'][1], instant('2026-09-21T10:00:00-05:00'))
        blobs = b''.join(p.read_bytes() for p in (self.store.root / 'objects').rglob('*') if p.is_file())
        self.assertNotIn(b'SYNTHETIC orphan', blobs)  # a route no workout references is still skipped
        c = out['counts']
        self.assertEqual((c['attachments'], c['attachments_unreferenced']), (5, 1))

    def test_oversized_member_is_refused_before_reading(self):
        reads = []
        real = zipfile.ZipExtFile.read

        def spy(f, n=-1):
            reads.append(f.name)
            return real(f, n)
        with mock.patch.object(apple_export, 'MAX_ATTACHMENT', 1000), \
                mock.patch.object(zipfile.ZipExtFile, 'read', spy):
            out = self.run_import(self.workout('SYNTHETIC Watch', 'route_big.gpx'),
                                  {'workout-routes/route_big.gpx': self.GPX.replace('SYNTHETIC', 'X' * 5000)})
        self.assertEqual(out['counts']['attachments_refused_oversize'], 1)
        self.assertEqual(self.attachments(), [])
        self.assertNotIn('apple_health_export/workout-routes/route_big.gpx', reads)


    def test_member_larger_than_declared_is_never_read_past_the_cap(self):
        sizes = []
        real = zipfile.ZipExtFile.read

        def spy(f, n=-1):
            sizes.append(n)
            return real(f, n)
        with mock.patch.object(apple_export, 'MAX_ATTACHMENT', 1000), \
                mock.patch.object(zipfile.ZipExtFile, 'read', spy):
            self.run_import(self.workout('SYNTHETIC Watch', 'r.gpx'), {'workout-routes/r.gpx': self.GPX})
        self.assertNotIn(-1, sizes)

    def test_corrupt_member_fails_the_run_instead_of_leaving_it_running(self):
        z = make(self.base / 'bad.zip', self.workout('SYNTHETIC Watch', 'r.gpx'), {'workout-routes/r.gpx': self.GPX})
        data = bytearray(z.read_bytes())
        at = data.find(b'<gpx')
        if at < 0:  # deflated: corrupt the member's stored CRC in its central directory entry instead
            cd = data.rfind(b'PK\x01\x02')
            data[cd + 16] ^= 0xFF
        else:
            data[at] ^= 0xFF
        z.write_bytes(bytes(data))
        with self.assertRaises(StoreError) as e:
            apple_export.import_export(self.store, z, tz='America/Chicago')
        self.assertEqual(e.exception.code, 'export_corrupt')
        with self.store.connect() as c:
            self.assertEqual([r[0] for r in c.execute('SELECT status FROM import_runs')], ['failed'])

class MetadataTests(Base):
    def test_repeated_metadata_key_keeps_every_value(self):
        body = rec(body='<MetadataEntry key="K" value="1"/><MetadataEntry key="K" value="2"/>')
        self.run_import(body)
        self.assertEqual(self.meta('1=1')['K'], ['1', '2'])

class CDATests(Base):
    def obs(self, typ, code, value, unit, low):
        return (f'<component><observation classCode="OBS" moodCode="EVN"><code code="{code}" '
                'codeSystem="2.16.840.1.113883.6.1"/>'
                f'<text><sourceName>SYNTHETIC Watch</sourceName><value>{value}</value><type>{typ}</type>'
                f'<unit>{unit}</unit></text><effectiveTime><low value="{low}"/><high value="{low}"/></effectiveTime>'
                f'<value xsi:type="PQ" value="{value}" unit="{unit}"/></observation></component>')

    def test_cda_is_measured_against_export_records(self):
        body = ('<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="SYNTHETIC Watch" unit="count/min" '
                'value="72" startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:00:00 -0500"/>\n'
                '<Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="SYNTHETIC Watch" unit="%" '
                'value="0.97" startDate="2026-09-20 08:05:00 -0500" endDate="2026-09-20 08:05:00 -0500"/>\n')
        cda = ('<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3" '
               'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><component><structuredBody>' +
               self.obs('HKQuantityTypeIdentifierHeartRate', '8867-4', '72', 'count/min', '20260920080000-0500') +
               self.obs('HKQuantityTypeIdentifierOxygenSaturation', '2708-6', '0.97', '%', '20260920080500-0500') +
               self.obs('HKQuantityTypeIdentifierRespiratoryRate', '9279-1', '15', 'count/min', '20260920081000-0500') +
               self.obs('HKQuantityTypeIdentifierBodyMass', '29463-7', '72', 'kg', '20260920081000-0500') +
               '</structuredBody></component></ClinicalDocument>')
        out = self.run_import(body, {'export_cda.xml': cda})
        c = out['counts']['cda']
        self.assertEqual((c['sampled'], c['matched'], c['unmatched']), (3, 2, 1))
        self.assertEqual(c['by_type']['HKQuantityTypeIdentifierRespiratoryRate'], {'matched': 0, 'unmatched': 1})
        with self.store.connect() as con:
            stored = json.loads(con.execute('SELECT counts_json FROM import_runs').fetchone()[0])
        self.assertEqual(stored['cda'], c)
        self.assertEqual(stored['elements']['Record'], 2)


    def test_trailing_entries_after_the_document_never_block_the_import(self):
        # Real Apple exports append <entry> elements after </ClinicalDocument>.
        body = ('<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="SYNTHETIC Watch" unit="count/min" '
                'value="72" startDate="2026-09-20 08:00:00 -0500" endDate="2026-09-20 08:00:00 -0500"/>\n')
        cda = ('<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3" '
               'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><component><structuredBody>' +
               self.obs('HKQuantityTypeIdentifierHeartRate', '8867-4', '72', 'count/min', '20260920080000-0500') +
               '</structuredBody></component></ClinicalDocument>\n<entry typeCode="DRIV"><organizer/></entry>')
        out = self.run_import(body, {'export_cda.xml': cda})
        c = out['counts']['cda']
        self.assertEqual((c['sampled'], c['matched'], c['malformed']), (1, 1, True))
        self.assertEqual(len(self.rows()), 1)

    def test_sample_stops_at_the_cap_and_counts_unparseable(self):
        good = ''.join(self.obs('HKQuantityTypeIdentifierHeartRate', '8867-4', str(60 + i % 30), 'count/min',
                                f'202609200{i // 60 % 10}{i % 60:02d}00-0500') for i in range(5))
        bad = self.obs('HKQuantityTypeIdentifierHeartRate', '8867-4', 'x', 'count/min', 'not-a-time')
        cda = ('<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3" '
               'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><component><structuredBody>' + bad + good +
               '</structuredBody></component></ClinicalDocument>')
        with mock.patch.object(apple_export, 'CDA_SAMPLE', 3):
            c = self.run_import(rec(), {'export_cda.xml': cda})['counts']['cda']
        self.assertEqual((c['sampled'], c['unparseable']), (3, 1))

if __name__ == '__main__':
    unittest.main(verbosity=2)
