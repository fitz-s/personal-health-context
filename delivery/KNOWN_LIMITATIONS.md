# Known limitations (linked to the original A–L acceptance criteria)

Status words: **met** (verified here), **partial**, **gap** (not met), with the reason and what closes it.

| # | Criterion | Status | Detail |
|---|---|---|---|
| A | Capture: text, voice transcript, photo, document | **met (web)** | Observed live in ChatGPT web (model "6 Pro") over the Secure MCP Tunnel on 2026-09-24 (live-evidence/chatgpt_live_probe_2026-09-24.md): text capture with committed receipt; a photo and a 2-page PDF saved with local SHA-256 == stored SHA-256, per-page text. Download allowlist is the anchored pattern for ChatGPT's observed regional file hosts. Voice: text path only (dictation produces text). ChatGPT mobile app not exercised. |
| B | Memory across conversations | **met (web)**, eval residual | A fresh ChatGPT chat found the probe note by content (live evidence). Eval memory: dev 6/6, holdout 1/2 — E016 did not page through an incomplete index before concluding. |
| C | Cross-source investigation (Apple + Oura + user + files) | **gap** (Oura, live Apple), met (Apple history) | Apple history: full export imported (1.87 M observations) and investigated live in ChatGPT (multi-year resting-HR question answered with coverage and provenance caveats). Live iPhone sync: code + Swift core tests exist; the app is not built (needs Xcode licence/Apple ID/device — deferred by you). Oura: no official route; never persisted by design. |
| D | Broad model discretion | **met (model eval)** | Bootstrap is an index; search/read/bounded SQL/original pages are available; eval E027 ("only the summary?") initially failed and was fixed; see eval-report. |
| E | Sparse proactivity / valid silence | **met (model eval + unit)**, shadow **pending** | Worker installed under launchd, runs every 15 min, no model calls without relevant change. Background model is **disabled** by default (privacy/quota decision, USER_ACTIONS §4). Real multi-day shadow observation has **not** happened (started 2026-09-23). |
| F | Revisitability | **met (model eval)** | Revisit cases pass with the real worker + strong model on synthetic data; not yet observed on real data over time. |
| G | Measurement-gap awareness | **met (model eval)** | Measurement category passes (posture, hypertrophy, unknown-unknown with decision value). |
| H | No fake certainty | **partial (residual risk)** | Dev 42/42 after fixes. **Blind holdout 12/14**: E040 hard failure (background surfaced an irrelevant measurement-gap alert — fixed, non-blind rerun passes), E016 did not page through notes before claiming nothing else exists. Model behavior is probabilistic. |
| I | One interface | **met (web)** | ChatGPT is the interface: write, read, files and fresh-chat recovery all observed live. The iPhone helper is only a one-time setup screen (not yet built). |
| J | Local-first persistence + backup | **met** | Live DB in `~/Library/Application Support` (FileVault on); consistent snapshots; restore into a new root verified; encrypted archive → iCloud Drive → read back → decrypt → restore verified on synthetic data. Encrypted cloud backup for production is off until you opt in (USER_ACTIONS §5). |
| K | Extensibility without sprawl | **met** | Sources are rows with policy; importer/ingest share one observation model; no extra vendors implemented. |
| L | User trust | **not assessed** | Needs a representative session with you in ChatGPT. |

## Other limitations
- **iOS helper**: Swift core (outbox, sync engine, uploader with certificate pinning) builds and passes 10 tests, and the real Swift uploader interoperates with the Mac ingest server over TLS (synthetic data). The iOS app target itself is not compiled (no iOS SDK) and has never run on a device; HealthKit authorization/background delivery are unverified. Background delivery timing is controlled by iOS (best effort, not real-time). LAN-only sync by default: data syncs when the phone is on the same network as the Mac; the phone keeps an outbox otherwise.
- **Oura cross-app copy**: the server cannot prove typed text was not copied from Oura; mitigated by the read-only tool profile for Oura-assisted sessions.
- **Scanned PDFs / images**: no local OCR; the conversational model reads images/pages directly and saves extracted facts citing the original. HEIC originals are stored but not returned inline as images.
- **Search** is substring (any term), not semantic; CJK works; a zero-hit search returns current routines/questions as a hint.
- **Eval harness** uses Codex CLI (same model family, same MCP tools) as the host, not the ChatGPT web client; tool-approval is auto-approved there to stand in for the user confirming ChatGPT's write prompt. The judge is the same model family (independent context, no tools); per-case evidence is saved for human review.
- **Mac sleep/wake and phone lock-screen** behavior: not tested in this run (NOT_RUN in recovery report).
- **Apple export parser upgrade**: rows imported before parser v4 carry `x:` ids. Migration v6 recomputes every origin_key (whole-second instants) and attributes those rows to the one export this archive ever imported; the next import of that same export retires them page by page. With more than one export ever imported, pre-v4 rows are left in place (never retired by guess) and can overlap newer rows of a different export.
- **Correlation elements** (e.g. blood-pressure pairs) are imported as their child Records; the parent's own attributes/metadata are counted in `unsupported_elements`, not stored.
- **Ingest connections**: 16 concurrent, at most 4 per source address, each bounded (~70 s). Many distinct LAN addresses stalling at once could still delay a phone upload; LAN-only exposure, and the phone retries from its outbox.
- **Scale**: measured at 200k observations + 5.2k records (test-report/perf_bench.json): capture p95 ≈1 ms, warm bootstrap p95 ≈0.43 s, 2 MiB original save p95 ≈51 ms, month aggregate p95 ≈37 ms. Not load-tested near 100 GB.
- **Notifications**: optional content-free macOS notification exists but is off; there is no push into existing ChatGPT threads (platform does not offer it) — proactive items wait in the local outbox for the next conversation.
