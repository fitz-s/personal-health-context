# Official-source re-check (2026-09-23, this run)

| Ref | Source | Fetch result | What was confirmed |
|---|---|---|---|
| S1 | help.openai.com developer mode article | **HTTP 403** to automated fetch | Not re-verified. Account observed directly instead: Pro plan, Developer mode switch ON (live-evidence). Whether Pro gets write tools is decided by the live probe, not docs. |
| S3 | developers.openai.com Secure MCP Tunnels guide | OK | tunnel-client from Platform settings or github.com/openai/tunnel-client releases; Tunnels Read+Manage to create, Read+Use to run; runtime API key `CONTROL_PLANE_API_KEY`; `tunnel-client init --sample sample_mcp_stdio_local --profile … --tunnel-id … --mcp-command "…"`, `doctor --explain`, `run`; ChatGPT: chatgpt.com/plugins → developer-mode app → Connection **Tunnel**. Page does not say whether ChatGPT Pro personal accounts are supported. |
| — | github.com/openai/tunnel-client release v0.0.14 (2026-09-01) | OK | Apache-2.0; darwin-arm64 zip SHA-256 `b540493c5bdbcdbb755700c8e2e16597e28b1569e425007e0f73111047bd6a64` matched release SHA256SUMS. `--help` confirms `init/doctor/run`, `--mcp-command`, secret refs `env:`/`file:`. |
| S4 | fileParams | from package contract (docs/08) | Implemented exactly: `_meta["openai/fileParams"]=["file"]`, file object with download_url/file_id required, mime_type/file_name optional. The download host must be observed live before allowlisting. |
| S6 | cloud.ouraring.com/legal/api-agreement | OK | Effective June 8, 2026. 3(m) "shall not store or cache any data"; 4(a) "solely for the Authorized Purposes"; 4(d) "solely through the MCP Server". Describes an Oura-operated MCP Server; no URL/access procedure given. |
| — | ChatGPT plugin directory search "Oura" | OK (browser, read-only) | No Oura plugin/connector listed. |
| S10 | PyPI `mcp` | OK | 2.2.0, MIT, Python ≥3.10; `mcp_types.Tool` has `annotations` and `meta` (alias `_meta`) — used to publish fileParams. |
| — | Codex CLI 0.146.0 with the user's ChatGPT login | OK | `gpt-5.6-sol` available; `gpt-6-astra` "requires a newer version of Codex"; `gpt-6-luna` "not supported when using Codex with a ChatGPT account". |

Not re-checked this run (see agent section below for S2/S4/S7): S5 Apple developer pages (JS-rendered; Xcode not installed anyway), S7/S8 API data/file docs (API path not provisioned), S9 Tasks.

## Additional findings from the source-verifier agent (curl/WebFetch, 2026-09-23)
| Ref | Source | Result | Finding (short quotes) |
|---|---|---|---|
| S2 | developers.openai.com/api/docs/guides/developer-mode | HTTP 200 | "Available to Pro, Plus, Business, Enterprise, and Education accounts on the web." "…full Model Context Protocol (MCP) client support for all tools, both read and write." "Write actions by default require confirmation." Tools without `readOnlyHint` are treated as writes. Mobile not mentioned (unknown, not "unsupported"). |
| S4 | developers.openai.com/plugins/reference | HTTP 200 | File object: `download_url`,`file_id` required; `mime_type`,`file_name` declared, not required; "ChatGPT always includes `download_url` and `file_id`; it may omit `mime_type` and `file_name`." Widget APIs `window.openai.uploadFile`, `selectFiles`, `getFileDownloadUrl` ("temporary download URL"). Download host, URL lifetime and size limit NOT documented → allowlist must come from the live probe. Annotations "only influence how ChatGPT or Codex frames the tool call… servers must still enforce their own authorization logic." |
| S7 | developers.openai.com/api/docs/guides/your-data | HTTP 200 | Responses API: 30-day application-state retention by default or `store=true`; `store=false` is not zero retention (abuse-monitoring logs up to 30 days). |
| S9 | help.openai.com scheduled tasks | HTTP 403 | Not verified; design does not rely on Tasks (local outbox instead). |
| S6 | Oura agreement | HTTP 200 | 3(m) full: "Unless and until you are authorized to do so by the Company in a separate written agreement or consent, you shall not store or cache any data accessed or obtained through the MCP Server." 4(d): LLM use "solely through the MCP Server"; Oura API may not be used "to develop, train, fine-tune, evaluate, prompt, or otherwise provide input or data to any AI Model or AI Platform." No user-facing Oura→ChatGPT/Claude enablement route found (ouraring.com sitemap has no mcp/chatgpt/claude strings). |

Implication: docs now say Pro gets write tools on web; still marked BLOCKED until observed live (docs ≠ account test).
