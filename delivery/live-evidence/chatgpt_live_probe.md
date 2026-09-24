# Live ChatGPT probes — 2026-09-24 (target account: ChatGPT Pro, web, model "6 Pro")

Connection: Secure MCP Tunnel `phctx-mac` (tunnel-client 0.0.14, launchd `com.personalhealthcontext.tunnel`), runtime key reused
from the existing WebCodex tunnel config at the user's direction (read into the process only; not copied). ChatGPT developer-mode app
"Personal Health Context" created via chatgpt.com/plugins → Connection: Tunnel → No Auth; ChatGPT confirmed "now connected" and
listed all tools (capture tagged PUBLIC WRITE). Server log (redacted): chatgpt_mcp_server_log_2026-09-24.txt. DB checks:
chatgpt_db_verification_2026-09-24.json.

| Probe | ChatGPT conversation | Server evidence | Result |
|---|---|---|---|
| Write | "保存连通性测试注记": context_capture of a SYNTHETIC nonce note | `tool=context_capture status=ok`; record rec_21014a3c… + receipt request_id phctx-probe-96de343d committed 05:12:35Z | PASS |
| Fresh conversation read | new chat "Find Probe Note Details": found record id + exact text | `tool=context_search status=ok` ×3 | PASS |
| Photo original bytes | new chat, synthetic PNG attached; first try refused `file_host_not_allowlisted` (host observed: oaisdmntprnorthcentralus.blob.core.windows.net) → allowlist set → retry | `file_saved sha=89d93e860685 size=157`; local sha256 == stored blob sha256 == 89d93e86…27a | PASS (and the model correctly said "not saved" on the refused attempt) |
| Document + page numbers | new chat, synthetic 2-page PDF; second regional host observed (oaisdmntprcentralus…) → allowlist changed to anchored pattern `re:oaisdmntpr[a-z0-9]{2,24}\.blob\.core\.windows\.net` → retry | `file_saved sha=ad7af05ff1c4 size=1371`, extraction done, 2 pages; context_read_original pages ok; model reported Test Analyte D on page 2 with value/unit/interval from saved pages | PASS |
| Prompt-injection document | synthetic PDF containing an injected instruction line | ChatGPT's own safety check blocked the save; nothing persisted | host-side refusal (not a phctx behavior) |
| Real-data investigation (production archive, values not recorded here) | new chat "静息心率变化分析": multi-year resting-HR question | bootstrap ×2 + ~20 read-only `context_query` calls (1 rejected, then corrected); answer gave yearly day-weighted stats, coverage denominators, gaps, device-provenance conflict (source label vs hardware metadata), "backfill only, not current", no diagnosis | PASS (qualitative; no health values stored in evidence) |
| Write confirmation | ChatGPT asked "Allow once / Always allow" before each write | host behavior; approved once per call during probes | observed |

Not exercised: ChatGPT mobile app (needs the user's phone), live voice dictation (text path verified; dictation produces text).

## Round 2 — re-probe on the final tree (commit c27db58, schema v8, 2026-09-24 04:47–05:30 CDT)

Tunnel restarted after the last source change (MCP server process started 04:45:08, after every src/contracts/prompts edit).
Runtime key read from Keychain `phctx-tunnel-key` only (the WebCodex file fallback is removed). Server log:
`chatgpt_mcp_server_log_round2.txt` (request ids redacted).

| Probe | ChatGPT conversation | Server / DB evidence | Result |
|---|---|---|---|
| Write | new chat, nonce `phctx-probe-b7133b6b` | `tool=context_capture status=ok` 04:47:47; record `rec_2df2aed260584504943690012ba5dbf1`, receipt `phctx-probe-b7133b6b-round-2`; ChatGPT showed the same id and `committed` | PASS |
| Fresh conversation read | new chat: "find the note containing phctx-probe-b7133b6b" | `context_search` + `context_read`; ChatGPT returned the exact id and text | PASS |
| Photo original bytes | new chat, synthetic PNG. First attempt: host `oaisdmntprnznorth` not on the exact list → `file_host_not_allowlisted`, ChatGPT said "nothing was saved"; host added, retry | `file_saved sha=89d93e860685 size=157`; record `rec_9fa2b9203bdf4466a645332f2487440c`; stored blob SHA-256 = local SHA-256 = `89d93e86…04127a` | PASS |
| Document + page read | new chat, synthetic 2-page PDF. First attempts: hosts `oaisdmntprwestus2`, `oaisdmntprindiasocentral` refused ("nothing saved"; ChatGPT separated "read from the attachment" from "read from the server original"); hosts added, retry | `file_saved sha=ad7af05ff1c4 size=1371`, extraction done, 2 pages; record `rec_22ed05ed5fe64442b8820ee515c570d3`; stored = local = `ad7af05f…4ff4ec19`; `context_read_original` pages → "Test Analyte D 41 U/L 10 - 40 H" from the server original | PASS |
| Real-data investigation | new chat: "about how many steps a day in 2025, any quarterly change, how were coverage and device overlap handled?" (values not recorded here) | bootstrap + ~25 read-only `context_query` calls on `canonical_observations` (some rejected — SQL errors or the 8 s budget — then narrowed); 10 min | PASS (qualitative): quarterly figures with day-coverage denominators, a phone-only cross-check, same-day devices not summed, stated that exact interval merging was not done because a query was interrupted, flagged source labels that disagree with hardware metadata, no diagnosis |
| Write confirmation | ChatGPT asked "Allow once / Always allow" before each write; one dialog warned a retry passed a path string instead of a file object — that attempt never reached the server | host behavior | observed |

Observed file hosts: ChatGPT used five regional storage accounts across this day's uploads (`oaisdmntpr` +
northcentralus, centralus, nznorth, westus2, indiasocentral). Exact hosts only (consult round 2, R2-10): each new
region is refused once, with "nothing saved" and the host logged, until added; `ops/status.sh` lists any pending.
