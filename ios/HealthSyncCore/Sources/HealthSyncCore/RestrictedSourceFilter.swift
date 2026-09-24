import Foundation

public struct RestrictedSourceFilter: Sendable {
    public private(set) var filteredCount = 0

    public init() {}

    public mutating func filter(_ samples: [HealthSample]) -> [HealthSample] {
        samples.filter { sample in
            let blocked = sample.sourceBundleID.localizedCaseInsensitiveContains("ouraring") ||
                sample.sourceName.localizedCaseInsensitiveContains("oura")
            if blocked { filteredCount += 1 }
            return !blocked
        }
    }
}
