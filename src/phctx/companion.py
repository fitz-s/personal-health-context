"""Life Dashboard Companion (MIT, github.com/owen282000/LifeDashboardCompanion) webhook receiver.

The companion app is a free, open-source iPhone app the owner builds and runs themselves via Xcode
(ios/COMPANION_SETUP.md); it is not developed in this repo. It POSTs one JSON object per sync —
keyed by HealthKit data type ("steps", "heart_rate", "sleep", ...) plus timestamp/app_version/source —
to a webhook URL, signed over the raw body with HMAC-SHA256 in an `X-Signature: sha256=<hex>` header
(LifeDashboardCompanion/Managers/WebhookManager.swift + WebhookSigner.swift). It never sends deletions:
HealthKit samples are only ever added to the arrays it pushes.

Each record is translated here to the same metric identifier / unit / origin_key that
src/phctx/apple_export.py (bulk export) and ios/HealthSyncCore's live helper use
(contracts/normalization.md), so a companion sample and its later bulk-export copy resolve to one
canonical observation via `supersessions`. A record this module cannot pin to one real HealthKit
identifier without guessing is stored as metric `companion.<type>`, raw fields intact, under
UNFAITHFUL_TYPES below — never a fabricated identifier.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .apple_export import origin_key
from .ingest import _linger_close, lan_addresses
from .store import Store, StoreError, instant

log = logging.getLogger('phctx.companion')

SOURCE_ID = 'apple_health:companion'
SOURCE_LABEL = 'Apple Health via Life Dashboard Companion (iPhone webhook)'
KEYCHAIN_SERVICE = 'phctx-companion-secret'
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 47823

CATEGORY = 'HKCategoryTypeIdentifier'

# Companion record types with no faithful single HealthKit identifier: kept as companion.<type> with
# raw fields rather than guessed. See module docstring and contracts/normalization.md.
#  - total_calories: the app merges HKQuantityTypeIdentifierActiveEnergyBurned and
#    ...BasalEnergyBurned into one array with no field saying which type a given record is.
#  - menstruation_period: derived client-side (MenstruationPeriodBuilder) from consecutive flow
#    days; HealthKit has no period sample type, so there is no HK identifier to assign at all.
UNFAITHFUL_TYPES = {'total_calories', 'menstruation_period'}

# Sleep stage string -> (HealthKit HKCategoryValueSleepAnalysis raw int, export.xml identifier string).
# Table per contracts/normalization.md / ios/HealthSyncCore/Sources/HealthSyncCore/Normalization.swift.
SLEEP_STAGE = {
    'in_bed': (0, 'HKCategoryValueSleepAnalysisInBed'),
    'sleeping': (1, 'HKCategoryValueSleepAnalysisAsleepUnspecified'),
    'awake': (2, 'HKCategoryValueSleepAnalysisAwake'),
    'light': (3, 'HKCategoryValueSleepAnalysisAsleepCore'),
    'deep': (4, 'HKCategoryValueSleepAnalysisAsleepDeep'),
    'rem': (5, 'HKCategoryValueSleepAnalysisAsleepREM'),
    # 'unknown' (the app's own @unknown default) has no HealthKit raw value to report: value_text
    # stays None per Normalization.swift's own rule ("an unmapped value keeps value_text nil").
}

# Menstrual flow string -> (HKCategoryValueMenstrualFlow raw int, export.xml identifier string).
# Not in Normalization.swift's table (that live helper does not request this type yet); these are
# HealthKit's own stable public enum values, not a guess.
MENSTRUAL_FLOW = {
    'unknown': (1, 'HKCategoryValueMenstrualFlowUnspecified'),
    'light': (2, 'HKCategoryValueMenstrualFlowLight'),
    'medium': (3, 'HKCategoryValueMenstrualFlowMedium'),
    'heavy': (4, 'HKCategoryValueMenstrualFlowHeavy'),
}

# HealthKitManager.swift's HKWorkoutActivityType.name switch -> the export.xml workoutActivityType
# suffix from Normalization.swift's `workoutActivities` table, paired by the underlying HK activity.
# 'hiit' is the one place the app's own string does not already match the export suffix; everything
# else lines up after PascalCasing. 'other' is the app's catch-all for any activity its switch does
# not name (including real HK activities Normalization.swift itself does not enumerate): it cannot be
# told apart from a true HKWorkoutActivityTypeOther workout, so this one entry is best-effort, not
# a faithful reconstruction of the original activity, so it is left unnamed (value_text None).
WORKOUT_ACTIVITY = {
    'american_football': 'AmericanFootball', 'archery': 'Archery', 'australian_football': 'AustralianFootball',
    'badminton': 'Badminton', 'baseball': 'Baseball', 'basketball': 'Basketball', 'bowling': 'Bowling',
    'boxing': 'Boxing', 'climbing': 'Climbing', 'cricket': 'Cricket', 'cross_training': 'CrossTraining',
    'curling': 'Curling', 'cycling': 'Cycling', 'dance': 'Dance', 'elliptical': 'Elliptical',
    'equestrian_sports': 'EquestrianSports', 'fencing': 'Fencing', 'fishing': 'Fishing',
    'functional_strength_training': 'FunctionalStrengthTraining', 'golf': 'Golf', 'gymnastics': 'Gymnastics',
    'handball': 'Handball', 'hiking': 'Hiking', 'hockey': 'Hockey', 'hunting': 'Hunting',
    'lacrosse': 'Lacrosse', 'martial_arts': 'MartialArts', 'mind_and_body': 'MindAndBody',
    'paddle_sports': 'PaddleSports', 'play': 'Play', 'preparation_and_recovery': 'PreparationAndRecovery',
    'racquetball': 'Racquetball', 'rowing': 'Rowing', 'rugby': 'Rugby', 'running': 'Running',
    'sailing': 'Sailing', 'skating_sports': 'SkatingSports', 'snow_sports': 'SnowSports', 'soccer': 'Soccer',
    'softball': 'Softball', 'squash': 'Squash', 'stair_climbing': 'StairClimbing',
    'surfing_sports': 'SurfingSports', 'swimming': 'Swimming', 'table_tennis': 'TableTennis',
    'tennis': 'Tennis', 'track_and_field': 'TrackAndField',
    'traditional_strength_training': 'TraditionalStrengthTraining', 'volleyball': 'Volleyball',
    'walking': 'Walking', 'water_fitness': 'WaterFitness', 'water_polo': 'WaterPolo',
    'water_sports': 'WaterSports', 'wrestling': 'Wrestling', 'yoga': 'Yoga', 'pilates': 'Pilates',
    'hiit': 'HighIntensityIntervalTraining', 'core_training': 'CoreTraining', 'flexibility': 'Flexibility',
    'cooldown': 'Cooldown',
}

TRANSPORT = 'life_dashboard_companion'


def _sample(native_id: str, metric: str, start_at: str, end_at: str, tz: str, value_num: float | None,
            value_text: str | None, unit: str | None, source_name: str | None,
            metadata: dict | None = None) -> dict:
    start_at, end_at = instant(start_at), instant(end_at)
    metadata = {'transport': TRANSPORT, **(metadata or {})}
    return {'native_id': native_id, 'metric': metric, 'start_at': start_at, 'end_at': end_at, 'timezone': tz,
            'value_num': value_num, 'value_text': value_text, 'unit': unit, 'source_name': source_name,
            'source_bundle_id': None, 'device': {}, 'metadata': metadata,
            'origin_key': origin_key(metric, start_at, end_at, value_num, value_text, unit, source_name)}


def _unfaithful(kind: str, record: dict, tz: str, native_id_seed: str, *, value_num: float | None = None,
                unit: str | None = None) -> dict:
    native_id = record.get('uuid') or 'companion:' + hashlib.sha256(native_id_seed.encode()).hexdigest()[:40]
    start = record.get('start_time') or record.get('time')
    end = record.get('end_time') or start
    metric = f'companion.{kind}'
    return _sample(native_id, metric, start, end, tz, value_num, None, unit, record.get('source'),
                  {'raw': record})


def _quantity(hk_type: str, unit: str, field: str, *, scale: float = 1.0) -> Callable[[dict, str], list[dict]]:
    """A record with one measured field and either an instant (`time`) or an interval
    (`start_time`/`end_time`), mapped 1:1 to a HealthKit quantity type."""
    def handler(record: dict, tz: str) -> list[dict]:
        start = record.get('start_time') or record.get('time')
        end = record.get('end_time') or start
        value = record.get(field)
        num = None if value is None else value * scale
        return [_sample(record.get('uuid') or '', hk_type, start, end, tz, num, None, unit, record.get('source'))]
    return handler


def _steps(record: dict, tz: str) -> list[dict]:
    return _quantity('HKQuantityTypeIdentifierStepCount', 'count', 'count')(record, tz)


def _blood_pressure(record: dict, tz: str) -> list[dict]:
    time = record.get('time')
    source = record.get('source')
    uuid = record.get('uuid') or ''
    out = [_sample(uuid, 'HKQuantityTypeIdentifierBloodPressureSystolic', time, time, tz,
                  record.get('systolic'), None, 'mmHg', source)]
    if record.get('diastolic') is not None:
        # The companion app attaches only the systolic sample's uuid to this combined record; the
        # diastolic HK sample's own uuid never crosses the wire. native_id is synthesized (stable
        # across retries) so the row can still be stored under its own real HK identifier — the
        # origin_key (metric/window/value/unit/source) is exact, only this id is not a device uuid.
        out.append(_sample(uuid + ':diastolic', 'HKQuantityTypeIdentifierBloodPressureDiastolic', time, time,
                          tz, record['diastolic'], None, 'mmHg', source))
    return out


def _nutrition(record: dict, tz: str) -> list[dict]:
    start = record.get('start_time') or record.get('time')
    end = record.get('end_time') or start
    source = record.get('source')
    uuid = record.get('uuid') or ''
    fields = [('calories', 'HKQuantityTypeIdentifierDietaryEnergyConsumed', 'kcal', ':energy'),
             ('protein_grams', 'HKQuantityTypeIdentifierDietaryProtein', 'g', ':protein'),
             ('carbs_grams', 'HKQuantityTypeIdentifierDietaryCarbohydrates', 'g', ':carbs'),
             ('fat_grams', 'HKQuantityTypeIdentifierDietaryFatTotal', 'g', ':fat')]
    present = [(field, metric, unit, suffix) for field, metric, unit, suffix in fields
              if record.get(field) is not None]
    out = []
    for i, (field, metric, unit, suffix) in enumerate(present):
        # The app attaches one uuid to the whole wire record. A standalone record (protein/carbs/fat
        # logged without calories) has exactly one populated field, and that uuid is genuinely its
        # own — give it unchanged. A record combining several measurements (calories + macros) can
        # only carry one of them under the real uuid; the rest get a synthesized, stable suffix, the
        # same trade-off as the blood-pressure split above.
        native_id = uuid if i == 0 else uuid + suffix
        out.append(_sample(native_id, metric, start, end, tz, record[field], None, unit, source))
    return out


def _sleep(record: dict, tz: str) -> list[dict]:
    out = []
    for stage in record.get('stages') or []:
        num, text = SLEEP_STAGE.get(stage.get('stage'), (None, None))
        out.append(_sample(stage.get('uuid') or '', CATEGORY + 'SleepAnalysis', stage.get('start_time'),
                          stage.get('end_time'), tz, num, text, '', stage.get('source')))
    return out


def _exercise(record: dict, tz: str) -> list[dict]:
    # The app's 'other' is its catch-all for every activity it does not name, not HealthKit's Other: kept unnamed.
    suffix = WORKOUT_ACTIVITY.get(record.get('type'))
    text = 'HKWorkoutActivityType' + suffix if suffix else None
    return [_sample(record.get('uuid') or '', 'HKWorkoutTypeIdentifier', record.get('start_time'),
                   record.get('end_time'), tz, record.get('duration_seconds'), text, 's', record.get('source'))]


def _menstruation_flow(record: dict, tz: str) -> list[dict]:
    num, text = MENSTRUAL_FLOW.get(record.get('flow'), (None, None))
    time = record.get('time')
    return [_sample(record.get('uuid') or '', CATEGORY + 'MenstrualFlow', time, time, tz, num, text, '',
                   record.get('source'))]


def _mindfulness(record: dict, tz: str) -> list[dict]:
    return [_sample(record.get('uuid') or '', CATEGORY + 'MindfulSession', record.get('start_time'),
                   record.get('end_time'), tz, None, None, '', record.get('source'))]


# Faithful, single-record handlers. Units are the companion app's own HKUnit for that field
# (HealthKitManager.swift), confirmed against tests/test_apple_mapping.py where the export.xml
# convention for that exact identifier is already exercised in this repo (steps, calories, weight,
# heart rate family, oxygen saturation, body fat, blood pressure); the rest use the same real
# HealthKit identifier in its own physically-correct unit, not yet cross-checked against a bulk
# export fixture for equivalence-key matching (contracts/normalization.md tolerates a live/export
# unit mismatch — both rows are kept rather than one silently overwriting the other).
HANDLERS: dict[str, Callable[[dict, str], list[dict]]] = {
    'steps': _steps,
    'distance': _quantity('HKQuantityTypeIdentifierDistanceWalkingRunning', 'm', 'meters'),
    'active_calories': _quantity('HKQuantityTypeIdentifierActiveEnergyBurned', 'kcal', 'calories'),
    'weight': _quantity('HKQuantityTypeIdentifierBodyMass', 'kg', 'kilograms'),
    'height': _quantity('HKQuantityTypeIdentifierHeight', 'm', 'meters'),
    'heart_rate': _quantity('HKQuantityTypeIdentifierHeartRate', 'count/min', 'bpm'),
    'resting_heart_rate': _quantity('HKQuantityTypeIdentifierRestingHeartRate', 'count/min', 'bpm'),
    'heart_rate_variability': _quantity('HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'ms',
                                        'heart_rate_variability_millis'),
    'blood_pressure': _blood_pressure,
    'blood_glucose': _quantity('HKQuantityTypeIdentifierBloodGlucose', 'mmol/L', 'mmol_per_liter'),
    # Companion sends 0-100; this repo's '%' convention is the HealthKit 0-1 fraction
    # (tests/test_apple_mapping.py exercises HKQuantityTypeIdentifierOxygenSaturation value="0.97" unit="%").
    'oxygen_saturation': _quantity('HKQuantityTypeIdentifierOxygenSaturation', '%', 'percentage', scale=0.01),
    'body_temperature': _quantity('HKQuantityTypeIdentifierBodyTemperature', 'degC', 'celsius'),
    'respiratory_rate': _quantity('HKQuantityTypeIdentifierRespiratoryRate', 'count/min', 'rate'),
    'body_fat': _quantity('HKQuantityTypeIdentifierBodyFatPercentage', '%', 'percentage', scale=0.01),
    'lean_body_mass': _quantity('HKQuantityTypeIdentifierLeanBodyMass', 'kg', 'kilograms'),
    'hydration': _quantity('HKQuantityTypeIdentifierDietaryWater', 'L', 'liters'),
    'nutrition': _nutrition,
    'sleep': _sleep,
    'exercise': _exercise,
    'mindfulness': _mindfulness,
    'menstruation_flow': _menstruation_flow,
}


def translate(payload: dict[str, Any], tz: str) -> tuple[list[dict], set[str]]:
    """Every companion record in `payload` as phctx observation-batch samples, plus the set of
    top-level type keys (known-unfaithful, or simply not recognized by this version) that were
    stored as companion.<type> instead of a real HealthKit identifier."""
    samples: list[dict] = []
    unmapped: set[str] = set()
    for key, records in payload.items():
        if not isinstance(records, list):
            continue  # timestamp / app_version / source: metadata, not sample arrays
        if key in UNFAITHFUL_TYPES:
            unmapped.add(key)
            value_field = 'calories' if key == 'total_calories' else None
            unit = 'kcal' if key == 'total_calories' else None
            for i, record in enumerate(records):
                samples.append(_unfaithful(key, record, tz, f'{key}:{i}:{record}',
                                          value_num=record.get(value_field) if value_field else None, unit=unit))
            continue
        handler = HANDLERS.get(key)
        if handler is None:
            unmapped.add(key)
            for i, record in enumerate(records):
                samples.append(_unfaithful(key, record, tz, f'{key}:{i}:{record}'))
            continue
        for record in records:
            samples.extend(handler(record, tz))
    return samples, unmapped


def _verify(raw: bytes, header: str, secret: str) -> bool:
    """`header` must be `sha256=<hex hmac-sha256(secret, raw)>`, matching WebhookSigner.swift."""
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    # bytes: a header with non-ASCII characters is a mismatch, not a TypeError
    return hmac.compare_digest(header.encode('utf-8', 'replace'), expected.encode())


def setup_secret(keychain) -> dict:
    """The Keychain secret to sign webhooks with — existing, or freshly generated and stored.
    Returned for the CLI to print once; never written to a log file."""
    secret = keychain.get(KEYCHAIN_SERVICE)
    if not secret:
        secret = secrets.token_urlsafe(32)
        keychain[KEYCHAIN_SERVICE] = secret
    host = next((h for h in lan_addresses() if h != '127.0.0.1'), '127.0.0.1')
    return {'url': f'http://{host}:{DEFAULT_PORT}/companion', 'secret': secret,
           'instructions': 'In Life Dashboard Companion: Settings -> Webhooks -> add this URL as a webhook, '
                           'paste this secret as its HMAC signing secret, then enable the HealthKit types to sync.'}


# ---- HTTP server ------------------------------------------------------------------------------
# Plain HTTP: the companion app speaks LAN HTTP and cannot pin TLS (unlike ios/HealthSyncHelper's
# TLS channel in ingest.py, whose bounded-connection shape this otherwise mirrors). Every request
# must carry a valid HMAC signature or nothing is stored; the raw body and parsed payload are never
# logged (do_POST logs method/path/status only, like ingest.py's log_message).
MAX_BODY = 8 * 1024 * 1024
MAX_CONN = 8
PER_PEER = 2
REQUEST_S = 60
IDLE_S = 20


class _BadRequest(Exception):
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.slots = threading.BoundedSemaphore(MAX_CONN)
        self.peers: dict[str, int] = {}
        self.peer_lock = threading.Lock()

    def _take(self, peer: str) -> bool:
        with self.peer_lock:
            if self.peers.get(peer, 0) >= PER_PEER or not self.slots.acquire(blocking=False):
                return False
            self.peers[peer] = self.peers.get(peer, 0) + 1
            return True

    def _give(self, peer: str) -> None:
        with self.peer_lock:
            self.peers[peer] -= 1
            if not self.peers[peer]:
                del self.peers[peer]
            self.slots.release()

    def process_request(self, request, client_address):
        if not self._take(client_address[0]):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._give(client_address[0])
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._give(client_address[0])

    def finish_request(self, request, client_address):
        try:
            self.RequestHandlerClass(request, client_address, self)
        finally:
            _linger_close(request)

    def handle_error(self, request, client_address):
        log.debug('companion_connection_error', exc_info=True)


def make_server(store: Store, host: str, port: int, secret: str, tz: str) -> ThreadingHTTPServer:
    store.register_source(SOURCE_ID, SOURCE_LABEL, 'durable')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'phctx-companion/1'
        sys_version = ''
        timeout = IDLE_S

        def handle(self):
            # Absolute deadline: a client trickling bytes resets no idle timer.
            self.timer = threading.Timer(REQUEST_S, self.cut)
            self.timer.daemon = True
            self.timer.start()
            try:
                super().handle()
            except OSError:
                pass
            finally:
                self.timer.cancel()

        def cut(self):
            try:
                socket.socket.shutdown(self.connection, socket.SHUT_RDWR)
            except OSError:
                pass

        def log_message(self, fmt, *args):  # redacted access log: method/path/status only
            log.info('%s %s', self.command, self.path.split('?')[0])

        def reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def read_body(self) -> bytes:
            raw_len = self.headers.get('Content-Length') or ''
            if not raw_len.isdigit() or int(raw_len) == 0:
                raise _BadRequest(411 if not raw_len else 400, {'error': 'length_required'})
            n = int(raw_len)
            if n > MAX_BODY:
                raise _BadRequest(413, {'error': 'too_large'})
            data = self.rfile.read(n)
            if len(data) != n:
                raise OSError('request body incomplete before deadline')
            self.timer.cancel()
            return data

        def do_POST(self):  # noqa: N802
            if self.path != '/companion':
                self.reply(404, {'error': 'not_found'})
                return
            try:
                raw = self.read_body()
            except _BadRequest as e:
                self.reply(e.status, e.body)
                return
            if not secret or not _verify(raw, self.headers.get('X-Signature', ''), secret):
                self.reply(401, {'error': 'invalid_signature'})  # nothing stored
                return
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError('payload must be a JSON object')
            except ValueError:
                self.reply(400, {'error': 'malformed_json'})
                return
            samples, unmapped = translate(payload, tz)
            request_id = 'companion:' + hashlib.sha256(raw).hexdigest()  # identical retries are idempotent
            try:
                # cursor must be a function of the request body, not wall-clock time: the store's own
                # idempotency check re-hashes the whole ingest_batch body (cursor included), so a
                # byte-identical retry needs a byte-identical cursor to be recognized as a replay
                # rather than an "idempotency_conflict".
                result = store.ingest_batch(request_id=request_id, source_id=SOURCE_ID, samples=samples,
                                           deleted_ids=[], cursor=request_id,
                                           coverage={'kind': 'life_dashboard_companion_webhook'})
            except StoreError as e:
                status = 503 if e.code in {'storage_busy', 'storage_unavailable'} else 422
                self.reply(status, {'error': e.code})
                return
            except Exception:
                log.exception('companion_ingest_error')  # never the payload: no health values logged
                self.reply(503, {'error': 'storage_unavailable'})
                return
            self.reply(200, {'accepted': True, 'upserted': result['upserted'], 'unmapped_types': sorted(unmapped)})

        def do_GET(self):  # noqa: N802
            self.reply(404, {'error': 'not_found'})

    return _Server((host, port), Handler)


def serve_background(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t
