"""Apple Health export.zip → observations (backfill only; never claims continuous sync).

ZIP preflight rejects traversal, symlinks and compression bombs. XML is parsed as a stream; any ENTITY
declaration or external reference aborts. Restricted-vendor samples (Oura) are dropped before persistence and only counted.
"""
from __future__ import annotations

import hashlib
import stat
import uuid
import zipfile
import pyexpat
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .store import Store, StoreError, dump, instant, restricted_origin, utcnow

MAX_UNCOMPRESSED = 20 * 1024 ** 3
MAX_RATIO = 200
PAGE = 5000
SOURCE = 'apple_health_export'
# Bump when the mapping changes: page request keys include it, so a re-import with a new mapping writes new
# receipts (rows upsert by native id) instead of colliding with receipts from the old mapping.
PARSER_VERSION = 2


def _apple_time(s: str) -> str:
    """'2026-09-01 07:12:03 -0500' → ISO 8601 with offset."""
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S %z').isoformat()


def origin_key(metric: str, start: str, end: str, value: float | None, text: str | None, source_name: str | None) -> str:
    """Content key shared by XML backfill and live HealthKit rows for the same underlying sample.

    Normalized (UTC instants, rounded value, source name) so both paths compute the same key.
    """
    v = None if value is None else round(float(value), 4)
    return hashlib.sha256(dump([metric, instant(start), instant(end), v, text, (source_name or '').strip()])
                          .encode()).hexdigest()


def preflight(zip_path: Path) -> dict:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise StoreError('invalid_export', 'Not a readable ZIP archive.') from e
    with zf:
        total, members, xml_name = 0, [], None
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
            if Path(name).name in {'export.xml', '导出.xml'} and 'cda' not in name.lower():
                xml_name = name
        if total > MAX_UNCOMPRESSED:
            raise StoreError('invalid_export', 'Archive is larger than the import cap.')
        if not xml_name:
            raise StoreError('invalid_export', 'No export.xml found in the archive.')
    return {'members': len(members), 'uncompressed_bytes': total, 'xml': xml_name}


def _sample(item: dict) -> dict:
    a, md = item['attrs'], item['metadata']
    if item['tag'] == 'Workout':
        metric, unit = 'HKWorkoutTypeIdentifier', 's'
        dur = a.get('duration')
        value = float(dur) * (60 if a.get('durationUnit', 'min') == 'min' else 1) if dur else None
        text = a.get('workoutActivityType')
        md = {**md, 'activity_type': a.get('workoutActivityType'), 'total_energy': a.get('totalEnergyBurned'),
              'total_distance': a.get('totalDistance'),
              'statistics': [{k: st.get(k) for k in ('type', 'sum', 'average', 'minimum', 'maximum', 'unit')}
                             for st in item.get('stats', [])],
              'events': item.get('events', [])[:200], 'route_files': item.get('routes', [])}
    else:
        metric, unit, text = a.get('type', 'unknown'), a.get('unit'), None
        raw = a.get('value')
        try:
            value = float(raw) if raw is not None else None
        except ValueError:
            value, text = None, raw
    start, end = _apple_time(a['startDate']), _apple_time(a.get('endDate', a['startDate']))
    return {'metric': metric, 'start_at': start, 'end_at': end, 'value_num': value, 'value_text': text, 'unit': unit,
            'timezone': md.get('HKTimeZone', 'America/Chicago'), 'source_name': a.get('sourceName', ''),
            'source_bundle_id': a.get('sourceName', ''), 'device': {'raw': a.get('device')} if a.get('device') else {},
            'metadata': {**md, 'source_version': a.get('sourceVersion'), 'creation_date': a.get('creationDate'),
                         **({'instantaneous_bpm_points': item['beats']} if item.get('beats') else {})},
            'origin_key': origin_key(metric, start, end, value, text, a.get('sourceName'))}


def iterate(zip_path: Path, xml_name: str) -> Iterator[dict]:
    """Stream Record/Workout/ActivitySummary/Me elements with their children.

    Any ENTITY declaration or external reference aborts the import.
    """
    out: list[dict] = []
    current: dict | None = None

    def start(name, attrs):
        nonlocal current
        if name in ('Record', 'Workout'):
            current = {'tag': name, 'attrs': attrs, 'metadata': {}, 'stats': [], 'events': [], 'routes': [],
                       'beats': 0}
        elif name in ('ActivitySummary', 'Me'):
            out.append({'tag': name, 'attrs': attrs, 'metadata': {}})
        elif current is None:
            return
        elif name == 'MetadataEntry':
            current['metadata'][attrs.get('key', '')] = attrs.get('value', '')
        elif name == 'WorkoutStatistics':
            current['stats'].append(attrs)
        elif name == 'WorkoutEvent':
            current['events'].append(attrs)
        elif name == 'FileReference':
            current['routes'].append(attrs.get('path', ''))
        elif name == 'InstantaneousBeatsPerMinute':
            current['beats'] += 1

    def end(name):
        nonlocal current
        if name in ('Record', 'Workout') and current is not None:
            out.append(current)
            current = None

    def refuse(*_):
        raise StoreError('invalid_export', 'XML entity declarations/references are refused.')

    p = pyexpat.ParserCreate()
    p.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartElementHandler, p.EndElementHandler = start, end
    p.EntityDeclHandler = refuse
    p.ExternalEntityRefHandler = refuse
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


def _summary(item: dict) -> list[dict]:
    """ActivitySummary → one observation per ring metric for that local day (goal kept in metadata)."""
    a = item['attrs']
    day = a.get('dateComponents')
    if not day:
        return []
    start, end = f'{day}T00:00:00-00:00', f'{day}T23:59:59-00:00'
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
                    'unit': unit, 'timezone': 'UTC', 'source_name': 'Apple Health activity summary',
                    'source_bundle_id': 'com.apple.health', 'device': {},
                    'metadata': {'local_date': day, 'goal': a.get(goal_key), 'date_semantics': 'local calendar day'},
                    'origin_key': origin_key(metric, start, end, v, None, 'ActivitySummary'),
                    'native_id': 'as:' + day + ':' + key})
    return out


def import_export(store: Store, zip_path: Path, dry_run: bool = False, since: str | None = None) -> dict:
    zip_path = Path(zip_path).expanduser().resolve()
    info = preflight(zip_path)
    digest = hashlib.sha256()
    with open(zip_path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    with zipfile.ZipFile(zip_path) as zf:
        zi = zf.getinfo(info['xml'])
    export_at = datetime(*zi.date_time).astimezone().isoformat()
    counts = {'records_seen': 0, 'filtered_restricted': 0, 'skipped_before_since': 0, 'unparseable': 0,
              'imported': 0, 'by_source': {}}
    run_id = 'imp_' + uuid.uuid4().hex
    if not dry_run:
        with store.transaction() as c:
            c.execute('INSERT INTO import_runs VALUES(?,?,?,?,?,?,?)',
                      (run_id, 'apple_export', digest.hexdigest(), utcnow(), None, 'running', '{}'))
    page: list[dict] = []
    page_no = 0

    def flush() -> None:
        nonlocal page, page_no
        if page and not dry_run:
            store.ingest_batch(request_id=f'apple-export:v{PARSER_VERSION}:{digest.hexdigest()[:16]}:{page_no}', source_id=SOURCE,
                               samples=page, deleted_ids=[], cursor=f'export:v{PARSER_VERSION}:{digest.hexdigest()[:16]}:{page_no}',
                               coverage={'kind': 'backfill', 'export_sha256': digest.hexdigest()})
        page_no += 1
        page = []
    try:
        for item in iterate(zip_path, info['xml']):
            if item['tag'] == 'Me':
                profile = {k.replace('HKCharacteristicTypeIdentifier', ''): v for k, v in item['attrs'].items()}
                counts['profile_fields'] = len(profile)
                if not dry_run:
                    store.put_record(request_id=f'apple-export:v{PARSER_VERSION}:{digest.hexdigest()[:16]}:profile', kind='note',
                                     text='Apple Health profile characteristics (from Health export)',
                                     occurred_at=export_at, payload={'apple_health_characteristics': profile,
                                                                     'source': 'apple_health_export'})
                continue
            if item['tag'] == 'ActivitySummary':
                rows = _summary(item)
                counts['activity_summary_days'] = counts.get('activity_summary_days', 0) + 1
                counts['imported'] += len(rows)
                page.extend(rows)
                if len(page) >= PAGE:
                    _dedupe(page)
                    flush()
                continue
            counts['records_seen'] += 1
            a = item['attrs']
            if restricted_origin(a.get('sourceName'), a.get('device'), dump(item['metadata'])):
                counts['filtered_restricted'] += 1
                continue
            try:
                s = _sample(item)
            except (KeyError, ValueError):
                counts['unparseable'] += 1
                continue
            if since and s['start_at'] < since:
                counts['skipped_before_since'] += 1
                continue
            s['native_id'] = 'x:' + s['origin_key'][:40]
            counts['by_source'][s['source_name']] = counts['by_source'].get(s['source_name'], 0) + 1
            counts['imported'] += 1
            page.append(s)
            if len(page) >= PAGE:
                _dedupe(page)
                flush()
        _dedupe(page)
        flush()
    except StoreError:
        if not dry_run:
            with store.transaction() as c:
                c.execute("UPDATE import_runs SET status='failed', finished_at=?, counts_json=? WHERE id=?",
                          (utcnow(), dump(counts), run_id))
        raise
    counts['attachments'] = 0
    with zipfile.ZipFile(zip_path) as zf:
        for zinfo in zf.infolist():
            name = zinfo.filename
            if not (name.endswith('.gpx') and '/workout-routes/' in name or
                    name.endswith('.csv') and '/electrocardiograms/' in name):
                continue
            counts['attachments'] += 1
            if dry_run:
                continue
            data = zf.read(zinfo)
            kind = 'Workout route (GPX)' if name.endswith('.gpx') else 'Electrocardiogram (CSV)'
            store.put_attachment_bytes(request_id=f'apple-export:v{PARSER_VERSION}:{digest.hexdigest()[:16]}:{name}', data=data,
                                       filename=Path(name).name, mime='application/gpx+xml' if name.endswith('.gpx')
                                       else 'text/csv', text=f'{kind} from Apple Health export: {Path(name).name}',
                                       occurred_at=datetime(*zinfo.date_time).astimezone().isoformat(),
                                       payload={'source': 'apple_health_export', 'export_path': name},
                                       max_bytes=64 * 1024 * 1024)
    if not dry_run:
        with store.transaction() as c:
            c.execute("UPDATE import_runs SET status='done', finished_at=?, counts_json=? WHERE id=?",
                      (utcnow(), dump(counts), run_id))
    return {'dry_run': dry_run, 'export_sha256': digest.hexdigest(), 'preflight': info, 'counts': counts,
            'note': 'Backfill only. Continuous sync requires the iPhone helper.'}


def _dedupe(page: list[dict]) -> None:
    """Truly identical export rows share a fingerprint; keep one (they cannot be told apart)."""
    seen, keep = set(), []
    for s in page:
        if s['native_id'] not in seen:
            seen.add(s['native_id'])
            keep.append(s)
    page[:] = keep
