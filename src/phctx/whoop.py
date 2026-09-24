"""WHOOP API v2 → observations (source `whoop`). The owner's own data, kept like any other durable source.

`phctx connect whoop` gives consent once (phctx.oauth). `phctx sync-whoop` refreshes the access token, then pulls cycles, recoveries, sleeps and workouts: one observation per WHOOP record, the whole record kept,
backfilled once and then re-read over a trailing window (scores settle after the record opens). Upsert only: absence
from a re-read is not treated as deletion (an older response or a mismatched window would hide valid records).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import oauth
from .store import Store, dump, utcnow

API = 'https://api.prod.whoop.com/developer'
SOURCE = 'whoop'
PROVIDER = oauth.Provider('whoop', 'https://api.prod.whoop.com/oauth/oauth2/auth',
                          'https://api.prod.whoop.com/oauth/oauth2/token',
                          'offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement',
                          refresh_scope='offline', state_chars=8)
FIRST_DAY = '2015-01-01T00:00:00Z'  # before the first WHOOP strap
REREAD_DAYS = 14

# collection → (path, metric, headline score field, unit)
COLLECTIONS = {
    'cycle': ('/v2/cycle', 'whoop.cycle', 'strain', 'strain'),
    'recovery': ('/v2/recovery', 'whoop.recovery', 'recovery_score', '%'),
    'sleep': ('/v2/activity/sleep', 'whoop.sleep', 'sleep_performance_percentage', '%'),
    'workout': ('/v2/activity/workout', 'whoop.workout', 'strain', 'strain'),
}


def _get(token: str, path: str, params: dict) -> dict:
    url = API + path + ('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(5):
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'User-Agent': oauth.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(min(int(e.headers.get('Retry-After') or 30), 300))
                continue
            raise oauth.OAuthError('whoop_auth' if e.code in (401, 403) else f'whoop_http_{e.code}') from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise oauth.OAuthError('whoop_unreachable') from e
    raise oauth.OAuthError('whoop_rate_limited')


def _records(token: str, path: str, start: str):
    params = {'limit': 25, 'start': start}
    while True:
        body = _get(token, path, params)
        if not isinstance(body, dict) or not isinstance(body.get('records'), list):  # an HTTP 200 without a collection
            raise oauth.OAuthError('whoop_bad_response')
        yield from body['records']
        if not body.get('next_token'):
            return
        params['nextToken'] = body['next_token']


def sample(collection: str, rec: dict, tz: str) -> dict:
    """One WHOOP record → one observation (contracts/normalization.md, WHOOP section)."""
    _, metric, field, unit = COLLECTIONS[collection]
    score = rec.get('score') or {}
    v = score.get(field)
    num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    rid = rec['cycle_id'] if collection == 'recovery' else rec['id']  # one recovery per cycle; sleep_id is a relation
    start = rec.get('start') or rec['created_at']
    end = rec.get('end') or start  # an open cycle has no end yet
    text = rec.get('sport_name') or ('nap' if rec.get('nap') else None) or rec.get('score_state')
    return {'native_id': f'{collection}:{rid}', 'metric': metric, 'start_at': start, 'end_at': max(start, end),
            'value_num': num, 'unit': unit if num is not None else '', 'value_text': text, 'source_name': 'WHOOP',
            'timezone': tz, 'raw': rec}


def sync(store: Store, tz: str = 'America/Chicago', full: bool = False, token: str | None = None) -> dict:
    store.register_source(SOURCE, 'WHOOP API v2')
    with oauth.exclusive(store.root, SOURCE):  # the refresh and every page that uses its token, together
        return _sync(store, tz, full, token or oauth.access_token(PROVIDER))


def _sync(store: Store, tz: str, full: bool, token: str) -> dict:
    with store.connect() as c:
        row = c.execute("SELECT cursor FROM sources WHERE id='whoop'").fetchone()
    done = json.loads(row[0]) if row and row[0] else {}
    now = datetime.now(timezone.utc)
    counts = {}
    try:
        for collection, (path, *_) in COLLECTIONS.items():
            start = FIRST_DAY if full or collection not in done else (
                datetime.fromisoformat(done[collection]) - timedelta(days=REREAD_DAYS)).isoformat()
            samples = [sample(collection, r, tz) for r in _records(token, path, start)]
            for i in range(0, len(samples), 5000):  # upsert only; see the module docstring
                store.ingest_batch(request_id=f'whoop:{collection}:{start}:{i}:{utcnow()}', source_id=SOURCE,
                                   samples=samples[i:i + 5000], deleted_ids=[], cursor=dump(done),
                                   coverage={'collection': collection})
            counts[collection] = len(samples)
            done[collection] = now.isoformat()
            with store.connect() as c:
                c.execute("UPDATE sources SET cursor=? WHERE id='whoop'", (dump(done),))
    except oauth.OAuthError as e:
        store.mark_source_attempt(SOURCE, e.code)
        raise
    return {'status': 'synced', 'mode': 'full' if full else 'incremental', 'records': counts}
