# Build and test on a signed iPhone

The iOS target is source-complete but cannot be built or installed on the current machine because it has Command Line Tools only (no Xcode.app or iOS SDK). An iOS build and signed-device run remain required.

## Build

1. Install the current Xcode from the Mac App Store or Apple Developer downloads, open it once, accept the license, and install the iOS platform SDK.
2. Select Xcode and its command-line tools in Xcode Settings → Locations. Verify with `xcodebuild -version` and `xcodebuild -showsdks`.
3. Install XcodeGen: `brew install xcodegen`.
4. From this directory generate the project: `xcodegen generate --spec project.yml`.
5. Open `HealthSyncHelper.xcodeproj` in Xcode. Set the `HealthSyncHelper` target's Team to the user's Apple development team. Replace the `suffix` in `PRODUCT_BUNDLE_IDENTIFIER` with a unique value, keeping `com.personalhealthcontext.healthsync.<suffix>`.
6. In Signing & Capabilities, enable HealthKit and HealthKit Background Delivery; confirm the checked-in entitlements are included and the provisioning profile supports them.
7. Connect the user's authorized iPhone, select it as the run destination, build and install. Do not claim a simulator build demonstrates access to real HealthKit data or background execution.
8. On the Mac run `phctx pair-device`, then use the generated pairing URL or enter host, port, certificate SHA-256 pin and the one-time pairing code in the helper. Pairing code expires after 10 minutes and can be used once.

## Real-device acceptance checks

Use actual, user-authorized data only. Never create fake Health measurements to pass tests. Record device model, iOS version, Xcode/SDK version, bundle identifier, signing status, non-sensitive counts, and ACK hash only.

1. Build with the real compiler, correct entitlements, and install on the user's authorized iPhone.
2. Request read permissions and confirm the requested types; the helper does not request Health write access.
3. Confirm one real, allowed sample reaches the paired Mac and its stream/source metadata are preserved.
4. Resend an unchanged batch and verify the server returns the same stored ACK without duplicate effect.
5. Where feasible and authorized, delete a test sample and verify its HealthKit UUID is sent as a tombstone. Otherwise use an isolated HealthKit test fixture and report that limitation.
6. Enable Airplane Mode, observe pending batches accumulate, reconnect to the same LAN, and confirm ordered delivery.
7. Terminate and relaunch the app with pending data; confirm outbox contents survive and are sent once connectivity returns.
8. Test lock/unlock and background delivery behavior; record system scheduling variability instead of assuming immediate execution.
9. Revoke the device token on the Mac with `phctx revoke-device <installation_id>`; confirm the client retains the outbox and reports “Re-pair needed”.
10. Restart the Mac ingest server while batches are pending; confirm transient failures back off and delivery resumes after the server returns.
11. Inspect device/server diagnostics and verify no health values or sensitive payloads are logged.

## Current-machine limits

`HealthSyncCore` is platform-independent and can be tested with Swift on the current Mac. The HealthKit app sources may be syntax/type-checked only where the installed macOS SDK exposes compatible declarations; that does not prove iOS API availability, entitlements, linking, code signing, or runtime behavior. See `STATUS.md` and the generated test logs.
