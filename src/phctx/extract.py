"""Derived page text from a saved original, parsed in a resource-limited child process with a minimal environment.

The original is already committed before this runs; failure here only changes extraction status.
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
from pathlib import Path

from .store import Store, StoreError

TIMEOUT_S = 30
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_PAGES = 500
SRC = str(Path(__file__).resolve().parents[1])


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S, TIMEOUT_S))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    except (ValueError, OSError):
        pass  # macOS may refuse RLIMIT_AS; the wall-clock timeout still bounds the child.


def _child(path: str) -> None:
    """Entry point inside the child: set limits first, then print {"pages": [...]} for a PDF.

    Limits are applied here, after exec, not via preexec_fn (unsafe to fork-then-run Python in a threaded parent).
    """
    _limits()
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        if i >= MAX_PAGES:
            break
        pages.append(page.extract_text() or '')
    print(json.dumps({'pages': pages, 'total_pages': len(reader.pages)}))


RENDER_PX = 1600  # longest side of a rendered page/image returned to the model


def _page_child(src: str, page: int, dst: str) -> None:
    """Inside the limited child: write page `page` (1-based) of a PDF as a one-page PDF at dst."""
    _limits()
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(src)
    if not 1 <= page <= len(reader.pages):
        print(json.dumps({'error': 'page_out_of_range', 'total_pages': len(reader.pages)}))
        return
    w = PdfWriter()
    w.add_page(reader.pages[page - 1])
    with open(dst, 'wb') as f:
        w.write(f)
    print(json.dumps({'total_pages': len(reader.pages)}))


def render(store: Store, sha: str, page: int = 1) -> tuple[bytes, dict]:
    """A bounded JPEG of one PDF page, or of an image the model cannot take as is (HEIC, oversized): the visual path
    for scanned documents. The original bytes are unchanged; this is a derived view, never stored."""
    import tempfile
    mime = store.object_info(sha)['mime']
    if mime != 'application/pdf' and not mime.startswith('image/'):
        raise StoreError('not_renderable', f'Originals of type {mime} have no visual rendering; use mode=text/file.')
    env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': SRC, 'HOME': os.environ.get('HOME', '/')}
    with tempfile.TemporaryDirectory(prefix='phctx-render-') as d:
        src, total = str(store.blobs / sha), None
        if mime == 'application/pdf':
            one = os.path.join(d, 'page.pdf')
            try:
                proc = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[4]); '
                                       'from phctx.extract import _page_child; _page_child(sys.argv[1], int(sys.argv[2]), '
                                       'sys.argv[3])', src, str(page), one, SRC],
                                      capture_output=True, text=True, timeout=TIMEOUT_S + 5, env=env)
                out = json.loads(proc.stdout or '{}')
            except (subprocess.TimeoutExpired, ValueError) as e:
                raise StoreError('render_failed', 'The page could not be split out of the PDF.') from e
            if out.get('error'):
                raise StoreError(out['error'], f'The PDF has {out["total_pages"]} pages.')
            if proc.returncode != 0 or not os.path.exists(one):
                raise StoreError('render_failed', 'The page could not be split out of the PDF.')
            src, total = one, out['total_pages']
        elif page != 1:
            raise StoreError('page_out_of_range', 'An image has one page.')
        jpg = os.path.join(d, 'page.jpg')
        try:
            proc = subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', '-Z', str(RENDER_PX), src, '--out', jpg],
                                  capture_output=True, timeout=TIMEOUT_S, env=env)
        except subprocess.TimeoutExpired as e:
            raise StoreError('render_failed', 'Rendering timed out.') from e
        if proc.returncode != 0 or not os.path.exists(jpg):
            raise StoreError('render_failed', 'The original could not be rendered.')
        return Path(jpg).read_bytes(), {'page': page, 'total_pages': total, 'max_side_px': RENDER_PX}


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
        env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': SRC, 'HOME': os.environ.get('HOME', '/')}
        proc = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[2]); '
                               'from phctx.extract import _child; _child(sys.argv[1])', str(store.blobs / sha), SRC],
                              capture_output=True, text=True, timeout=TIMEOUT_S + 5, env=env)
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
