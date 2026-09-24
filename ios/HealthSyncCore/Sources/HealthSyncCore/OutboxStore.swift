import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

public enum OutboxState: String, Codable, Sendable { case pending, acked, conflict }

public struct OutboxEntry: Codable, Sendable {
    public var batch: BatchModels
    public var body: Data
    public var stream: String { batch.stream }
    public var sequence: Int { batch.sequence }
    public var previousBatchID: String? { batch.previousBatchID }
    public let nextAnchor: Data
    public var retryCount: Int
    public var nextAttemptAt: Date?
    public var state: OutboxState
    public var conflictReason: String?

    public init(batch: BatchModels, body: Data, nextAnchor: Data, retryCount: Int = 0,
                nextAttemptAt: Date? = nil, state: OutboxState = .pending, conflictReason: String? = nil) {
        self.batch = batch
        self.body = body
        self.nextAnchor = nextAnchor
        self.retryCount = retryCount
        self.nextAttemptAt = nextAttemptAt
        self.state = state
        self.conflictReason = conflictReason
    }
}

public struct StreamCheckpoint: Codable, Sendable {
    public var lastAckedSequence: Int
    public var lastAckedBatchID: String?
    public var committedAnchorB64: String?
    public var lastAckedAt: String?
    public var initialHistoryComplete: Bool
    public var initialHistoryStartEpoch: Double?

    public init(lastAckedSequence: Int = -1, lastAckedBatchID: String? = nil, committedAnchorB64: String? = nil,
                lastAckedAt: String? = nil, initialHistoryComplete: Bool = false, initialHistoryStartEpoch: Double? = nil) {
        self.lastAckedSequence = lastAckedSequence
        self.lastAckedBatchID = lastAckedBatchID
        self.committedAnchorB64 = committedAnchorB64
        self.lastAckedAt = lastAckedAt
        self.initialHistoryComplete = initialHistoryComplete
        self.initialHistoryStartEpoch = initialHistoryStartEpoch
    }
}

private struct OutboxIndex: Codable { var streams: [String: StreamCheckpoint] = [:] }
private struct OutboxResyncJournal: Codable { let entries: [OutboxEntry] }

public enum OutboxError: Error, Equatable {
    case missingEntry(String), invalidAck, sequenceGapCannotResync, conflictingBatchID, outboxLimitReached
}

public actor OutboxStore {
    private let directory: URL
    private let encoder: JSONEncoder
    private let decoder = JSONDecoder()
    private var index: OutboxIndex

    public init(directory: URL) throws {
        self.directory = directory
        encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        decoder.dateDecodingStrategy = .iso8601
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        #if os(iOS)
        try Self.protect(directory)
        #endif
        let indexURL = directory.appendingPathComponent("index.json")
        if let data = try? Data(contentsOf: indexURL) { index = try decoder.decode(OutboxIndex.self, from: data) }
        else {
            index = OutboxIndex()
            try Self.atomicWrite(try encoder.encode(index), to: indexURL)
            #if os(iOS)
            try Self.protect(indexURL)
            #endif
        }
        try Self.recoverResyncJournal(in: directory, encoder: encoder, decoder: decoder)
        try Self.recoverAcknowledgedEntries(in: directory, index: index, decoder: decoder)
    }

    public func checkpoints() -> [String: StreamCheckpoint] { index.streams }
    public func latestAckedAt() -> String? { index.streams.values.compactMap(\.lastAckedAt).max() }

    public func initialHistory(for stream: String) -> (complete: Bool, startEpoch: Double?) {
        let checkpoint = index.streams[stream] ?? StreamCheckpoint()
        if checkpoint.initialHistoryComplete { return (true, checkpoint.initialHistoryStartEpoch) }
        if let first = (try? allEntries(stream: stream))?.filter({ $0.state != .acked }).min(by: { $0.sequence < $1.sequence }) {
            if case .number(let epoch)? = first.batch.coverage["initial_history_start_epoch"] { return (false, epoch) }
            if case .number(let days)? = first.batch.coverage["initial_history_days"] {
                return (false, Date().addingTimeInterval(-days * 86_400).timeIntervalSince1970)
            }
        }
        return (false, checkpoint.initialHistoryStartEpoch)
    }

    public func enqueue(installationID: String, stream: String, nextAnchor: Data, queryCompletedAt: String,
                        samples: [HealthSample], deletedIDs: [String], coverage: [String: JSONValue] = [:]) throws -> OutboxEntry {
        let pending = try allEntries(stream: stream).filter { $0.state != .acked }
        guard !pending.contains(where: { $0.state == .conflict }) else { throw OutboxError.sequenceGapCannotResync }
        guard pending.count < 20, samples.count <= 5_000, deletedIDs.count <= 5_000 else { throw OutboxError.outboxLimitReached }
        let sampleData = try encoder.encode(samples)
        guard sampleData.count + nextAnchor.count < 6 * 1024 * 1024 else { throw OutboxError.outboxLimitReached }
        let checkpoint = index.streams[stream] ?? StreamCheckpoint()
        let latest = pending.max(by: { $0.sequence < $1.sequence })
        let sequence = max(checkpoint.lastAckedSequence + 1, (latest?.sequence ?? checkpoint.lastAckedSequence) + 1)
        let previousID = latest?.batch.batchID ?? checkpoint.lastAckedBatchID
        let batch = BatchModels(installationID: installationID, stream: stream, batchID: UUID().uuidString.lowercased(),
                                sequence: sequence, previousBatchID: previousID, nextAnchorB64: nextAnchor.base64EncodedString(),
                                queryCompletedAt: queryCompletedAt, samples: samples, deletedIDs: deletedIDs, coverage: coverage)
        let body = try encoder.encode(batch)
        guard body.count <= 8 * 1024 * 1024 else { throw OutboxError.outboxLimitReached }
        let entry = OutboxEntry(batch: batch, body: body, nextAnchor: nextAnchor)
        let url = entryURL(batchID: batch.batchID)
        guard !FileManager.default.fileExists(atPath: url.path) else { throw OutboxError.conflictingBatchID }
        // The payload and corresponding anchor are durable in the same outbox record.
        try Self.atomicWrite(try encoder.encode(entry), to: url)
        #if os(iOS)
        try Self.protect(url)
        #endif
        return entry
    }

    public func entries(stream: String? = nil) throws -> [OutboxEntry] {
        try allEntries(stream: stream).sorted { $0.stream == $1.stream ? $0.sequence < $1.sequence : $0.stream < $1.stream }
    }

    public func nextEntry(stream: String) throws -> OutboxEntry? {
        let checkpoint = index.streams[stream] ?? StreamCheckpoint()
        return try allEntries(stream: stream).filter { $0.sequence > checkpoint.lastAckedSequence }
            .sorted { $0.sequence < $1.sequence }.first
    }

    public func anchor(for stream: String) throws -> Data? {
        if let latest = try allEntries(stream: stream).filter({ $0.state != .acked }).max(by: { $0.sequence < $1.sequence }) {
            return latest.nextAnchor
        }
        guard let value = index.streams[stream]?.committedAnchorB64 else { return nil }
        return Data(base64Encoded: value)
    }

    public func pendingCount() throws -> Int { try allEntries(stream: nil).filter { $0.state != .acked }.count }

    public func recordRetry(batchID: String, now: Date = Date()) throws -> TimeInterval {
        var entry = try load(batchID: batchID)
        entry.retryCount += 1
        let delay = RetryPolicy.delay(retryCount: entry.retryCount)
        entry.nextAttemptAt = now.addingTimeInterval(delay)
        try save(entry)
        return delay
    }

    public func markConflict(batchID: String, reason: String) throws {
        var entry = try load(batchID: batchID)
        entry.state = .conflict
        entry.conflictReason = reason
        try save(entry)
    }

    public func acknowledge(batchID: String, ack: BatchAcknowledgement) throws {
        guard ack.batchID == batchID, ack.committed else { throw OutboxError.invalidAck }
        if index.streams.values.contains(where: { $0.lastAckedBatchID == batchID }) { return }
        let entry = try load(batchID: batchID)
        let current = index.streams[entry.stream] ?? StreamCheckpoint()
        guard entry.sequence == current.lastAckedSequence + 1, entry.previousBatchID == current.lastAckedBatchID else {
            throw OutboxError.invalidAck
        }
        var checkpoint = current
        checkpoint.lastAckedSequence = entry.sequence
        checkpoint.lastAckedBatchID = entry.batch.batchID
        checkpoint.committedAnchorB64 = entry.nextAnchor.base64EncodedString()
        checkpoint.lastAckedAt = entry.batch.queryCompletedAt
        if case .bool(true)? = entry.batch.coverage["initial_history_complete"] { checkpoint.initialHistoryComplete = true }
        if case .number(let value)? = entry.batch.coverage["initial_history_start_epoch"] { checkpoint.initialHistoryStartEpoch = value }
        index.streams[entry.stream] = checkpoint
        try persistIndex()
        try FileManager.default.removeItem(at: entryURL(batchID: batchID))
        syncDirectory()
    }

    public func resync(stream: String, expectedSequence: Int, expectedPreviousBatchID: String?) throws {
        guard expectedSequence >= 0 else { throw OutboxError.sequenceGapCannotResync }
        let entries = try allEntries(stream: stream).sorted { $0.sequence < $1.sequence }
        let checkpoint = index.streams[stream] ?? StreamCheckpoint()
        guard expectedSequence == checkpoint.lastAckedSequence + 1,
              expectedPreviousBatchID == checkpoint.lastAckedBatchID else { throw OutboxError.sequenceGapCannotResync }
        guard let first = entries.first, expectedSequence == first.sequence else {
            throw OutboxError.sequenceGapCannotResync
        }
        let journalURL = directory.appendingPathComponent("resync-journal.json")
        try Self.atomicWrite(try encoder.encode(OutboxResyncJournal(entries: entries)), to: journalURL)
        #if os(iOS)
        try Self.protect(journalURL)
        #endif
        for (offset, var entry) in entries.enumerated() {
            entry.batch.sequence = expectedSequence + offset
            entry.batch.previousBatchID = offset == 0 ? expectedPreviousBatchID : entries[offset - 1].batch.batchID
            entry.body = try encoder.encode(entry.batch)
            entry.state = .pending
            entry.conflictReason = nil
            entry.nextAttemptAt = nil
            try save(entry)
        }
        try FileManager.default.removeItem(at: journalURL)
        syncDirectory()
    }

    public func checkpoint(for stream: String) -> StreamCheckpoint { index.streams[stream] ?? StreamCheckpoint() }

    private func load(batchID: String) throws -> OutboxEntry {
        let url = entryURL(batchID: batchID)
        guard let data = try? Data(contentsOf: url) else { throw OutboxError.missingEntry(batchID) }
        return try decoder.decode(OutboxEntry.self, from: data)
    }

    private func save(_ entry: OutboxEntry) throws {
        let url = entryURL(batchID: entry.batch.batchID)
        try Self.atomicWrite(try encoder.encode(entry), to: url)
        #if os(iOS)
        try Self.protect(url)
        #endif
    }

    private func allEntries(stream: String?) throws -> [OutboxEntry] {
        try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasPrefix("batch-") && $0.pathExtension == "json" }
            .map { try decoder.decode(OutboxEntry.self, from: Data(contentsOf: $0)) }
            .filter { stream == nil || $0.stream == stream }
    }

    private func entryURL(batchID: String) -> URL { directory.appendingPathComponent("batch-\(batchID).json") }

    private func persistIndex() throws {
        let url = directory.appendingPathComponent("index.json")
        try Self.atomicWrite(try encoder.encode(index), to: url)
        #if os(iOS)
        try Self.protect(url)
        #endif
    }

    private func syncDirectory() { Self.syncDirectory(directory) }

    private static func recoverResyncJournal(in directory: URL, encoder: JSONEncoder, decoder: JSONDecoder) throws {
        let url = directory.appendingPathComponent("resync-journal.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        let journal = try decoder.decode(OutboxResyncJournal.self, from: Data(contentsOf: url))
        for entry in journal.entries {
            try atomicWrite(try encoder.encode(entry), to: directory.appendingPathComponent("batch-\(entry.batch.batchID).json"))
        }
        try FileManager.default.removeItem(at: url)
        syncDirectory(directory)
    }

    private static func recoverAcknowledgedEntries(in directory: URL, index: OutboxIndex, decoder: JSONDecoder) throws {
        for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            where file.lastPathComponent.hasPrefix("batch-") && file.pathExtension == "json" {
            let entry = try decoder.decode(OutboxEntry.self, from: Data(contentsOf: file))
            guard let checkpoint = index.streams[entry.stream],
                  entry.sequence < checkpoint.lastAckedSequence ||
                    (entry.sequence == checkpoint.lastAckedSequence && entry.batch.batchID == checkpoint.lastAckedBatchID) else { continue }
            try FileManager.default.removeItem(at: file)
        }
        syncDirectory(directory)
    }

    private static func syncDirectory(_ directory: URL) {
        #if canImport(Darwin) || canImport(Glibc)
        let fd = open(directory.path, O_RDONLY)
        if fd >= 0 { _ = fsync(fd); _ = close(fd) }
        #endif
    }

    private static func atomicWrite(_ data: Data, to destination: URL) throws {
        let temporary = destination.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString).tmp")
        try data.write(to: temporary)
        #if canImport(Darwin) || canImport(Glibc)
        let fd = open(temporary.path, O_RDONLY)
        if fd >= 0 { _ = fsync(fd); _ = close(fd) }
        #endif
        guard rename(temporary.path, destination.path) == 0 else {
            try? FileManager.default.removeItem(at: temporary)
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
        }
        syncDirectory(destination.deletingLastPathComponent())
    }

    #if os(iOS)
    private static func protect(_ url: URL) throws {
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                                              ofItemAtPath: url.path)
    }
    #endif
}

public enum RetryPolicy {
    public static let baseDelay: TimeInterval = 2
    public static let maximumDelay: TimeInterval = 15 * 60
    public static func delay(retryCount: Int) -> TimeInterval {
        guard retryCount > 0 else { return 0 }
        return min(baseDelay * pow(2, Double(min(retryCount - 1, 20))), maximumDelay)
    }
}
