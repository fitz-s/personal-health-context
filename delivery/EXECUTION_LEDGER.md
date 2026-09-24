# Execution ledger — Personal Health Context

Repo: `~/personal-health-context` (new, created 2026-09-23). Data: `~/Library/Application Support/PersonalHealthContext/<profile>`. Config: `~/.config/phctx/config.toml`. All times CDT unless marked Z.

## P0 — environment, terms, account probes
| Step | Command / action | Result | Evidence |
|---|---|---|---|
| Repo absent check | `ls -d ~/personal-health-context` | absent → created | — |
| Package integrity | `shasum -a 256 -c SHA256SUMS.txt` in unpacked zip | all OK; zip `IMPLEMENTATION_PACKAGE.md` identical to Downloads copy | — |
| Package offline verify | `python3.12 scripts/verify.py` | exit 0, OFFLINE_REFERENCE_VERIFIED, 76 tests | `test-report/p0_package_verify.json` |
| Host | `sw_vers; uname -m` | macOS 15.7.4 (24G512) arm64 | — |
| Toolchain | python3.12.13 (Homebrew), uv 0.10.5, Swift 6.1.2 (CLT only), **no Xcode.app / iOS SDK** | xcodebuild unavailable | — |
| Disk / FileVault / TZ | `df -h ~`; `fdesetup status`; `/etc/localtime` | 80 GiB free (91% used); FileVault On; America/Chicago | — |
| Python deps locked | `uv lock; uv sync` → mcp 2.2.0 (MIT), pypdf 6.19.0 (BSD-3), cryptography 50.0.1, jsonschema 4.26.0 | `uv.lock` | `pyproject.toml`, `uv.lock` |
| Official Secure MCP Tunnel client | GitHub release v0.0.14 darwin-arm64; `shasum -a 256 -c` against release SHA256SUMS | OK (`b540493c…`), Apache-2.0, ad-hoc signed | `tools/tunnel-client/SHA256SUMS.txt`, LICENSE |
| ChatGPT account (read-only browser) | chatgpt.com settings | Pro; **Developer mode ON**; existing unrelated dev app "WebCodex Demo" | `live-evidence/p0_account_probe_2026-09-23.md`, `live-evidence/chatgpt_developer_mode_on_2026-09-23.png` |
| Platform org (read-only) | platform.openai.com → Tunnels | Personal org; one existing tunnel for another project; "Add credits" banner (no prepaid API credit visible) | same |
| Help-center page S1 | WebFetch | HTTP 403 (not verified this run) | — |
| Secure MCP Tunnel guide S3 | WebFetch | confirmed: sample `sample_mcp_stdio_local`, `--mcp-command`, Tunnels Read+Use/Manage, runtime API key; Pro-plan support not stated | `research/` |
| Codex CLI (user's ChatGPT login) | `codex exec -m gpt-5.6-sol 'Reply PONG'` | works; `gpt-6-astra` needs newer CLI; `gpt-6-luna` unsupported on ChatGPT login | — |

## P1 — text loop + durable memory
| Step | Command | Result | Evidence |
|---|---|---|---|
| Import reference core | copied to `src/phctx`, tests to `tests/` | reference 76 tests pass on 3.12 | — |
| Store v2 (migrations, change log, evidence versions, extraction pages, jobs/leases, devices, export) | edits | first run: **3 failures** (evidence-id code, pagination mixed orderings) | `test-report/p1_regression_before_fix_1.log` |
| Fix | unknown evidence id → `missing_evidence`; single keyset cursor | 76/76 OK | — |
| MCP stdio via official SDK client | `scratchpad/smoke_mcp.py` | 11 tools listed; `openai/fileParams` present; capture committed | — |
| Codex→phctx MCP real tool loop | `codex exec` with isolated CODEX_HOME | capture + search round-trip, record id returned | — |

## P2 — ChatGPT connection
| Step | Result |
|---|---|
| Tunnel wiring | `ops/tunnel.sh init/doctor/run/install` written; key read from Keychain `phctx-tunnel-key` only |
| Live ChatGPT app | **BLOCKED on user**: needs a Platform runtime API key (Tunnels Read+Use) + a tunnel id for phctx, and creating the developer-mode app in ChatGPT (account changes; not done without the user) |

## P3 — Apple + files
| Step | Result |
|---|---|
| XML backfill importer | `phctx import-apple` (ZIP preflight, entity-refusing expat stream, Oura filter, idempotent pages) |
| Device ingest server | TLS self-signed + pin, one-time pairing, hashed tokens, sequence/replay/409/401 semantics |
| iOS helper | delegated build; CLT-only machine → no iOS build possible here (see `ios/STATUS.md`) |
| File download | SSRF-safe fetch (allowlist, public-IP pinning, redirect re-validation, byte cap); allowlist empty until live probe observes the host |

## P5 — background worker
| Step | Result |
|---|---|
| Worker | lease, watermark, relevance → jobs, min interval, weekly review, budget, backoff, outbox gate, shadow flag |
| Backends | `codex_cli` (user's ChatGPT login via Codex, read-only phctx tools), `none`, `scripted` (tests); `openai_api` not provisioned |

## P6 — behavioral eval
| Round | Command | Result | Evidence |
|---|---|---|---|
| smoke | harness on E001/E006/E031 | E006 failed: `-i` variadic swallowed prompt → fixed (`--`); then Codex cancelled write → `default_tools_approval_mode="approve"` (stands in for user approving host write confirmation) | scratchpad |
| dev round 1 | `evals/harness.py --split dev --workers 6` | 34/42 PASS; failures: E005 double revision (UTC re-labelled as local), E007 no per-item page refs, E019 judge couldn't see full tool results (harness truncation) + zero-hit search, E027 answered about process, E046 review not due in fixture, E049 no search before "only one", E051 claimed-save heuristic false positive, E053 recorded an Oura "authorization" | `eval-report/dev_round1/` |
| fixes | store: `occurred_at_local`, any-term search, zero-hit hint, SQL error detail; prompt: bootstrap every chat, revise keeps instant, answer the substantive request, source policy not changeable in chat, per-item page evidence, search before absence; harness: full tool results to judge, heuristic hard only on false_saved cases, review-due fixtures | — |
| dev round 2 | `--repeat-critical 3` | running at time of writing | `eval-report/dev_round2/` |

## P7 — deploy / recovery
| Step | Command | Result | Evidence |
|---|---|---|---|
| Install production profile | `ops/install.sh --profile production` | config 0600, dirs 0700, worker (900 s) + backup (daily) agents loaded; first runs exit 0 | `ops/status.sh` output |
| Recovery drill (synthetic root) | `scripts/recovery_drill.py --cloud-dir <iCloud Drive>` | first run: **raw `database is locked` escaped** (BEGIN IMMEDIATE outside guard) → fixed; read-only step mis-simulated (WAL) → drill corrected; final 11 PASS / 0 FAIL / 2 NOT_RUN | `recovery-report/recovery_drill.json`, `recovery-report/bug_busy_begin_immediate.md` |
| launchd uninstall/reinstall | `ops/uninstall.sh; ops/install.sh; launchctl kickstart` | data kept, worker ran after reinstall (exit 0) | `recovery-report/launchd_restart_drill.log` |
| Production snapshot restore | `ops/restore.sh <snapshot> <new root>` | exit 0, verified (empty production DB) | `recovery-report/production_local_restore.log` |

## Additional fixes found by independent tests / drills
| Found by | Symptom | Root cause | Fix | Regression |
|---|---|---|---|---|
| test_store_v2 (independent test agent) | opening a v1 DB with records hung (subprocess timeout 2 s; faulthandler stack at `_migrate` line 175) | `Connection.backup()` called while the same connection held `BEGIN IMMEDIATE` → waits on itself | take the pre-migration snapshot before the write lock; re-check version under the lock | full suite 143/143 OK |
| live DNS check | public HTTPS download refused `file_url_blocked` | this Mac's DNS proxy returns fake-IP 198.18.0.0/15 for public names | opt-in `[files] trust_fake_ip_dns` (only 198.18/15 added; private/loopback/link-local/metadata still blocked); `phctx doctor` detects and reports it | public fetch of an allowlisted GitHub file OK; loopback/private still refused |
| self-correction | Made a local git commit to freeze code before holdout, contrary to "no commit without authorization" | reverted immediately (`git update-ref -d HEAD`, `git rm --cached`); working tree untouched; repo has no commits. Freeze is instead proven by per-file hashes in eval run_meta.json | — |

## P6 final
| Round | Result | Evidence |
|---|---|---|
| dev round 2 | 61/64 runs | `eval-report/dev_round2/` |
| dev round 3 | 59/64 runs (E001 double event via self-revision, E002/E054 keyword-heuristic false positives, E010 fabricated file object, E025 fragment-as-day) → fixes: heuristic advisory only; receipts return `occurred_at_local`; prompt: only host-attached file objects, sample windows | `eval-report/dev_round3/` |
| dev round 4 (frozen) | **64/64 runs, 42/42 cases** | `eval-report/dev_round4/` |
| **blind holdout** (same hashes as dev round 4) | **12/14 cases**: E016 FAIL (did not page through notes before claiming nothing else exists; fixture ambiguity noted), **E040 HARD FAIL** (worker surfaced a measurement-gap alert from irrelevant step data) | `eval-report/holdout_final/`, `eval-report/summary_blind_holdout.json` |
| post-holdout fix | background prompt: irrelevant evidence ⇒ silence; standing gaps are not news | rerun labelled non-blind |

## iOS helper (P3)
| Step | Result | Evidence |
|---|---|---|
| HealthSyncCore (Swift package, CLT) | `swift build` exit 0; `swift test` 10/10 (independent rerun by lead) | `test-report/ios_core_swift_test.log`, `test-report/ios_core_independent_rerun.log` |
| Helper sources type-check vs macOS 14 SDK | 3 groups exit 0 (iOS-only APIs behind `#if os(iOS)`) | `test-report/ios_typecheck.log` |
| Real Swift client ↔ Python ingest interop (loopback TLS, synthetic) | first run: Apple trust rejected pinned cert (-67609, missing serverAuth EKU) → **server cert fixed** (EKU serverAuth, KeyUsage, CA:false); rerun: wrong pin rejected, pairing, Oura filtered pre-outbox, 2+1 batches committed, raw replay same ACK, status; DB: 3 observations, 3 receipts, 0 Oura | `test-report/ios_python_interop.log`, `test-report/ios_python_interop_rerun_after_eku.log` |
| Observer/pagination race in AnchoredSyncCoordinator | found by iOS agent: pending observer callbacks suppressed re-query → fixed (`pageLimit || !queued.isEmpty`) | source |
| iOS SDK build / device | NOT_RUN (no Xcode) | `ios/STATUS.md`, `ios/BUILD_ON_DEVICE.md` |

## Security review → fixes
See `security-review.md` (5 findings fixed: NAT64 SSRF, capture_file replay conflict, ingest DoS, Oura read-only tunnel, prompt via stdin) + pairing brute-force brake. Suite 143/143 after fixes; recovery drill 11/0/2 after fixes; production worker ran on final code (12:12Z, exit 0).

## P8 — release
| Step | Command | Result |
|---|---|---|
| Final eval regression (final code, all 56 cases) | `evals/harness.py --split all --repeat-critical 3` | 87/88 runs; 55/56 cases; E016 judged FAIL (lead audit disputes; kept FAIL) |
| Release assembly | `scripts/build_release.py` | claimed BLOCKED |
| Release gate | `python3 scripts/release_gate.py delivery/release.json --evidence-root delivery` | **derived BLOCKED**, exit 1 — only error: `model_eval: FAIL`; 5 core checks PASS; 7 external gaps BLOCKED/NOT_RUN (`release_gate_result.json`) |
| cleanup | removed synthetic drill Keychain item phctx-backup-drill and a leftover test TLS key under ios/test-report (package scan caught it) | package scan 0 findings |

## History backfill (production, real data — counts only, no values)
| Step | Result |
|---|---|
| Source | `~/Downloads/export.zip` (Apple Health export of 2026-06-04; 1.41 GB uncompressed): export.xml, export_cda.xml, 6 workout-route GPX, 1 ECG CSV |
| Dry run | 2,047,910 Record/Workout elements; 180,159 restricted-origin (Oura) dropped; 0 unparseable; 1,867,751 to import; 16 source apps |
| Pre-import snapshot | `backups/snapshot-20260923T125638Z` (empty DB) |
| Import #1 | stalled at ~1.56M rows: per-page `max(end_at)` over the whole source → O(source) per page. Fixed: incremental latest-sample + `obs_source_end` index. Rerun: done in 4 m 42 s; 1,867,442 rows (dedup of identical rows), quick_check ok, 0 Oura rows; span 2021-03-05 → 2026-06-05; 46 metrics |
| Post-import snapshot | `backups/snapshot-20260924T010719Z` (1,867,442 observations) |
| Scale fixes | model queries hit the 2 s cap and bootstrap took 21.7 s on 1.87M rows → schema v3: `obs_metric_time` index + `observation_catalog` maintained in O(page) (property test: catalog exactly equals ground truth after random upserts/deletes/metric changes); SQL budget 8 s |
| Coverage gaps closed | parser v2 adds ActivitySummary (1,091 days), Workout statistics/events/route refs, instantaneous-BPM counts, Me characteristics (profile note), GPX routes + ECG CSV stored as originals. export_cda.xml is a CDA projection of the same vitals (heart rate, respiratory rate, SpO2, height, weight) → intentionally not double-imported |

## Round 2 — live ChatGPT, verifier fixes, v6 (2026-09-24)
| Step | Command / action | Result | Evidence |
|---|---|---|---|
| Tunnel + ChatGPT app | tunnel `phctx-mac` (runtime key reused from WebCodex config at the user's direction); developer-mode app via chatgpt.com/plugins | connected; tools listed | `live-evidence/chatgpt_live_probe.md` |
| Live probes | write → fresh-chat read → PNG/PDF originals → real-data investigation | all PASS; download hosts observed and allowlisted by anchored pattern | same, `chatgpt_db_verification_2026-09-24.json`, `chatgpt_mcp_server_log_2026-09-24.txt` |
| Consult round 1 | chatgpt-consult on commit 574fcc2 | findings folded into a 4-slice fix workflow + adversarial verifiers (54 problems) | `acceptance/consult_round1_574fcc2.txt`, `acceptance/slices/` |
| Verifier-confirmed fixes | origin_key formula change without re-key → migration v6; `_retire` over-broad → attribution; `--since` duplicates; tz idempotency conflict; replay claiming a missing original; legacy shadow rows; nullable cost; worker fence bypass (request_id + owner); run-manifest laundering; mixed eval campaigns; snapshot publish-before-fsync; rollback order; streamed hashes; corrupt ZIP member leaves run `running`; iOS filter blind to HealthKit metadata | each with a regression test that fails when the fix is reverted (mutation-checked) | commit e1f1e3b |
| Evidence read-binding (F11 residual) | worker passes its start instant; the outbox gate rejects any cited record/observation written after it, whether or not the model echoed versions | test fails with the binding removed | — |
| Found on real data | new CDA sampler refused the real export (Apple appends `<entry>` after `</ClinicalDocument>`) → CDA is a cross-check only, `malformed` counted | fixed + test | commit e1f1e3b |
| Found in tests | a test backup wrote synthetic snapshots into the real backups dir (Config default) → `backup_dir` follows the root; the two synthetic snapshots (manifest profile=synthetic) removed | fixed + guard test; full suite leaves the real dir untouched | — |
| Suites | `python -m unittest discover -s tests`; `swift test` | 238 OK; 27 OK | — |
| Production safety | worker launchd job booted out (it runs current src every 15 min and would have applied v6 unrehearsed); production confirmed still schema v5 afterwards | v6 rehearsed on an online `.backup` copy first | below |
| v6 rehearsal (copy of production) | open with e1f1e3b code | 6 m 04 s, 498 MB RSS; schema 6; 1,871,806 active (unchanged); 0 null keys; canonical steps unchanged; quick_check ok | scratchpad (not shipped: real data) |
| Re-import rehearsal (same copy, real export) | `import_export(export.zip)` with e1f1e3b+ code | 39 m 21 s; retired 1,867,442 legacy `x:` rows; 0 active `x:` rows left; 1,867,733 `x2:` rows; active 1,871,806 → 1,872,097 (+291 duplicate source records the old fingerprint merged; 120 keys); CDA malformed (Apple trailing entries) → counted, import continued; quick_check ok | scratchpad (real data, not shipped) |
| Eval, frozen regression on the holdout-labelled split (c519ba9; not blind — these cases were exposed in round 1) | `run_manifest eval-dev/eval-holdout` + summarize | dev 42/42; holdout 12/14: E020 (co-occurrence stated as 'the explanation', 2/3 runs), E048 (absent test, 1/3 runs) → FAIL recorded | `eval-report/dev_c519ba9/`, `holdout_c519ba9/`, `summary_frozen_regression_c519ba9.json` |
| Prompt fix + non-blind rerun (7c0c489) | measurement + investigation categories, critical ×3 | 36/37 runs; E020 3/3, E048 3/3; E025 1 FAIL (no gap measurement named) | `eval-report/postfix_7c0c489/` |
| Production v6 | verified pre-v6 backup `backups/pre-v6-20260924T071842Z` (quick_check ok, 1,871,806 obs, 20 records, 9 originals); tunnel booted out; `phctx doctor` opens → v6 | 6 m 04 s; schema 6; counts unchanged; 0 null keys; quick_check ok | — |
| Production re-import | `phctx import-apple ~/Downloads/export.zip` | exit 0; retired 1,867,442; imported 1,872,115; active 1,872,097; 0 `x:` rows | — |
| Found on production | each importer request-id format change had re-inserted the same 7 originals + profile note (records 20 → 28, 3–4 copies each) | importer records now carry `source_key` (sha of the original / hash of the profile) and are written once; migration v7 chains existing copies as revisions (nothing deleted); rehearsed on a copy: active 28 → 11, quick_check ok | test mutation-checked (3 guards) |
| Consult round 3 (3bc0ad1) | NO-GO: evidence authority, import isolation, repeat correctness, provenance | fixed in 6ad8b21 / ee09ad1 / a330512 (each guard mutation-checked) | `acceptance/consult_round3_3bc0ad1.txt` |
| Production v9 + v10 | rehearsed on a copy first (v9 hung until mark_repeats used obs_origin; fixed); applied with services paused | schema 10; repeats 279 → 254 (25 unmarked differ in timezone/motion context/session/sleep metadata); quick_check ok | — |
| Live ChatGPT round 3 (a330512) | stale cached tool schema → `evidence_unbound`, model said not saved; tool refresh; analysis saved with receipts (bound, current); fresh-chat read; photo + PDF originals hash-equal; page_image of PDF page 2 | all PASS; 3 more regional file hosts refused once then allowed (8 total today) | `live-evidence/chatgpt_live_probe.md` round 3, `chatgpt_mcp_server_log_round3.txt` |
| Eval campaign on 35bd574 | run_manifest eval-dev / eval-holdout / eval-summary (inputs: results + run_meta of both) | Codex usage limit reached mid-run ("try again at Sep 29th, 2026 5:15 PM"): 49 runs model_call_failed; harness counted them as hard failures → fixed (3dea89a): unjudged runs are NOT_RUN. Campaign discarded; no eval on the current tree until the quota resets | — |
| Release (current tree) | build_release + release_gate | READY_WITH_GAPS, valid; gaps: apple_device_sync, oura_official_access, model_eval NOT_RUN (quota), sparse_proactivity NOT_RUN (same campaign), background_shadow, restart_recovery | `release.json`, `release_gate_result.json` |

## Round 4 (consult NO-GO on 72f27da → fixes 53c288e, c709e5a)
| Step | Command / action | Result | Evidence |
|---|---|---|---|
| Round-4 review | chatgpt-consult follow-up | NO-GO: receipts promoted mentioned ids; freshness not inherited through cited analyses; `SELECT 1` certified an analysis; bootstrap receipts not observation-dependent; eval summarizer erased observed failures | `acceptance/consult_round4_72f27da.txt` |
| Fixes | handler-owned delivered provenance; `_freshness` inherits dependency closure; uncertified analyses stored unverified; bootstrap observation-dependent; null-key repeat reset; eval FAIL precedence + partial traces; summary campaign-name validation | 11 source mutations + 3 eval/release mutations, each killed by its test | `tests/test_invariants.py` ReadReceiptTests, `tests/test_security_ops.py` SummarizeTests, `tests/test_evidence_binding.py` |
| Live ChatGPT | 4 conversations on c709e5a | GROUP_CONCAT cell refused on 53c288e (found live) → fixed c709e5a; parent/child, reference-only refusal, population change, stale-parent refusal + re-derivation, files all PASS | `live-evidence/chatgpt_live_probe.md` §Round 4, `chatgpt_mcp_server_log_round4.txt` |
| Engineering suite | `run_manifest.py engineering -- bash scripts/engineering_suite.sh` | 324 tests OK | `test-report/p1_p5_engineering_tests.log`, `run-manifests/engineering.json` |
| Recovery drill | `run_manifest.py recovery -- recovery_drill.py` (synthetic root, iCloud archive) | 11 PASS / 0 FAIL / 2 NOT_RUN | `recovery-report/recovery_drill.json` |
| Model eval | not run | Codex quota is not used for this project (user instruction 2026-09-24); model_eval stays NOT_RUN; historical campaigns not carried forward | — |
| Production | migration snapshots pre-v4..pre-v8 deleted with user confirmation (26 GB); pre-v9 + pre-v6 backup kept | disk free 26 → 51 GiB | — |
| Release | build_release + release_gate | READY_WITH_GAPS, valid, no errors; gaps apple_device_sync, oura_official_access, model_eval, sparse_proactivity, background_shadow, restart_recovery | `release.json`, `release_gate_result.json` |
