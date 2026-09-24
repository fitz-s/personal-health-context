"""Model-facing tool handlers. Transport-independent: the MCP server and the eval harness both call `call`.

The backend validates every argument against contracts/tools.json, enforces the tool profile server-side,
and returns bounded error codes. Annotations are hints for the host, not the permission system.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema
from urllib.parse import urlsplit

from . import download, extract
from .store import Store, StoreError, dump

log = logging.getLogger('phctx.tools')
CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'tools.json'
FILE_RETURN_CAP = 4 * 1024 * 1024


def load_contract(path: Path = CONTRACT) -> list[dict]:
    return json.loads(path.read_text())['tools']


@dataclass
class ToolContext:
    store: Store
    profile: str = 'full'
    allowed_download_hosts: list[str] = field(default_factory=list)
    max_file_bytes: int = 25 * 1024 * 1024
    fetch: Callable[..., download.Downloaded] = download.fetch
    fetch_options: dict = field(default_factory=dict)
    run_extraction: bool = True


@dataclass
class Result:
    data: Any
    is_error: bool = False
    image: tuple[bytes, str] | None = None


class Tools:
    def __init__(self, ctx: ToolContext, contract: list[dict] | None = None):
        if ctx.profile not in {'full', 'readonly'}:
            raise ValueError('tool profile must be full or readonly')
        self.ctx = ctx
        self.s = ctx.store
        self.all = {t['name']: t for t in (contract or load_contract())}
        self.validators = {n: jsonschema.Draft202012Validator(t['inputSchema']) for n, t in self.all.items()}

    def listed(self) -> list[dict]:
        """Tools the host may see. The readonly profile (Oura-assisted sessions) omits every writer."""
        return [t for t in self.all.values()
                if self.ctx.profile == 'full' or t['annotations']['readOnlyHint']]

    def call(self, name: str, args: dict | None) -> Result:
        args = args or {}
        tool = self.all.get(name)
        if tool is None:
            return Result({'error': 'unknown_tool', 'message': f'No tool named {name}.'}, True)
        if self.ctx.profile == 'readonly' and not tool['annotations']['readOnlyHint']:
            return Result({'error': 'write_disabled_in_profile',
                           'message': 'This session uses the read-only local tool profile (restricted-vendor data may '
                                      'be present). Nothing was saved. Save from a separate session that has no '
                                      'Oura content.'}, True)
        errors = sorted(self.validators[name].iter_errors(args), key=lambda e: list(e.path))
        if errors:
            e = errors[0]
            return Result({'error': 'invalid_arguments',
                           'message': f'{"/".join(map(str, e.path)) or "arguments"}: {e.message}'[:500]}, True)
        try:
            token = self.s.read_token() if tool['annotations']['readOnlyHint'] else None  # before the read runs
            out = getattr(self, name)(**args)
            if token is not None:
                data = out.data if isinstance(out, Result) else out
                if isinstance(data, dict) and not (isinstance(out, Result) and out.is_error):
                    data['read_token'] = token
            return out if isinstance(out, Result) else Result(out)
        except StoreError as e:
            return Result({'error': e.code, 'message': str(e)}, True)
        except sqlite3.Error:
            log.exception('storage_error tool=%s', name)
            return Result({'error': 'storage_error', 'message': 'Storage failed; nothing from this call was saved. '
                                    'Retry later with the same request_id.'}, True)

    # ---- read tools ----------------------------------------------------------------------
    def context_bootstrap(self) -> dict:
        b = self.s.bootstrap()
        b['tool_profile'] = self.ctx.profile
        if self.ctx.profile == 'readonly':
            b['boundaries'].append('Read-only profile: local writes are disabled in this session.')
        return b

    def context_search(self, query: str = '', kind: str | None = None, start_at: str | None = None,
                       end_at: str | None = None, limit: int = 50, cursor: str | None = None,
                       include_superseded: bool = False) -> dict:
        return self.s.search(query=query, kind=kind, start_at=start_at, end_at=end_at, limit=limit,
                             cursor=cursor, include_superseded=include_superseded)

    def context_read(self, record_ids: list[str], include_history: bool = False) -> dict:
        out = self.s.get_records(record_ids)
        # Echo these as a candidate's evidence_versions; the outbox rejects it if any changed since this read.
        out['versions'] = self.s.read_versions([r['id'] for r in out['records']])
        if include_history:
            out['history'] = {r['id']: [h['id'] for h in self.s.history(r['id'])] for r in out['records']}
        return out

    def context_query(self, sql: str, parameters: list | None = None, limit: int = 500) -> dict:
        return self.s.query_readonly(sql, parameters, limit)

    def context_read_original(self, object_sha256: str, mode: str, start_page: int | None = None,
                              end_page: int | None = None) -> Result | dict:
        info = self.s.object_info(object_sha256)
        if mode == 'info':
            return {**info, 'versions': self.s.read_versions([f'obj:{object_sha256}'])}
        if mode in {'text', 'pages'}:
            out = self.s.read_pages(object_sha256, start_page or 1, end_page)
            return {**out, 'versions': self.s.read_versions(out['evidence_ids'])}
        if mode == 'page_image':
            jpg, meta = extract.render(self.s, object_sha256, start_page or 1)
            return Result({**meta, 'object_sha256': object_sha256, 'derived': 'rendered view; the original is unchanged',
                           'cite_as': f'obj:{object_sha256}', 'versions': self.s.read_versions([f'obj:{object_sha256}'])},
                          image=(jpg, 'image/jpeg'))
        data = self.s.read_object(object_sha256)
        if len(data) > FILE_RETURN_CAP:
            return Result({'error': 'file_too_large_to_return', 'size': len(data),
                           'message': 'Use mode=text/pages, or mode=page_image for a page or image view.'}, True)
        meta = {'object_sha256': object_sha256, 'size': len(data), 'mime': info['mime'],
                'filename': info['filename'], 'verified_sha256': hashlib.sha256(data).hexdigest() == object_sha256,
                'versions': self.s.read_versions([f'obj:{object_sha256}'])}
        if info['mime'].startswith('image/') and info['mime'] != 'image/heic':
            return Result(meta, image=(data, info['mime']))
        if info['mime'] == 'image/heic':
            meta['hint'] = 'The host may not display HEIC; mode=page_image returns a JPEG view.'
        meta['base64'] = base64.b64encode(data).decode()
        return meta

    def context_receipt(self, request_id: str) -> dict:
        r = self.s.receipt(request_id)
        return {'request_id': request_id, 'committed': r is not None, 'receipt': r}

    # ---- write tools ---------------------------------------------------------------------
    def context_capture(self, request_id: str, kind: str, text: str, occurred_at: str,
                        timezone_name: str = 'America/Chicago', payload: dict | None = None,
                        evidence_ids: list[str] | None = None, read_token: int | None = None) -> dict:
        return self.s.put_record(request_id=request_id, kind=kind, text=text, occurred_at=occurred_at,
                                 timezone_name=timezone_name, payload=payload, evidence_ids=evidence_ids,
                                 read_token=read_token)

    def context_revise(self, request_id: str, supersedes: str, kind: str, text: str, occurred_at: str,
                       timezone_name: str = 'America/Chicago', payload: dict | None = None,
                       evidence_ids: list[str] | None = None, read_token: int | None = None) -> dict:
        return self.s.put_record(request_id=request_id, kind=kind, text=text, occurred_at=occurred_at,
                                 timezone_name=timezone_name, payload=payload, evidence_ids=evidence_ids,
                                 supersedes=supersedes, read_token=read_token)

    def context_capture_file(self, request_id: str, file: dict, text: str, occurred_at: str,
                             timezone_name: str = 'America/Chicago', payload: dict | None = None) -> dict:
        # Signed URLs change between retries, so the envelope is the host file id plus the capture fields.
        envelope = hashlib.sha256(dump([file['file_id'], text, occurred_at, timezone_name, payload or {}])
                                  .encode()).hexdigest()
        receipt, failed = self.s.receipt(request_id), None
        if receipt is not None:
            rec = self.s.get_records([receipt['record_id']])['records']
            if not rec or rec[0]['payload'].get('request_envelope_sha256') != envelope:
                raise StoreError('idempotency_conflict', 'request_id was already used for a different file or '
                                                         'capture; use a new request_id.')
        else:
            log.info('file_fetch host=%s', urlsplit(file['download_url']).hostname or '?')
            got = self.ctx.fetch(file['download_url'], allowlist=self.ctx.allowed_download_hosts,
                                 max_bytes=self.ctx.max_file_bytes, **self.ctx.fetch_options)
            name = Path(file.get('file_name') or '').name.strip() or 'attachment'
            name = ''.join(ch for ch in name if ord(ch) >= 32 and ch not in '\\/')[:200] or 'attachment'
            declared = (file.get('mime_type') or got.content_type or 'application/octet-stream')[:100]
            meta = {**(payload or {}), 'host_file_id_sha256': hashlib.sha256(file['file_id'].encode()).hexdigest(),
                    'download_host': got.host, 'request_envelope_sha256': envelope}
            receipt = self.s.put_attachment_bytes(request_id=request_id, data=got.data, filename=name, mime=declared,
                                                  text=text, occurred_at=occurred_at, timezone_name=timezone_name,
                                                  payload=meta, max_bytes=self.ctx.max_file_bytes)
            log.info('file_saved request=%s sha=%s size=%d host=%s', request_id, receipt['object_sha256'][:12],
                     receipt['size'], got.host)
            # The original is committed: from here on a failure is an extraction state, never an error result.
            if self.ctx.run_extraction:
                try:
                    extract.extract(self.s, receipt['object_sha256'])
                except (StoreError, sqlite3.Error, OSError, ValueError, KeyError) as e:
                    failed = getattr(e, 'code', type(e).__name__)
                    log.info('extraction_failed sha=%s code=%s', receipt['object_sha256'][:12], failed)
        # Same answer for the first call and any replay: the committed receipt plus current extraction state.
        try:
            ex = self.s.object_info(receipt['object_sha256'])['extraction'] or {}
        except StoreError as e:
            if e.code in {'object_missing', 'object_corrupt', 'not_found'}:
                raise  # the receipt exists but its bytes do not verify: never report the original as saved
            failed, ex = failed or e.code, {}
        except sqlite3.Error:
            failed, ex = failed or 'storage_error', {}
        if failed and ex.get('status') in {None, 'pending'}:
            ex = {'status': 'failed', 'error_code': failed}
        return {**receipt, 'extraction_status': ex.get('status', 'pending'), 'pages': ex.get('page_count') or 0,
                'extraction_error': ex.get('error_code')}

    def context_set_preference(self, request_id: str, key: str, value: str) -> dict:
        return self.s.set_preference(request_id=request_id, key=key, value=value)

    def context_ack_insight(self, request_id: str, insight_id: str, disposition: str = 'delivered') -> dict:
        return self.s.ack_insight(request_id=request_id, insight_id=insight_id, disposition=disposition)


def as_text(data: Any) -> str:
    return dump(data)
