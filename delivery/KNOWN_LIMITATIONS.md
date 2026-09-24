# Known limitations (linked to the original A–L acceptance criteria)

Status words: **met** (verified here), **partial**, **gap** (not met), with the reason and what closes it.

| # | Criterion | Status | Detail |
|---|---|---|---|
| A | Capture: text, voice transcript, photo, document | **partial** | Backend + MCP tools verified end-to-end with a strong model (gpt-5.6-sol via Codex, same MCP server, synthetic HTTPS file host): text/voice-transcript capture with committed receipts, photo and 2-page PDF saved with matching SHA-256, per-page extraction. **Not yet observed inside the target ChatGPT client** (tunnel/app creation needs your account — USER_ACTIONS §1). ChatGPT's real file-download host is unknown until that probe, so `allowed_download_hosts` is empty and every attachment download is refused on purpose. |
| B | Memory across conversations | **partial** | Separate processes/sessions recover records, revisions, open questions, preferences (tests; eval memory: dev 6/6, holdout 1/2 — E016 paging miss). ChatGPT fresh-chat read not yet observed live. |
| C | Cross-source investigation (Apple + Oura + user + files) | **gap** (Oura), partial (Apple) | Oura: no official MCP route available to this account was found; Oura data is never persisted by design (terms). Apple: XML backfill importer works on synthetic exports; live iPhone sync code exists but is not built/installed (no Xcode). User + files: met in eval. |
| D | Broad model discretion | **met (model eval)** | Bootstrap is an index; search/read/bounded SQL/original pages are available; eval E027 ("only the summary?") initially failed and was fixed; see eval-report. |
| E | Sparse proactivity / valid silence | **met (model eval + unit)**, shadow **pending** | Worker installed under launchd, runs every 15 min, no model calls without relevant change. Background model is **disabled** by default (privacy/quota decision, USER_ACTIONS §4). Real multi-day shadow observation has **not** happened (started 2026-09-23). |
| F | Revisitability | **met (model eval)** | Revisit cases pass with the real worker + strong model on synthetic data; not yet observed on real data over time. |
| G | Measurement-gap awareness | **met (model eval)** | Measurement category passes (posture, hypertrophy, unknown-unknown with decision value). |
| H | No fake certainty | **partial (residual risk)** | Dev 42/42 after fixes. **Blind holdout 12/14**: E040 hard failure (background surfaced an irrelevant measurement-gap alert — fixed, non-blind rerun passes), E016 did not page through notes before claiming nothing else exists. Model behavior is probabilistic. |
| I | One interface | **partial** | No dashboard/app for daily use exists. The iPhone helper is only a one-time setup screen. But the ChatGPT app is not connected yet, so today the only way to talk to the system is through a tool-capable client (e.g. Codex CLI) — this is the main gap. |
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
- **Scale**: measured at 200k observations + 5.2k records (test-report/perf_bench.json): capture p95 ≈1 ms, warm bootstrap p95 ≈0.43 s, 2 MiB original save p95 ≈51 ms, month aggregate p95 ≈37 ms. Not load-tested near 100 GB.
- **Notifications**: optional content-free macOS notification exists but is off; there is no push into existing ChatGPT threads (platform does not offer it) — proactive items wait in the local outbox for the next conversation.
