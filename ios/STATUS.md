# iOS Health Sync Helper status

## Compiled and tested on this machine

- Host: macOS 15.7.4 arm64; Swift 6.1.2; Command Line Tools SDK only; `xcodebuild` and an iOS SDK are unavailable.
- `HealthSyncCore`: `swift build` exit 0; `swift test` 27 tests (Swift Testing, parameterized cases included) pass.
  Full output: `ios/test-report/ios_core_swift_test.log` (`bash ios/test-report/run-core-tests.sh`).
- macOS SDK `arm64-apple-macos14.0` type-checks pass for authorization, keychain, pairing, the anchored HealthKit
  coordinator and the SwiftUI app: `ios/test-report/ios_typecheck.log` (`bash ios/test-report/typecheck.sh`).
- Synthetic Swift ↔ Python ingest interop passes (loopback TLS, disposable store): `ios/test-report/interop.log`.

## Acceptance fixes (consult round 1) and the tests that cover them

Each test below was run and seen failing against the previous code before the fix.

- F05 producer equivalence (`contracts/normalization.md`): `HealthSyncCore/Normalization.swift` holds the requested
  types and the export.xml strings. Sleep samples send `value_text` = `HKCategoryValueSleepAnalysis…` and
  `value_num` = raw int, unit `""`; an unmapped category value sends `value_text` nil rather than a made-up string.
  Workouts send `value_text` = `HKWorkoutActivityType…` (every SDK activity type mapped, fallback
  `HKWorkoutActivityTypeOther`), `value_num` = duration seconds, unit `s`. Quantity units are the export.xml strings
  (`count`, `kcal`, `min`, `count/min`, `ms`, `%`, `mL/min·kg`, `kg`), converted with `HKUnit(from:)`. Authorization
  and the anchored queries both derive their type list from these tables. Tests: `quantityUnitsMatchExportXML`,
  `sleepCategoryWireFieldsMatchExportXML` and `workoutWireFieldsMatchExportXML` parse synthetic export.xml rows;
  `tablesMatchHealthKitSDK` checks the tables against the SDK's HealthKit enum values and HKUnit strings.
- Oura-origin samples are uploaded as-is since 2026-09-24, per owner decision.
- F30 outbox durability: all file I/O goes through `OutboxFiles` (`POSIXFiles` in production). Write, fsync of the
  temp file, rename, fsync of the directory, removal and index writes now propagate their errors; nothing is
  ignored. The in-memory index changes only after the durable index write. An index file that exists but can't be
  read makes open fail rather than resetting checkpoints. Tests with an injected failing file layer, each reopening
  the store: `enqueueFailsWhenPageIsNotDurable` (write, sync, move), `enqueueFailsWhenDirectoryEntryIsNotDurable`,
  `failedIndexWriteKeepsPageUnacknowledged` (write, sync, move),
  `failedPageRemovalAfterDurableAckIsReportedAndRecoveredOnReopen`, `failedResyncJournalRemovalIsReported`,
  `unreadableIndexFailsOpenInsteadOfResettingCheckpoints`.
- F31 drain reentrancy: `SyncEngine.drain` claims the drain guard before its first `await` and releases it with
  `defer`. Test: `simultaneousDrainsWithSuspendedTokenUploadOnce` (one token call, one upload).
- F32 TLS challenge: every `urlSession(_:didReceive:)` path calls its completion handler exactly once. Test:
  `everyChallengePathCompletesExactlyOnce` covers accepted pin, wrong pin, missing trust, HTTP Basic and client
  certificate, using a synthetic self-signed certificate embedded in the test.
- F33 paging: `HealthSyncCore/PageDecision.swift` keeps "initial history complete" (set once, sticky) separate from
  "this page was full" (samples + deleted ≥ 500, so query again). `AnchoredSyncCoordinator` uses it and requeries
  only after the full page is durably queued. `OutboxStore.initialHistory` also counts a completing page that is
  queued but not yet acknowledged. Tests: `fullPagesAfterBootstrapKeepQuerying` (501 → 2 queries, 1001 → 3, plus
  the 500, 499 and 0 edges), `bootstrapCompletesOnlyOnShortPage`, `mixedSamplesAndDeletionsFillAPage`,
  `pendingCompletingPageEndsInitialHistoryBeforeAck`.

## Not verified here

- No iOS Simulator/device build, app installation, signing, provisioning, entitlement validation, or real HealthKit permission/data flow was possible without Xcode.app and an iOS SDK.
- Background delivery, BGAppRefresh scheduling, device lock behavior, app termination/relaunch, HealthKit deletion tombstones, token revocation/re-pair UX, and LAN reconnect behavior require signed real-device tests in `BUILD_ON_DEVICE.md`.
- The local real Python-ingest interoperability probe passed using only a disposable synthetic store and loopback TLS server. It exercised pairing, DER leaf pinning (including wrong-pin rejection), two sequential batches on one stream, another stream, replay ACK, authenticated status, and preservation of an Oura-origin sample in the outbox. Full output: `ios/test-report/interop.log`; rerun with `bash ios/test-report/run-interop.sh` from repository root.
- The interop probe is host/macOS validation, not an iOS SDK or device test.
- macOS type-check does not prove iOS SDK compatibility or iOS runtime behavior.
- `AnchoredSyncCoordinator` has no automated test here: it needs a live `HKHealthStore`. Its paging decision and wire mapping are covered through `PageDecision` and `Normalization`. Draining 501 or 1,001 real HealthKit changes after bootstrap without another observer event still needs a device run.
- Cross-producer `origin_key` equality also depends on the Python side (`src/phctx/apple_export.py`, ingest) applying the same contract. That is outside `ios/` and was not verified in this slice.
- The requested final log paths under sibling `delivery/test-report/` were not written because this task was constrained to changes inside `ios/`; logs are in `ios/test-report/` for integration to copy if authorized.

## Data handling

The implementation contains no fabricated Health measurements or health-value logging. The client pins the certificate leaf SHA-256, stores the paired device token and installation ID in Keychain, requests HealthKit read permissions only, and preserves HealthKit samples in the outbox as-is. Outbox data is protected with `completeUntilFirstUserAuthentication` on iOS; actual lock/unlock behavior still needs device verification.
