"""Apple Health export.zip → observations (backfill only; never claims continuous sync).

ZIP preflight rejects traversal, symlinks and compression bombs. XML is parsed as a stream; any ENTITY
declaration or external reference aborts. Mapping follows contracts/normalization.md (source identity `native_id` vs equivalence key `origin_key`).
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import stat
import uuid
import zipfile
import pyexpat
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterator

from .store import Store, StoreError, check_tz, dump, instant, utcnow

MAX_UNCOMPRESSED = 20 * 1024 ** 3
MAX_RATIO = 200
MAX_ATTACHMENT = 64 * 1024 * 1024
PAGE = 5000
SOURCE = 'apple_health_export'
# Bump when the mapping changes: page request keys include it, and a full import under a new version retires the
# same export's rows written by older versions (rows without metadata.import count as older).
PARSER_VERSION = 4
WORKOUT = 'HKWorkoutTypeIdentifier'
CATEGORY = 'HKCategoryTypeIdentifier'
UNITS = {'Cal': 'kcal'}
SECONDS = {'s': 1, 'min': 60, 'hr': 3600}
# Children mapped into sample metadata; any other child is kept raw in other_children and counted unsupported.
KNOWN = {'Record': {'MetadataEntry', 'HeartRateVariabilityMetadataList'},
         'Workout': {'MetadataEntry', 'WorkoutEvent', 'WorkoutStatistics', 'WorkoutRoute', 'FileReference'}}
MAPPED_ATTRS = {'type', 'unit', 'value', 'sourceName', 'sourceVersion', 'device', 'creationDate', 'startDate',
                'endDate', 'workoutActivityType'}
CDA_TYPES = {'HKQuantityTypeIdentifierHeartRate', 'HKQuantityTypeIdentifierRespiratoryRate',
             'HKQuantityTypeIdentifierOxygenSaturation'}
CDA_SAMPLE = 2000


def _apple_time(s: str) -> str:
    """'2026-09-01 07:12:03 -0500' → ISO 8601 with offset."""
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S %z').isoformat()


def origin_key(metric: str, start: str, end: str, value_num: float | None, value_text: str | None,
               unit: str | None, source_name: str | None) -> str:
    """Equivalence key shared by XML backfill and live HealthKit rows for the same sample (normalization v1)."""
    if metric == WORKOUT:
        value, unit = [value_text, None if value_num is None else round(float(value_num))], 's'
    elif metric.startswith(CATEGORY):
        value, unit = value_text, ''
    else:
        value, unit = None if value_num is None else round(float(value_num), 4), UNITS.get(unit or '', unit or '')
    # export.xml has whole seconds while HealthKit dates carry fractions: key on the whole second.
    return hashlib.sha256(dump([metric, instant(start)[:19], instant(end)[:19], value, unit,
                                (source_name or '').strip()]).encode()).hexdigest()


def origin_key_of_row(metric: str, start: str, end: str, value_num, value_text, unit, raw_json: str) -> str | None:
    """Recompute a stored observation's equivalence key from its row (used when the key formula changes)."""
    import json as _json
    try:
        raw = _json.loads(raw_json or '{}')
    except ValueError:
        raw = {}
    if metric == 'tombstone':
        return None
    if metric == WORKOUT and not value_text:  # live rows written before normalization kept the type in metadata
        value_text = (raw.get('metadata') or {}).get('activity_type')
    return origin_key(metric, start, end, value_num, value_text, unit, raw.get('source_name'))


def preflight(zip_path: Path) -> dict:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise StoreError('invalid_export', 'Not a readable ZIP archive.') from e
    with zf:
        total, members, xml_name, cda = 0, [], None, None
        for info in zf.infolist():
            name = info.filename
            if name.startswith('/') or '..' in Path(name).parts or '\\' in name:
                raise StoreError('invalid_export', 'Archive contains an unsafe path.')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise StoreError('invalid_export', 'Archive contains a symlink.')
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 50_000_000:
                raise StoreError('invalid_export', 'Archive member compression ratio is unsafe.')
            total += info.file_size
            members.append(name)
            base = Path(name).name
            if base in {'export.xml', '导出.xml'} and 'cda' not in name.lower():
                xml_name = name
            elif base.lower().endswith('cda.xml'):
                cda = name
        if total > MAX_UNCOMPRESSED:
            raise StoreError('invalid_export', 'Archive is larger than the import cap.')
        if not xml_name:
            raise StoreError('invalid_export', 'No export.xml found in the archive.')
    return {'members': len(members), 'uncompressed_bytes': total, 'xml': xml_name, 'cda': cda}


def _parser(start, end, chars=None) -> pyexpat.XMLParserType:
    def refuse(*_):
        raise StoreError('invalid_export', 'XML entity declarations/references are refused.')
    p = pyexpat.ParserCreate()
    p.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartElementHandler, p.EndElementHandler = start, end
    if chars:
        p.CharacterDataHandler = chars
    p.EntityDeclHandler = refuse
    p.ExternalEntityRefHandler = refuse
    return p


def _walk(n: dict) -> Iterator[dict]:
    yield n
    for c in n['children']:
        yield from _walk(c)


def _flat(n: dict) -> dict:
    """A child element as its attributes, with any grandchildren kept raw."""
    return {**n['attrs'], **({'children': n['children']} if n['children'] else {})}


def _files(n: dict) -> list[str]:
    return [c['attrs'].get('path', '') for c in _walk(n) if c['tag'] == 'FileReference']


def _sample(item: dict, tz: str, imp: dict) -> dict:
    a, kids = item['attrs'], item['children']
    md: dict = {}
    for c in kids:  # a repeated key keeps every value, in order
        if c['tag'] == 'MetadataEntry':
            k, v = c['attrs'].get('key', ''), c['attrs'].get('value', '')
            md[k] = [*(md[k] if isinstance(md[k], list) else [md[k]]), v] if k in md else v
    if item['tag'] == 'Workout':
        metric, unit, text = WORKOUT, 's', a.get('workoutActivityType')
        dur = a.get('duration')
        value = float(dur) * SECONDS[a.get('durationUnit', 'min')] if dur else None
        totals = {k: {'value': v, 'unit': a[k + 'Unit']} for k, v in a.items() if k + 'Unit' in a}
        extra = {'activity_type': text, 'totals': totals,
                 'statistics': [_flat(c) for c in kids if c['tag'] == 'WorkoutStatistics'],
                 'events': [_flat(c) for c in kids if c['tag'] == 'WorkoutEvent'],
                 'routes': [_flat(c) for c in kids if c['tag'] == 'WorkoutRoute'], 'route_files': _files(item)}
        mapped = MAPPED_ATTRS | set(totals) | {k + 'Unit' for k in totals}
    else:
        metric, unit, raw = a.get('type', 'unknown'), a.get('unit'), a.get('value')
        try:
            value = float(raw) if raw is not None else None
        except ValueError:
            value = None
        text = raw if value is None or metric.startswith(CATEGORY) else None
        hrv = [[_flat(b) for b in c['children']] for c in kids if c['tag'] == 'HeartRateVariabilityMetadataList']
        extra = {'hrv_metadata_lists': hrv} if hrv else {}
        mapped = MAPPED_ATTRS
    other = [c for c in kids if c['tag'] not in KNOWN[item['tag']]]
    if other:
        extra['other_children'] = other
    if rest := {k: v for k, v in a.items() if k not in mapped}:
        extra['other_attributes'] = rest
    zone = md.get('HKTimeZone', tz) if isinstance(md.get('HKTimeZone', tz), str) else tz  # conflicting zones: default
    try:
        ZoneInfo(zone)
    except (KeyError, ValueError):
        zone = tz
    start, end = _apple_time(a['startDate']), _apple_time(a.get('endDate', a['startDate']))
    extra.update(source_version=a.get('sourceVersion'), creation_date=a.get('creationDate'), **{'import': imp})
    if clash := {k: md.pop(k) for k in list(md) if k in extra}:  # a MetadataEntry never loses to a derived field
        extra['colliding_metadata_entries'] = clash
    return {'metric': metric, 'start_at': start, 'end_at': end, 'value_num': value, 'value_text': text, 'unit': unit,
            'timezone': zone, 'source_name': a.get('sourceName', ''),
            'source_bundle_id': a.get('sourceName', ''), 'device': {'raw': a.get('device')} if a.get('device') else {},
            'metadata': {**md, **extra},
            'origin_key': origin_key(metric, start, end, value, text, unit, a.get('sourceName')),
            'native_id': 'x2:' + hashlib.sha256(dump(item).encode()).hexdigest()[:40]}


def iterate(zip_path: Path, xml_name: str, elements: Counter | None = None,
            unsupported: Counter | None = None) -> Iterator[dict]:
    """Stream Record/Workout (with their full child trees), ActivitySummary, Me and ExportDate elements.

    Counts every element by tag, and top-level elements this mapping does not handle. Any ENTITY declaration or
    external reference aborts the import.
    """
    elements = Counter() if elements is None else elements
    unsupported = Counter() if unsupported is None else unsupported
    out: list[dict] = []
    tree: list[dict] = []  # open Record/Workout subtree
    path: list[str] = []

    def start(name, attrs):
        elements[name] += 1
        node = {'tag': name, 'attrs': attrs, 'children': []}
        if tree:
            tree[-1]['children'].append(node)
            tree.append(node)
        elif name in ('Record', 'Workout'):
            tree.append(node)
        elif name in ('ActivitySummary', 'Me', 'ExportDate'):
            out.append(node)
        elif path and path[-1] == 'HealthData':
            unsupported[name] += 1
        path.append(name)

    def end(name):
        path.pop()
        if tree:
            node = tree.pop()
            if not tree:
                out.append(node)

    p = _parser(start, end)
    try:
        with zipfile.ZipFile(zip_path) as zf, zf.open(xml_name) as f:
            while chunk := f.read(1 << 20):
                p.Parse(chunk, False)
                yield from out
                out.clear()
            p.Parse(b'', True)
            yield from out
    except pyexpat.ExpatError as e:
        raise StoreError('invalid_export', 'export.xml is malformed; committed pages remain and re-import is '
                                           'idempotent.') from e


class _Enough(Exception):
    pass


def _cda_sample(zip_path: Path, name: str) -> tuple[list[tuple], int, bool]:
    """Up to CDA_SAMPLE heart-rate/respiratory-rate/SpO2 observations of export_cda.xml as (type, instant, value),
    the unparseable count, and whether the file stopped parsing. Apple writes entries after </ClinicalDocument>, so
    a malformed CDA is normal: it is a cross-check only and never blocks the import."""
    keys: list[tuple] = []
    bad = 0
    obs: dict | None = None
    path: list[str] = []
    buf: list[str] = []

    def start(tag, attrs):
        nonlocal obs
        parent = path[-1] if path else None
        path.append(tag)
        buf.clear()
        if tag == 'observation':
            obs = {}
        elif obs is not None and tag == 'low' and parent == 'effectiveTime':
            obs['low'] = attrs.get('value')
        elif obs is not None and tag == 'value' and parent == 'observation':
            obs['pq'] = attrs.get('value')

    def chars(d):
        if obs is not None:
            buf.append(d)

    def end(tag):
        nonlocal obs, bad
        path.pop()
        if obs is None:
            return
        if tag != 'observation':
            if path and path[-1] == 'text' and tag in ('type', 'value'):
                obs[tag] = ''.join(buf).strip()
            return
        o, obs = obs, None
        if o.get('type') not in CDA_TYPES:
            return
        try:
            at = instant(datetime.strptime(o['low'], '%Y%m%d%H%M%S%z').isoformat())
            keys.append((o['type'], at, round(float(o.get('value') or o['pq']), 4)))
        except (KeyError, TypeError, ValueError, StoreError):
            bad += 1
        if len(keys) + bad >= CDA_SAMPLE:
            raise _Enough

    p = _parser(start, end, chars)
    try:
        with zipfile.ZipFile(zip_path) as zf, zf.open(name) as f:
            while chunk := f.read(1 << 20):
                p.Parse(chunk, False)
            p.Parse(b'', True)
    except _Enough:
        pass
    except pyexpat.ExpatError:
        return keys, bad, True
    return keys, bad, False


def _since(since: str, tz: str) -> str:
    """An instant; a date or naive time is read in the user's timezone."""
    x = datetime.fromisoformat(since.replace('Z', '+00:00'))
    return instant((x if x.tzinfo else x.replace(tzinfo=ZoneInfo(tz))).isoformat())


def _summary(item: dict, tz: str, imp: dict) -> list[dict]:
    """ActivitySummary → one observation per ring metric for that local calendar day (goal kept in metadata).

    Apple gives only a date, no zone: identity is the date (native_id, metadata.local_date); the instant window is
    [local midnight, next local midnight) in the user's timezone, so DST days are 23 or 25 hours long.
    """
    a = item['attrs']
    day = a.get('dateComponents')
    if not day:
        return []
    d0 = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=ZoneInfo(tz))
    start, end = d0.isoformat(), (d0 + timedelta(days=1)).isoformat()
    out = []
    for key, unit_key, goal_key in [('activeEnergyBurned', 'activeEnergyBurnedUnit', 'activeEnergyBurnedGoal'),
                                    ('appleMoveTime', None, 'appleMoveTimeGoal'),
                                    ('appleExerciseTime', None, 'appleExerciseTimeGoal'),
                                    ('appleStandHours', None, 'appleStandHoursGoal')]:
        if a.get(key) is None:
            continue
        unit = a.get(unit_key) if unit_key else ('hr' if key == 'appleStandHours' else 'min')
        metric = f'ActivitySummary.{key}'
        v = float(a[key])
        out.append({'metric': metric, 'start_at': start, 'end_at': end, 'value_num': v, 'value_text': None,
                    'unit': unit, 'timezone': tz, 'source_name': 'Apple Health activity summary',
                    'source_bundle_id': 'com.apple.health', 'device': {},
                    'metadata': {'local_date': day, 'goal': a.get(goal_key), 'date_semantics': 'local calendar day',
                                 'import': imp},
                    'origin_key': origin_key(metric, start, end, v, None, unit, 'ActivitySummary'),
                    'native_id': 'as:' + day + ':' + key})
    return out


PROFILE_KEY, OBJECT_KEY = 'apple-export:profile:', 'apple-export:obj:'  # records.source_key identities


def _recorded(store: Store, key: str) -> bool:
    with store.connect() as c:
        return c.execute("SELECT 1 FROM records WHERE source_id='user' AND source_key=?", (key,)).fetchone() is not None


def _active_profile(store: Store) -> tuple[str, dict] | None:
    """(record id, characteristics) of the current profile note, or None."""
    with store.connect() as c:
        row = c.execute("SELECT id, payload_json FROM active_records WHERE source_id='user' AND source_key LIKE ?",
                        (PROFILE_KEY + '%',)).fetchone()
    return (row[0], json.loads(row[1]).get('apple_health_characteristics')) if row else None


def _retire(store: Store, sha16: str, rid: str, cursor: str, since_at: str | None = None) -> tuple[int, int]:
    """Tombstone rows of THIS export written by an older parser, but only those this run replaced.

    Candidates: tagged rows (metadata.import.export == sha16, parser < current) and untagged pre-v4 rows when migration
    v6 attributed them to this export (meta export_legacy_owner, set only if the DB ever imported exactly one export).
    With `since`, only rows at/after it. A candidate is retired only if a current-parser row of this export has its
    equivalence key; one the new parser did not reproduce (unparseable, skipped, a changed mapping) stays and is
    counted as unreplaced. Imports are serialized (import_in_progress), so nothing changes between select and delete.
    Returns (retired, unreplaced).
    """
    with store.connect() as c:
        owner = c.execute("SELECT value FROM meta WHERE key='export_legacy_owner'").fetchone()
        rows = c.execute(
            "SELECT o.native_id, EXISTS(SELECT 1 FROM observations n INDEXED BY obs_origin WHERE n.origin_key=o.origin_key "
            "AND n.source_id=o.source_id AND n.deleted=0 AND json_extract(n.raw_json, '$.metadata.import.export')=? "
            "AND json_extract(n.raw_json, '$.metadata.import.parser')=?) FROM observations o "
            "WHERE o.source_id=? AND o.deleted=0 AND o.start_at>=? AND "
            "(json_extract(o.raw_json, '$.metadata.import') IS NULL AND ? OR "
            "json_extract(o.raw_json, '$.metadata.import.export')=? AND json_extract(o.raw_json, '$.metadata.import.parser')<?)",
            (sha16, PARSER_VERSION, SOURCE, since_at or '', bool(owner and owner[0] == sha16), sha16,
             PARSER_VERSION)).fetchall()
    ids = [nid for nid, replaced in rows if replaced]
    for i in range(0, len(ids), PAGE):
        chunk = ids[i:i + PAGE]
        store.ingest_batch(request_id=f'{rid}:retire:' + hashlib.sha256(dump(chunk).encode()).hexdigest()[:16],
                           source_id=SOURCE, samples=[], deleted_ids=chunk, cursor=cursor,
                           coverage={'kind': 'backfill_retire_older_parser'})
    return len(ids), len(rows) - len(ids)


def _ecg_recorded_at(data: bytes) -> str | None:
    """The 'Recorded Date' instant of the header (lines up to the first blank line), if present and parseable."""
    lines = []
    for ln in data[:65536].decode('utf-8', 'replace').splitlines():
        if not ln.strip():
            break
        lines.append(ln)
    at = None
    for ln in lines:
        k, _, v = ln.partition(',')
        if k.strip().strip('"') == 'Recorded Date':
            try:
                at = _apple_time(v.strip().strip('"'))
            except ValueError:
                pass
    return at


def import_export(store: Store, zip_path: Path, dry_run: bool = False, since: str | None = None,
                  tz: str | None = None) -> dict:
    """Exclusive execution: an OS lock held for the whole run (the kernel releases it if the process dies), so no two
    imports ever run at once, whatever they import. A dry run writes nothing and takes no lock."""
    if dry_run:
        return _import(store, zip_path, True, since, tz)
    with open(store.root / 'import.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise StoreError('import_in_progress', 'Another Apple import is running now; wait for it.') from e
        return _import(store, zip_path, False, since, tz)


def _import(store: Store, zip_path: Path, dry_run: bool, since: str | None, tz: str | None) -> dict:
    tz = tz or store.preferences().get('timezone', 'America/Chicago')
    check_tz(tz)
    try:
        since_at = _since(since, tz) if since else None
    except ValueError as e:
        raise StoreError('invalid_time', 'since must be an ISO 8601 date or time.') from e
    zip_path = Path(zip_path).expanduser().resolve()
    info = preflight(zip_path)
    digest = hashlib.sha256()
    with open(zip_path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    sha = digest.hexdigest()
    tag = f'v{PARSER_VERSION}:{sha[:16]}'
    rid = f'apple-export:{tag}:' + hashlib.sha256(dump({'since': since_at, 'tz': tz}).encode()).hexdigest()[:12]
    imp = {'parser': PARSER_VERSION, 'export': sha[:16]}
    with zipfile.ZipFile(zip_path) as zf:
        zi = zf.getinfo(info['xml'])
    export_at = datetime(*zi.date_time).astimezone().isoformat()
    elements, unsupported = Counter(), Counter()
    counts = {'records_seen': 0, 'skipped_before_since': 0, 'unparseable': 0,
              'imported': 0, 'retired': 0, 'by_source': {}}
    run_id = 'imp_' + uuid.uuid4().hex
    if not dry_run:
        # Recovery: while a run is unfinished old- and new-parser rows overlap, so the durable marker makes observation
        # reads refuse until retirement finishes. A crashed run leaves it set on purpose; only a run with the exact same
        # signature (archive, parser, since, timezone) may take it over and reconcile.
        signature = {'export': sha, 'parser': PARSER_VERSION, 'since': since_at, 'tz': tz}
        with store.transaction() as c:
            held = c.execute("SELECT value FROM meta WHERE key='import_in_progress'").fetchone()
            if held and json.loads(held[0]).get('signature') != signature:
                raise StoreError('import_in_progress', 'An earlier Apple import did not finish; re-run it with the same '
                                                       f'archive and options to reconcile: {json.loads(held[0]).get("signature")}')
            c.execute("INSERT OR REPLACE INTO meta VALUES('import_in_progress', ?)",
                      (dump({'run': run_id, 'signature': signature}),))
            c.execute('INSERT INTO import_runs VALUES(?,?,?,?,?,?,?)',
                      (run_id, 'apple_export', sha, utcnow(), None, 'running', '{}'))
    page: list[dict] = []
    seen: set[str] = set()
    page_no = 0
    routes: dict[str, str] = {}  # workout FileReference → workout start
    cda, cda_bad, cda_malformed = [], 0, False
    found: set[tuple] = set()

    def flush() -> None:
        nonlocal page_no
        if page and not dry_run:
            store.ingest_batch(request_id=f'{rid}:{page_no}', source_id=SOURCE, samples=page, deleted_ids=[],
                               cursor=f'export:{tag}:{page_no}',
                               coverage={'kind': 'backfill', 'export_sha256': sha, 'since': since_at})
        page_no += 1
        page.clear()
        seen.clear()

    def add(s: dict) -> bool:
        if since_at and instant(s['start_at']) < since_at:
            counts['skipped_before_since'] += 1
            return False
        counts['imported'] += 1
        if s['native_id'] not in seen:  # truly identical elements cannot be told apart: kept once
            seen.add(s['native_id'])
            page.append(s)
            if len(page) >= PAGE:
                flush()
        return True

    try:
        if info['cda']:
            cda, cda_bad, cda_malformed = _cda_sample(zip_path, info['cda'])
        want = set(cda)
        for item in iterate(zip_path, info['xml'], elements, unsupported):
            a = item['attrs']
            if item['tag'] == 'ExportDate':
                try:
                    export_at = _apple_time(a.get('value', ''))
                except ValueError:
                    pass
                continue
            if item['tag'] == 'Me':
                profile = {k.replace('HKCharacteristicTypeIdentifier', ''): v for k, v in a.items()}
                counts['profile_fields'] = len(profile)
                active = None if dry_run else _active_profile(store)
                if not dry_run and (active is None or active[1] != profile):  # same as current: nothing to record
                    # A revision of the chain, keyed by what it revises: a return to an earlier profile is a new revision.
                    key = PROFILE_KEY + hashlib.sha256(dump([active and active[0], profile]).encode()).hexdigest()[:12]
                    store.put_record(request_id=f'apple-export:{sha[:16]}:{key}', kind='note',
                                     text='Apple Health profile characteristics (from Health export)',
                                     occurred_at=export_at, source_key=key, supersedes=active and active[0],
                                     payload={'apple_health_characteristics': profile, 'source': 'apple_health_export'})
                continue
            if item['tag'] == 'ActivitySummary':
                counts['activity_summary_days'] = counts.get('activity_summary_days', 0) + 1
                for s in _summary(item, tz, imp):
                    add(s)
                continue
            counts['records_seen'] += 1
            if want and a.get('type') in CDA_TYPES:
                try:
                    k = (a['type'], instant(_apple_time(a['startDate'])), round(float(a['value']), 4))
                    if k in want:
                        found.add(k)
                except (KeyError, ValueError, StoreError):
                    pass
            try:
                s = _sample(item, tz, imp)
            except (KeyError, ValueError):
                counts['unparseable'] += 1
                continue
            unsupported.update(c['tag'] for c in item['children'] if c['tag'] not in KNOWN[item['tag']])
            if add(s):
                counts['by_source'][s['source_name']] = counts['by_source'].get(s['source_name'], 0) + 1
                for p in s['metadata'].get('route_files', []):
                    routes.setdefault(p.lstrip('/'), s['start_at'])
        flush()
        counts['elements'], counts['unsupported_elements'] = dict(elements), dict(unsupported)
        if info['cda']:
            by_type: dict[str, dict] = {}
            for k in cda:
                d = by_type.setdefault(k[0], {'matched': 0, 'unmatched': 0})
                d['matched' if k in found else 'unmatched'] += 1
            matched = sum(d['matched'] for d in by_type.values())
            counts['cda'] = {'present': True, 'sampled': len(cda) + cda_bad, 'matched': matched,
                             'unmatched': len(cda) - matched, 'unparseable': cda_bad, 'by_type': by_type,
                             'malformed': cda_malformed}
        else:
            counts['cda'] = {'present': False}
        if not dry_run:  # also after a partial (since) import: its rows replace older-parser rows of this export
            counts['retired'], counts['unreplaced_older_rows'] = _retire(
                store, sha[:16], rid, f'export:{tag}:{max(page_no - 1, 0)}', since_at)
        _attachments(store, zip_path, counts, routes, export_at, tag, tz, dry_run)
    except BaseException as e:  # any failure (a corrupt member, an interrupt) closes the run: never left 'running'
        if not dry_run:
            with store.transaction() as c:
                c.execute("UPDATE import_runs SET status='failed', finished_at=?, counts_json=? WHERE id=?",
                          (utcnow(), dump({**counts, 'error': type(e).__name__}), run_id))
        if isinstance(e, zipfile.BadZipFile):
            raise StoreError('export_corrupt', 'An archive member is corrupt; the import stopped.') from e
        raise
    if not dry_run:
        with store.transaction() as c:
            c.execute("UPDATE import_runs SET status='done', finished_at=?, counts_json=? WHERE id=?",
                      (utcnow(), dump(counts), run_id))
            c.execute("DELETE FROM meta WHERE key='import_in_progress' AND json_extract(value, '$.run')=?", (run_id,))
    return {'dry_run': dry_run, 'export_sha256': sha, 'preflight': info, 'counts': counts,
            'note': 'Backfill only. Continuous sync requires the iPhone helper.'}


def _attachments(store: Store, zip_path: Path, counts: dict, routes: dict, export_at: str, tag: str,
                 tz: str, dry_run: bool) -> None:
    """GPX routes only when a workout references them (occurred_at = workout start); ECG CSVs (occurred_at = Recorded
    Date, else export date + event_time_unknown)."""
    n = Counter(attachments=0, attachments_unreferenced=0, attachments_refused_oversize=0)
    with zipfile.ZipFile(zip_path) as zf:
        for zinfo in zf.infolist():
            name, parts = zinfo.filename, Path(zinfo.filename).parts
            folder = parts[-2] if len(parts) > 1 else ''
            gpx = folder == 'workout-routes' and name.endswith('.gpx')
            if not (gpx or folder == 'electrocardiograms' and name.endswith('.csv')):
                continue
            key = 'workout-routes/' + parts[-1]
            if gpx and key not in routes:
                n['attachments_unreferenced'] += 1
                continue
            if zinfo.file_size > MAX_ATTACHMENT:
                n['attachments_refused_oversize'] += 1
                continue
            with zf.open(zinfo) as f:
                data = f.read(MAX_ATTACHMENT + 1)
            if len(data) > MAX_ATTACHMENT:
                n['attachments_refused_oversize'] += 1
                continue
            payload = {'source': 'apple_health_export', 'export_path': name}
            if gpx:
                at = routes[key]
            else:
                at = _ecg_recorded_at(data)
                if at is None:
                    at, payload['event_time_unknown'] = export_at, True
            n['attachments'] += 1
            key = OBJECT_KEY + hashlib.sha256(data).hexdigest()
            if dry_run or _recorded(store, key):  # the same original is recorded once, whatever export carried it
                continue
            kind = 'Workout route (GPX)' if gpx else 'Electrocardiogram (CSV)'
            store.put_attachment_bytes(
                request_id=f'apple-export:{tag.split(":")[1]}:{key}', data=data,
                filename=parts[-1], mime='application/gpx+xml' if gpx else 'text/csv',
                text=f'{kind} from Apple Health export: {parts[-1]}', occurred_at=at, timezone_name='UTC',
                payload=payload, max_bytes=MAX_ATTACHMENT, source_key=key)
    counts.update(n)
