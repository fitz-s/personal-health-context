# Source policy (as implemented, 2026-09-23)

| Source | Policy | What persists locally | Enforcement |
|---|---|---|---|
| User conversation (text, voice transcript) | durable | original wording, parsed payload, time + timezone, revisions | `context_capture` / `context_revise` with committed receipt |
| User files (photos, PDFs, documents) | durable | original bytes (SHA-256 named, fsynced, never recompressed), derived page text, extracted facts citing `obj:<sha>#p<n>` | `context_capture_file`: host file object → allowlisted HTTPS download → verify → commit |
| Apple Health XML export | durable, **backfill only** | allowed observations with source name/device/metadata/timezone; import manifest | `phctx import-apple` |
| Apple Health live (iPhone helper) | durable | HealthKit UUID samples + tombstones, per-stream sequence | paired device token → `ingest-server` |
| **Oura** (any path: REST, official MCP, Apple Health mirror, copy-paste) | **ephemeral / not persisted** | nothing: no values, no responses, no derived summaries, not in logs, backups, evals | (1) `register_source` refuses durable Oura sources; (2) importer + ingest drop samples whose source name, bundle id, device or metadata mention Oura, counting them only; (3) worker gate refuses non-durable evidence; (4) Oura-assisted sessions must use `phctx mcp --profile readonly` (write tools not listed and refused server-side); (5) a chat statement cannot change policy — only the operator can |
| Synthetic test data | synthetic profile only | fixtures | `synthetic:` sources rejected in the production profile; data roots are bound to one profile (`PROFILE` marker) |

## Why Oura is not persisted
The Oura API agreement (reviewed per package doc 08, clauses 3(m), 4(d), 4(a)) restricts supplying Oura data to LLMs outside its official MCP path and caching/storing MCP-delivered data, and restricts eval/embedding uses. No official Oura MCP endpoint available to this account was found in this run, so Oura is `not_connected`. A formal permission or a later explicit rule change is required before `oura` could become durable, and only the operator can change it.

## Known residual risk
The server cannot prove that free text typed by the user was not copied from an Oura screen. Mitigation is the read-only profile for Oura-assisted sessions plus the policy prompt; this is disclosed, not claimed as a guarantee.

## Data boundaries
- Local archive: Mac only; FileVault reported ON by `phctx doctor`.
- Sent to OpenAI: whatever the user types/attaches in ChatGPT, and whatever phctx tools return into a conversation or into a background Codex run (user's ChatGPT account).
- Encrypted backups: AES-256-GCM archives (key in Keychain `phctx-backup`) may be written to iCloud Drive; the live database never is.
