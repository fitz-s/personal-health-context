# iPhone helper: local implementation/build contract

This directory is a construction specification, not a compiled iOS app. The local agent must create a real Xcode project and compile/test it with the available Apple SDK. Do not deliver a Swift-shaped text file as an installed integration.

## Minimal visible surface

One setup screen: purpose, data-sharing boundary, selected read permissions, Mac pairing, last successful sync/status, revoke/disconnect. No dashboard, meal logging or daily reporting UI. Routine data collection does not require reopening the helper, subject to iOS scheduling constraints.

## Components to implement

`HealthAuthorizationCoordinator`: request only agreed read types, no write access. Configure HealthKit capability and the current SDK's background delivery entitlement/usage-description requirements; verify through official docs and signed-device testing.

`AnchoredSyncCoordinator`: per supported sample type, observer notification → serialize query per stream → anchored page → preserve source/UUID/unit/time/deleted UUID. Call HealthKit completion appropriately after local durable processing; never hold an observer callback waiting indefinitely for the network. Initial anchored query needs a bounded historical policy; XML history and live UUID records may overlap, so track backfill/native provenance and reconcile without double aggregating.

`OutboxStore`: transactional local persistence of batch payload, predecessor, sequence, next anchor, retry state. Use protected file storage appropriate to actual background availability and Keychain tokens; data protection/locked state must be tested, not guessed. Query anchor may advance only when that exact page is durably in the outbox; remote ACK alone authorizes deleting it. Do not store the only copy of an unacknowledged page in memory.

`Uploader`: HTTPS device-authenticated dedicated ingest, not the ChatGPT stdio tunnel. No arbitrary trust-all certificate handling. Pair through a one-time code/QR using a route and certificate controlled by the user; on local network validate actual server identity. Existing VPN may be reused when available; LAN-only is acceptable as a disclosed latency limitation, with outbox recovery on return. Device token bound to installation/source ingest only, not context-query or model permissions.

## Wire protocol

Use `contracts/apple_batch.schema.json`. Metadata is untrusted until the server verifies the paired installation and allowed Apple-source policy. Do not let the phone choose an arbitrary durable source or bypass Oura restrictions. Sequential stream: previous_batch_id must match committed predecessor; replay same batch/content returns stored ACK; same ID/different hash409; future sequence409/retry after gap recovery.

Server commit: normalize/validate page → upserts/deletions + source cursor/coverage + receipt in one transaction → ACK `{batch_id,request_hash,committed:true,upserted,deleted,server_time}`. Client ACK loss replays identical batch. Deleted unknown UUID is a tombstone, never a negative measurement.

## Required device tests

Build with real compiler and correct entitlements; install on user's authorized iPhone; request permissions; one real allowed sample arrives at Mac; resend unchanged batch; delete test sample where feasible/authorized or validate deletion with isolated HealthKit test fixture; airplane mode queue and reconnect; app termination/relaunch preserves outbox; lock/unlock behavior; token rejection; Mac restart; no sensitive console logs. Never create fake measurements in the user's Health store merely to make a test pass.

Capture hardware/OS/SDK/bundle-id/signing status and non-sensitive counts/ACK hashes in delivery. A simulator-only result does not prove access to the user's device or real background execution. Distribution/signing requiring the user's Apple account is non-delegable; continue backend work while it is blocked.
