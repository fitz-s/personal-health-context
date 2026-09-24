import Foundation

public struct HealthSample: Codable, Equatable, Sendable {
    public let nativeID: String
    public let metric: String
    public let startAt: String
    public let endAt: String
    public let timezone: String
    public let valueNum: Double?
    public let valueText: String?
    public let unit: String?
    public let sourceBundleID: String
    public let sourceName: String
    public let device: [String: JSONValue]?
    public let metadata: [String: JSONValue]?

    public init(nativeID: String, metric: String, startAt: String, endAt: String, timezone: String,
                valueNum: Double? = nil, valueText: String? = nil, unit: String? = nil,
                sourceBundleID: String, sourceName: String, device: [String: JSONValue]? = nil,
                metadata: [String: JSONValue]? = nil) {
        self.nativeID = nativeID
        self.metric = metric
        self.startAt = startAt
        self.endAt = endAt
        self.timezone = timezone
        self.valueNum = valueNum
        self.valueText = valueText
        self.unit = unit
        self.sourceBundleID = sourceBundleID
        self.sourceName = sourceName
        self.device = device
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case nativeID = "native_id", metric, startAt = "start_at", endAt = "end_at", timezone
        case valueNum = "value_num", valueText = "value_text", unit
        case sourceBundleID = "source_bundle_id", sourceName = "source_name", device, metadata
    }
}

public enum JSONValue: Codable, Equatable, Sendable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null

    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let bool = try? value.decode(Bool.self) { self = .bool(bool) }
        else if let number = try? value.decode(Double.self) { self = .number(number) }
        else if let string = try? value.decode(String.self) { self = .string(string) }
        else if let object = try? value.decode([String: JSONValue].self) { self = .object(object) }
        else { self = .array(try value.decode([JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .string(let v): try value.encode(v)
        case .number(let v): try value.encode(v)
        case .bool(let v): try value.encode(v)
        case .object(let v): try value.encode(v)
        case .array(let v): try value.encode(v)
        case .null: try value.encodeNil()
        }
    }
}

public struct BatchModels: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let installationID: String
    public let stream: String
    public let batchID: String
    public var sequence: Int
    public var previousBatchID: String?
    public let nextAnchorB64: String
    public let queryCompletedAt: String
    public let samples: [HealthSample]
    public let deletedIDs: [String]
    public let coverage: [String: JSONValue]

    public init(protocolVersion: Int = 1, installationID: String, stream: String, batchID: String,
                sequence: Int, previousBatchID: String?, nextAnchorB64: String, queryCompletedAt: String,
                samples: [HealthSample], deletedIDs: [String], coverage: [String: JSONValue] = [:]) {
        self.protocolVersion = protocolVersion
        self.installationID = installationID
        self.stream = stream
        self.batchID = batchID
        self.sequence = sequence
        self.previousBatchID = previousBatchID
        self.nextAnchorB64 = nextAnchorB64
        self.queryCompletedAt = queryCompletedAt
        self.samples = samples
        self.deletedIDs = deletedIDs
        self.coverage = coverage
    }

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version", installationID = "installation_id", stream, batchID = "batch_id"
        case sequence, previousBatchID = "previous_batch_id", nextAnchorB64 = "next_anchor_b64"
        case queryCompletedAt = "query_completed_at", samples, deletedIDs = "deleted_ids", coverage
    }

    public func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(protocolVersion, forKey: .protocolVersion)
        try values.encode(installationID, forKey: .installationID)
        try values.encode(stream, forKey: .stream)
        try values.encode(batchID, forKey: .batchID)
        try values.encode(sequence, forKey: .sequence)
        try values.encode(previousBatchID, forKey: .previousBatchID)
        try values.encode(nextAnchorB64, forKey: .nextAnchorB64)
        try values.encode(queryCompletedAt, forKey: .queryCompletedAt)
        try values.encode(samples, forKey: .samples)
        try values.encode(deletedIDs, forKey: .deletedIDs)
        try values.encode(coverage, forKey: .coverage)
    }
}

public struct ISO8601OffsetDateFormatter: Sendable {
    public init() {}

    public func string(from date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .iso8601)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX"
        return formatter.string(from: date)
    }
}
