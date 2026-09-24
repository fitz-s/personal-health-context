"""Oura API v2 → observations (source `oura`). The owner's own data, kept like any other durable source.

One Oura document (a day's sleep score, a sleep period, a workout, a heart-rate sample) is one observation: `native_id`
is the Oura document id (time series: the timestamp), `metric` names the collection, `value_num` carries the
collection's headline number and `raw` keeps the whole document. Each collection is re-read over a trailing window
because Oura revises recent days as the ring syncs; a document gone from a re-read window is a deletion.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import oauth
from .config import keychain_get
from .store import Store, StoreError, dump, utcnow

API = 'https://api.ouraring.com/v2/usercollection/'
SOURCE = 'oura'
TOKEN = ('openclaw-oura-personal-access-token', 'leofitz')  # the owner's token (Secret Pipeline entry)
FIRST_DAY = date(2015, 1, 1)  # before the first Oura ring; a full backfill asks from here
REREAD_DAYS = 14  # Oura revises recent documents as the ring syncs
TIME_SERIES_DAYS = 30  # heartrate / battery: the API caps a request's datetime range

# collection → (headline field, unit, time fields). Time fields: interval start/end, else `day`.
DAILY = {
    'daily_activity': ('score', 'score'), 'daily_readiness': ('score', 'score'), 'daily_sleep': ('score', 'score'),
    'daily_resilience': (None, ''), 'daily_spo2': (None, ''), 'daily_stress': ('stress_high', 's'),
    'daily_cardiovascular_age': ('vascular_age', 'years'), 'vO2_max': ('vo2_max', 'mL/min·kg'),
    'sleep_time': (None, ''), 'tag': (None, ''),
}
INTERVAL = {  # collection → (headline, unit, start field, end field)
    'sleep': ('total_sleep_duration', 's', 'bedtime_start', 'bedtime_end'),
    'workout': ('calories', 'kcal', 'start_datetime', 'end_datetime'),
    'session': (None, '', 'start_datetime', 'end_datetime'),
    'enhanced_tag': (None, '', 'start_time', 'end_time'),
    'rest_mode_period': (None, '', 'start_time', 'end_time'),
}
SERIES = {'heartrate': ('bpm', 'count/min'), 'ring_battery_level': ('level', '%')}


def _get(token: str, collection: str, params: dict) -> dict:
    url = API + collection + '?' + urllib.parse.urlencode(params)
    for attempt in range(5):
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'User-Agent': oauth.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(min(int(e.headers.get('Retry-After') or 30), 300))
                continue
            if e.code in (401, 403):
                raise oauth.OAuthError('oura_auth') from e  # expired/revoked token or missing scope
            if e.code == 404:
                raise oauth.OAuthError('oura_not_found') from e
            raise oauth.OAuthError(f'oura_http_{e.code}') from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise oauth.OAuthError('oura_unreachable') from e
    raise oauth.OAuthError('oura_rate_limited')


def _pages(token: str, collection: str, params: dict):
    params = dict(params)
    while True:
        body = _get(token, collection, params)
        yield from body.get('data', [])
        if not body.get('next_token'):
            return
        params['next_token'] = body['next_token']


def _num(doc: dict, field: str | None) -> float | None:
    v = doc.get(field) if field else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _day_bounds(day: str, tz: str) -> tuple[str, str]:
    from zoneinfo import ZoneInfo
    start = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(tz))
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def sample(collection: str, doc: dict, tz: str) -> dict:
    """One Oura document → one observation (contracts/normalization.md, Oura section)."""
    if collection in SERIES:
        field, unit = SERIES[collection]
        return {'native_id': f'{collection}:{doc["timestamp"]}', 'metric': f'oura.{collection}',
                'start_at': doc['timestamp'], 'end_at': doc['timestamp'], 'value_num': _num(doc, field), 'unit': unit,
                'value_text': doc.get('source'), 'source_name': 'Oura', 'timezone': tz, 'raw': doc}
    if collection in INTERVAL:
        field, unit, a, b = INTERVAL[collection]
        start = doc.get(a) or _day_bounds(doc.get('day') or doc.get('start_day'), tz)[0]
        end = doc.get(b) or start
        text = doc.get('activity') or doc.get('type') or doc.get('tag_type_code')
    else:
        field, unit = DAILY[collection]
        start, end = _day_bounds(doc['day'], tz)
        text = doc.get('day_summary') or doc.get('level')
    return {'native_id': f'{collection}:{doc["id"]}', 'metric': f'oura.{collection}', 'start_at': start,
            'end_at': max(start, end), 'value_num': _num(doc, field), 'unit': unit if _num(doc, field) is not None else '',
            'value_text': text if isinstance(text, str) else None, 'source_name': 'Oura', 'timezone': tz, 'raw': doc}


def _windows(first: date, last: date, days: int):
    while first <= last:
        end = min(first + timedelta(days=days - 1), last)
        yield first, end
        first = end + timedelta(days=1)


def sync(store: Store, tz: str = 'America/Chicago', full: bool = False, token: str | None = None,
         today: date | None = None) -> dict:
    """Backfill (first run or full=True) or re-read the trailing window, one committed page per collection window."""
    token = token or keychain_get(*TOKEN)
    if not token:
        raise oauth.OAuthError('oura_token_missing')
    today = today or datetime.now(timezone.utc).date()
    with store.connect() as c:
        row = c.execute("SELECT cursor FROM sources WHERE id='oura'").fetchone()
    done = json.loads(row[0]) if row and row[0] else {}
    counts: dict[str, int] = {}
    try:
        for collection in [*DAILY, *INTERVAL, *SERIES]:
            since = FIRST_DAY if full or collection not in done else date.fromisoformat(done[collection]) - timedelta(
                days=REREAD_DAYS)
            span = TIME_SERIES_DAYS if collection in SERIES else 3650
            for lo, hi in _windows(since, today + timedelta(days=1), span):
                if collection in SERIES:
                    params = {'start_datetime': f'{lo}T00:00:00+00:00', 'end_datetime': f'{hi}T23:59:59+00:00'}
                else:
                    params = {'start_date': lo.isoformat(), 'end_date': hi.isoformat()}
                docs = list(_pages(token, collection, params))
                _commit(store, collection, lo, hi, docs, tz, done)
                counts[collection] = counts.get(collection, 0) + len(docs)
            done[collection] = today.isoformat()
            _commit_cursor(store, done)
    except oauth.OAuthError as e:
        store.mark_source_attempt(SOURCE, e.code)
        raise
    return {'status': 'synced', 'mode': 'full' if full else 'incremental', 'documents': counts}


def _commit(store: Store, collection: str, lo: date, hi: date, docs: list[dict], tz: str, done: dict) -> None:
    """Upsert this window's documents; any stored document of the window Oura no longer returns is deleted."""
    samples = [sample(collection, d, tz) for d in docs]
    ids = {s['native_id'] for s in samples}
    a, b = _day_bounds(lo.isoformat(), tz)[0], _day_bounds(hi.isoformat(), tz)[1]
    with store.connect() as c:
        gone = [r[0] for r in c.execute(
            "SELECT native_id FROM observations WHERE source_id='oura' AND metric=? AND deleted=0 AND start_at>=? "
            'AND start_at<?', (f'oura.{collection}', a, b)) if r[0] not in ids]
    for i in range(0, max(len(samples), len(gone), 1), 5000):
        page, dels = samples[i:i + 5000], gone[i:i + 5000]
        if not page and not dels and i:
            break
        store.ingest_batch(request_id=f'oura:{collection}:{lo}:{hi}:{i}:{utcnow()}', source_id=SOURCE, samples=page,
                           deleted_ids=dels, cursor=dump(done), coverage={'collection': collection})


def _commit_cursor(store: Store, done: dict) -> None:
    with store.connect() as c:
        c.execute("UPDATE sources SET cursor=? WHERE id='oura'", (dump(done),))
