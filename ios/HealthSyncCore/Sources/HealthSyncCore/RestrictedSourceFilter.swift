import Foundation

/// The one provenance policy (contracts/normalization.md): a sample is restricted if its source name, bundle id,
/// any device field, or any metadata key or value contains "oura", case-insensitively. Applied before the outbox.
public struct RestrictedSourceFilter: Sendable {
    public private(set) var filteredCount = 0

    public init() {}

    public mutating func filter(_ samples: [HealthSample]) -> [HealthSample] {
        samples.filter { sample in
            let blocked = Self.isRestricted(sample)
            if blocked { filteredCount += 1 }
            return !blocked
        }
    }

    public static func isRestricted(_ sample: HealthSample) -> Bool {
        marked(sample.sourceName) || marked(sample.sourceBundleID) ||
            marked(.object(sample.device ?? [:])) || marked(.object(sample.metadata ?? [:]))
    }

    private static func marked(_ text: String) -> Bool { text.range(of: "oura", options: .caseInsensitive) != nil }

    private static func marked(_ value: JSONValue) -> Bool {
        switch value {
        case .string(let v): return marked(v)
        case .object(let v): return v.contains { marked($0.key) || marked($0.value) }
        case .array(let v): return v.contains(where: marked)
        case .number, .bool, .null: return false
        }
    }
}
