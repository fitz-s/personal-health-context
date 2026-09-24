#!/bin/bash
set +e
printf 'iOS source type-check probe\nDate: '; date '+%Y-%m-%d %H:%M:%S %Z'
printf '\nSwift: '; swift --version | head -n 1
sdk=$(xcrun --sdk macosx --show-sdk-path)
printf '\nSDK: %s\n' "$sdk"
core=/Users/leofitz/personal-health-context/ios/HealthSyncCore
app=/Users/leofitz/personal-health-context/ios/HealthSyncHelper
printf '\nCommand: swiftc -typecheck -target arm64-apple-macos14.0 -sdk "$sdk" -I HealthSyncCore/.build/arm64-apple-macosx/debug/Modules HealthAuthorizationCoordinator.swift KeychainStore.swift PairingURL.swift\n'
swiftc -typecheck -target arm64-apple-macos14.0 -sdk "$sdk" -I "$core/.build/arm64-apple-macosx/debug/Modules" "$app/HealthAuthorizationCoordinator.swift" "$app/KeychainStore.swift" "$app/PairingURL.swift"
printf 'typecheck exit code: %s\n' "$?"
printf '\nCommand: swiftc -typecheck -target arm64-apple-macos14.0 -sdk "$sdk" -I HealthSyncCore/.build/arm64-apple-macosx/debug/Modules AnchoredSyncCoordinator.swift HealthAuthorizationCoordinator.swift\n'
swiftc -typecheck -target arm64-apple-macos14.0 -sdk "$sdk" -I "$core/.build/arm64-apple-macosx/debug/Modules" "$app/AnchoredSyncCoordinator.swift" "$app/HealthAuthorizationCoordinator.swift"
printf 'AnchoredSyncCoordinator typecheck exit code: %s\n' "$?"
printf '\nCommand: swiftc -parse-as-library -typecheck -target arm64-apple-macos14.0 -sdk "$sdk" -I HealthSyncCore/.build/arm64-apple-macosx/debug/Modules HealthSyncHelperApp.swift plus helper sources\n'
swiftc -parse-as-library -typecheck -target arm64-apple-macos14.0 -sdk "$sdk" -I "$core/.build/arm64-apple-macosx/debug/Modules" "$app/HealthSyncHelperApp.swift" "$app/HealthAuthorizationCoordinator.swift" "$app/KeychainStore.swift" "$app/PairingURL.swift" "$app/AnchoredSyncCoordinator.swift"
printf 'HealthSyncHelperApp typecheck exit code: %s\n' "$?"
printf '\nScope: macOS 14 SDK type-check only; this is not an iOS build.\n'
