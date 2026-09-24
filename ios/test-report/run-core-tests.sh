#!/bin/bash
set +e
cd /Users/leofitz/personal-health-context/ios/HealthSyncCore
printf 'iOS core Swift verification\nDate: '; date '+%Y-%m-%d %H:%M:%S %Z'
printf '\nCommand: swift --version\n'
swift --version
printf '\nCommand: swift build\n'
swift build
build_code=$?
printf 'swift build exit code: %s\n' "$build_code"
printf '\nCommand: swift test\n'
swift test
test_code=$?
printf 'swift test exit code: %s\n' "$test_code"
