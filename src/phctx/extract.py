"""Derived page text from a saved original. Runs in a resource-limited child process with no network use.

The original is already committed before this runs; failure here only changes extraction status.
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys

from .store import Store, StoreError

TIMEOUT_S = 30
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_PAGES = 500


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S, TIMEOUT_S))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    except (ValueError, OSError):
        pass  # macOS may refuse RLIMIT_AS; the wall-clock timeout still bounds the child.


def _child(path: str) -> None:
    """Entry point inside the child: print {"pages": [...]} for a PDF."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        if i >= MAX_PAGES:
            break
        pages.append(page.extract_text() or '')
    print(json.dumps({'pages': pages, 'total_pages': len(reader.pages)}))


def extract(store: Store, sha: str) -> dict:
    info = store.object_info(sha)
    mime = info['mime']
    if mime == 'text/plain':
        text = store.read_object(sha).decode('utf-8', errors='replace')
        return store.set_extraction(sha, status='done', method='utf8', pages=[text])
    if mime != 'application/pdf':
        # Images are read by the conversational model directly; facts it extracts are saved as records
        # citing obj:<sha>. No lossy local OCR substitute is stored.
        return store.set_extraction(sha, status='not_applicable', method=None, pages=[],
                                    error_code='visual_original_model_reads_directly')
    try:
        proc = subprocess.run([sys.executable, '-c', 'import sys; from phctx.extract import _child; _child(sys.argv[1])',
                               str(store.blobs / sha)], capture_output=True, text=True, timeout=TIMEOUT_S + 5,
                              preexec_fn=_limits, env={**os.environ, 'no_proxy': '*', 'NO_PROXY': '*'})
    except subprocess.TimeoutExpired:
        return store.set_extraction(sha, status='failed', method='pdf_text', error_code='timeout')
    if proc.returncode != 0:
        return store.set_extraction(sha, status='failed', method='pdf_text', error_code='parser_error')
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return store.set_extraction(sha, status='failed', method='pdf_text', error_code='parser_output')
    pages = out['pages']
    empty = sum(1 for p in pages if not p.strip())
    status = 'done' if pages and not empty and out['total_pages'] <= MAX_PAGES else 'partial'
    if pages and empty == len(pages):
        status = 'partial'  # scanned PDF: no native text layer; the model must read page images
    return store.set_extraction(sha, status=status, method='pdf_text', pages=pages,
                                error_code=None if status == 'done' else 'no_text_layer_or_truncated')


def extract_pending(store: Store, limit: int = 20) -> list[dict]:
    with store.connect() as c:
        shas = [r[0] for r in c.execute("SELECT object_sha FROM extractions WHERE status='pending' LIMIT ?", (limit,))]
    out = []
    for sha in shas:
        try:
            out.append(extract(store, sha))
        except StoreError as e:
            out.append({'object_sha256': sha, 'error': e.code})
    return out
