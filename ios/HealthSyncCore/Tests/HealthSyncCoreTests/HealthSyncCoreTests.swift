import Foundation
import Testing
@testable import HealthSyncCore

private actor MockUploader: Uploader {
    enum Action: Sendable { case ack, gap(Int, String?), unauthorized, conflict, retryable }
    var actions: [Action]
    var sent: [String] = []

    init(_ actions: [Action]) { self.actions = actions }

    func pair(_ request: PairRequest) async throws -> PairResponse {
        PairResponse(deviceToken: "token", installationID: request.installationID, sourceID: "apple_health:\(request.installationID)")
    }

    func status(token: String) async throws -> IngestStatus {
        try JSONDecoder().decode(IngestStatus.self, from: Data(#"{"server_time":"2026-01-01T00:00:00Z","streams":{}}"#.utf8))
    }

    func upload(_ entry: OutboxEntry, token: String) async -> UploadResult {
        sent.append(entry.batch.batchID)
        guard !actions.isEmpty else { return .retryable }
        switch actions.removeFirst() {
        case .ack:
            return .acknowledged(BatchAcknowledgement(batchID: entry.batch.batchID, requestHash: "hash", committed: true))
        case .gap(let sequence, let previous): return .sequenceGap(SequenceGap(expectedSequence: sequence, expectedPreviousBatchID: previous))
        case .unauthorized: return .unauthorized
        case .conflict: return .conflict("batch_conflict")
        case .retryable: return .retryable
        }
    }
}

private func makeStore() throws -> (OutboxStore, URL) {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("health-sync-\(UUID().uuidString)")
    return (try OutboxStore(directory: url), url)
}

private func enqueue(_ store: OutboxStore, stream: String = "HKQuantityTypeIdentifierHeartRate") async throws -> OutboxEntry {
    try await store.enqueue(installationID: "installation-test", stream: stream, nextAnchor: Data([1, 2, 3]),
                            queryCompletedAt: "2026-09-23T10:00:00-05:00", samples: [], deletedIDs: [])
}

@Test func schemaShapeMatchesRequiredKeys() throws {
    let schemaURL = try #require(Bundle.module.url(forResource: "apple_batch.schema", withExtension: "json"))
    let schema = try JSONDecoder().decode([String: JSONValue].self, from: Data(contentsOf: schemaURL))
    let top = try #require(schema["required"])
    let required: [String]
    if case .array(let values) = top { required = values.compactMap { if case .string(let value) = $0 { value } else { nil } } }
    else { Issue.record("Schema required field list is not an array"); return }
    let batch = BatchModels(installationID: "i", stream: "s", batchID: "b", sequence: 0, previousBatchID: nil,
                            nextAnchorB64: "", queryCompletedAt: "2026-09-23T10:00:00-05:00", samples: [], deletedIDs: [])
    let object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(batch)) as? [String: Any])
    #expect(Set(object.keys) == Set(required))
    #expect(object["protocol_version"] as? Int == 1)
    #expect(object["previous_batch_id"] is NSNull)
    let sample = HealthSample(nativeID: "n", metric: "m", startAt: "2026-09-23T10:00:00-05:00",
                              endAt: "2026-09-23T10:01:00-05:00", timezone: "America/Chicago",
                              sourceBundleID: "bundle", sourceName: "source")
    let sampleObject = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(sample)) as? [String: Any])
    guard case .object(let properties)? = schema["properties"],
          case .object(let sampleProperty)? = properties["samples"],
          case .object(let item)? = sampleProperty["items"],
          case .array(let requiredValues)? = item["required"] else {
        Issue.record("Sample required field list is missing"); return
    }
    let sampleRequired = Set(requiredValues.compactMap { if case .string(let field) = $0 { field } else { nil } })
    let allowedSampleKeys: Set<String> = ["native_id", "metric", "start_at", "end_at", "timezone", "value_num", "value_text", "unit", "source_bundle_id", "source_name", "device", "metadata"]
    #expect(sampleRequired.isSubset(of: Set(sampleObject.keys)))
    #expect(Set(sampleObject.keys).isSubset(of: allowedSampleKeys))
}

@Test func queryTimeEncodingKeepsExplicitOffset() {
    let value = ISO8601OffsetDateFormatter().string(from: Date(timeIntervalSince1970: 0),
                                                   timeZone: TimeZone(secondsFromGMT: -5 * 3600)!)
    #expect(value.hasSuffix("-05:00"))
}

@Test func outboxSurvivesReopenAndStoresAnchorWithPage() async throws {
    let (store, directory) = try makeStore()
    let entry = try await enqueue(store)
    let reopened = try OutboxStore(directory: directory)
    let entries = try await reopened.entries()
    #expect(entries.count == 1)
    #expect(entries[0].batch.batchID == entry.batch.batchID)
    #expect(try await reopened.anchor(for: entry.stream) == Data([1, 2, 3]))
    try? FileManager.default.removeItem(at: directory)
}

@Test func onlyCommittedMatchingAckDeletesAndReplayIsIdempotent() async throws {
    let (store, directory) = try makeStore()
    let first = try await enqueue(store)
    let wrong = BatchAcknowledgement(batchID: "other", requestHash: "h", committed: true)
    do {
        try await store.acknowledge(batchID: first.batch.batchID, ack: wrong)
        Issue.record("Mismatched acknowledgement was accepted")
    } catch let error as OutboxError { #expect(error == .invalidAck) }
    #expect(try await store.entries().count == 1)
    let notCommitted = BatchAcknowledgement(batchID: first.batch.batchID, requestHash: "h", committed: false)
    do {
        try await store.acknowledge(batchID: first.batch.batchID, ack: notCommitted)
        Issue.record("Uncommitted acknowledgement was accepted")
    } catch let error as OutboxError { #expect(error == .invalidAck) }
    let ack = BatchAcknowledgement(batchID: first.batch.batchID, requestHash: "h", committed: true)
    try await store.acknowledge(batchID: first.batch.batchID, ack: ack)
    #expect(try await store.entries().isEmpty)
    try await store.acknowledge(batchID: first.batch.batchID, ack: ack)
    #expect(await store.checkpoint(for: first.stream).lastAckedBatchID == first.batch.batchID)
    try? FileManager.default.removeItem(at: directory)
}

@Test func sequenceGapResyncsOldestBatchAndPreservesPageIDs() async throws {
    let (store, directory) = try makeStore()
    let first = try await enqueue(store)
    let second = try await enqueue(store)
    let mock = MockUploader([.gap(0, nil), .ack, .ack])
    let engine = SyncEngine(store: store, uploader: mock, token: { "token" })
    await engine.drain()
    let entries = try await store.entries(stream: first.stream)
    #expect(entries.isEmpty)
    #expect(await mock.sent == [first.batch.batchID, first.batch.batchID, second.batch.batchID])
    #expect(await store.checkpoint(for: first.stream).lastAckedSequence == 1)
    #expect(await store.checkpoint(for: first.stream).lastAckedBatchID == second.batch.batchID)
    #expect(try await store.anchor(for: first.stream) == Data([1, 2, 3]))
    try? FileManager.default.removeItem(at: directory)
    try? FileManager.default.removeItem(at: directory)
}

@Test func unauthorizedKeepsOutboxAndStopsRepair() async throws {
    let (store, directory) = try makeStore()
    let entry = try await enqueue(store)
    let engine = SyncEngine(store: store, uploader: MockUploader([.unauthorized]), token: { "token" })
    await engine.drain()
    #expect(await engine.requiresRepair)
    #expect(try await store.nextEntry(stream: entry.stream)?.batch.batchID == entry.batch.batchID)
    try? FileManager.default.removeItem(at: directory)
}

@Test func conflictsKeepOutboxAndStopStream() async throws {
    let (store, directory) = try makeStore()
    let entry = try await enqueue(store)
    let engine = SyncEngine(store: store, uploader: MockUploader([.conflict]), token: { "token" })
    await engine.drain()
    let pending = try #require(try await store.nextEntry(stream: entry.stream))
    #expect(pending.state == .conflict)
    #expect(pending.conflictReason == "batch_conflict")
    #expect(try await store.pendingCount() == 1)
    try? FileManager.default.removeItem(at: directory)
}

@Test func restrictedSourcesAreFilteredBeforeQueueing() {
    let allowed = sample(bundle: "com.apple.health", name: "Health")
    let bundleRestricted = sample(bundle: "com.ouraring.oura", name: "Ring")
    let nameRestricted = sample(bundle: "vendor.device", name: "Oura Sync")
    var filter = RestrictedSourceFilter()
    #expect(filter.filter([allowed, bundleRestricted, nameRestricted]) == [allowed])
    #expect(filter.filteredCount == 2)
}

@Test func retryBackoffCapsAndPersistsAttemptState() async throws {
    let (store, directory) = try makeStore()
    let entry = try await enqueue(store)
    let now = Date(timeIntervalSince1970: 1_000)
    #expect(try await store.recordRetry(batchID: entry.batch.batchID, now: now) == 2)
    let retried = try #require(try await store.nextEntry(stream: entry.stream))
    #expect(retried.retryCount == 1)
    #expect(retried.nextAttemptAt == now.addingTimeInterval(2))
    #expect(RetryPolicy.delay(retryCount: 2) == 4)
    #expect(RetryPolicy.delay(retryCount: 40) == RetryPolicy.maximumDelay)
    try? FileManager.default.removeItem(at: directory)
}

@Test func streamDoesNotSendSecondBatchBeforeFirstAck() async throws {
    let (store, directory) = try makeStore()
    let first = try await enqueue(store)
    let _ = try await enqueue(store)
    let mock = MockUploader([.retryable])
    let engine = SyncEngine(store: store, uploader: mock, token: { "token" })
    await engine.drain()
    #expect(await mock.sent == [first.batch.batchID])
    try? FileManager.default.removeItem(at: directory)
}

private func sample(bundle: String, name: String) -> HealthSample {
    HealthSample(nativeID: UUID().uuidString, metric: "metric", startAt: "2026-09-23T10:00:00-05:00",
                 endAt: "2026-09-23T10:01:00-05:00", timezone: "America/Chicago", sourceBundleID: bundle,
                 sourceName: name)
}
