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

@Test func batchAcknowledgementDecodesWithoutOptionalFields() throws {
    let ack = try JSONDecoder().decode(BatchAcknowledgement.self,
                                       from: Data(#"{"batch_id":"batch-1","request_hash":"hash","committed":true}"#.utf8))
    #expect(ack.batchID == "batch-1")
    #expect(ack.committed)
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
    #expect(try await store.checkpoint(for: first.stream).lastAckedBatchID == first.batch.batchID)
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
    #expect(try await store.checkpoint(for: first.stream).lastAckedSequence == 1)
    #expect(try await store.checkpoint(for: first.stream).lastAckedBatchID == second.batch.batchID)
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

@Test func ouraSourceNameIsPreservedInOutbox() async throws {
    let (store, directory) = try makeStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let oura = sample(bundle: "com.ouraring.oura", name: "Oura Sync")
    let entry = try await store.enqueue(installationID: "installation-test", stream: "metric",
                                        nextAnchor: Data([1]), queryCompletedAt: "2026-09-23T10:00:00-05:00",
                                        samples: [oura], deletedIDs: [])
    #expect(entry.batch.samples == [oura])
    let queued = try await store.nextEntry(stream: "metric")
    #expect(queued?.batch.samples == [oura])
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

private func sample(bundle: String, name: String, device: [String: JSONValue]? = nil,
                    metadata: [String: JSONValue]? = nil) -> HealthSample {
    HealthSample(nativeID: UUID().uuidString, metric: "metric", startAt: "2026-09-23T10:00:00-05:00",
                 endAt: "2026-09-23T10:01:00-05:00", timezone: "America/Chicago", sourceBundleID: bundle,
                 sourceName: name, device: device, metadata: metadata)
}

// MARK: - F30 outbox durability under injected file failures

enum FileOp: Sendable { case write, sync, move, remove }
private struct InjectedFault: Error {}

/// Real files, except the armed operation on a matching path fails.
private final class FaultyFiles: OutboxFiles, @unchecked Sendable {
    private let real = POSIXFiles()
    private let lock = NSLock()
    private var fault: (op: FileOp, match: @Sendable (URL) -> Bool)?

    func arm(_ op: FileOp, _ match: @escaping @Sendable (URL) -> Bool) { lock.withLock { fault = (op, match) } }
    func disarm() { lock.withLock { fault = nil } }
    private func check(_ op: FileOp, _ url: URL) throws {
        if let fault = lock.withLock({ fault }), fault.op == op, fault.match(url) { throw InjectedFault() }
    }
    func write(_ data: Data, to url: URL) throws { try check(.write, url); try real.write(data, to: url) }
    func sync(_ url: URL) throws { try check(.sync, url); try real.sync(url) }
    func move(_ from: URL, to: URL) throws { try check(.move, from); try real.move(from, to: to) }
    func remove(_ url: URL) throws { try check(.remove, url); try real.remove(url) }
}

private func isTemp(_ url: URL) -> Bool { url.pathExtension == "tmp" }
private func isIndex(_ url: URL) -> Bool { url.lastPathComponent == "index.json" }
private func isDirectory(_ url: URL) -> Bool { url.pathExtension.isEmpty }

private func faultyStore() throws -> (OutboxStore, FaultyFiles, URL) {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("health-sync-\(UUID().uuidString)")
    let files = FaultyFiles()
    return (try OutboxStore(directory: url, files: files), files, url)
}

@Test(arguments: [FileOp.write, .sync, .move])
func enqueueFailsWhenPageIsNotDurable(op: FileOp) async throws {
    let (store, files, directory) = try faultyStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    files.arm(op, isTemp)
    await #expect(throws: InjectedFault.self) { try await enqueue(store) }
    files.disarm()
    let reopened = try OutboxStore(directory: directory)
    #expect(try await reopened.entries().isEmpty)
    #expect(try await reopened.anchor(for: "HKQuantityTypeIdentifierHeartRate") == nil)
}

@Test func enqueueFailsWhenDirectoryEntryIsNotDurable() async throws {
    let (store, files, directory) = try faultyStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    files.arm(.sync, isDirectory)
    await #expect(throws: InjectedFault.self) { try await enqueue(store) }
}

@Test(arguments: [FileOp.write, .sync, .move])
func failedIndexWriteKeepsPageUnacknowledged(op: FileOp) async throws {
    let (store, files, directory) = try faultyStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let entry = try await enqueue(store)
    let ack = BatchAcknowledgement(batchID: entry.batch.batchID, requestHash: "h", committed: true)
    files.arm(op, { isTemp($0) || isIndex($0) })
    await #expect(throws: InjectedFault.self) { try await store.acknowledge(batchID: entry.batch.batchID, ack: ack) }
    // Neither memory nor disk may claim the ACK; the page is still next in this process and after reopen.
    #expect(try await store.checkpoint(for: entry.stream).lastAckedSequence == -1)
    #expect(try await store.nextEntry(stream: entry.stream)?.batch.batchID == entry.batch.batchID)
    let reopened = try OutboxStore(directory: directory)
    #expect(try await reopened.checkpoint(for: entry.stream).lastAckedSequence == -1)
    #expect(try await reopened.nextEntry(stream: entry.stream)?.batch.batchID == entry.batch.batchID)
    files.disarm()
    try await store.acknowledge(batchID: entry.batch.batchID, ack: ack)
    #expect(try await store.checkpoint(for: entry.stream).lastAckedBatchID == entry.batch.batchID)
}

@Test func failedPageRemovalAfterDurableAckIsReportedAndRecoveredOnReopen() async throws {
    let (store, files, directory) = try faultyStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let entry = try await enqueue(store)
    let ack = BatchAcknowledgement(batchID: entry.batch.batchID, requestHash: "h", committed: true)
    files.arm(.remove, { $0.lastPathComponent.hasPrefix("batch-") })
    await #expect(throws: InjectedFault.self) { try await store.acknowledge(batchID: entry.batch.batchID, ack: ack) }
    #expect(try await store.checkpoint(for: entry.stream).lastAckedBatchID == entry.batch.batchID)
    #expect(try await store.nextEntry(stream: entry.stream) == nil)
    files.disarm()
    let reopened = try OutboxStore(directory: directory)
    #expect(try await reopened.entries().isEmpty)
}

@Test func failedResyncJournalRemovalIsReported() async throws {
    let (store, files, directory) = try faultyStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let entry = try await enqueue(store)
    files.arm(.remove, { $0.lastPathComponent == "resync-journal.json" })
    await #expect(throws: InjectedFault.self) {
        try await store.resync(stream: entry.stream, expectedSequence: 0, expectedPreviousBatchID: nil)
    }
    files.disarm()
    let reopened = try OutboxStore(directory: directory)
    #expect(try await reopened.nextEntry(stream: entry.stream)?.batch.batchID == entry.batch.batchID)
}

@Test func ambiguousDirectoryPublicationPoisonsStoreUntilRecovered() async throws {
    let (store, files, directory) = try faultyStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let first = try await enqueue(store)
    // The rename succeeds (the file becomes visible) but the fsync confirming that publication does not:
    // this is the ambiguous case, distinct from a write/sync/move failure that rolls back cleanly.
    files.arm(.sync, isDirectory)
    await #expect(throws: InjectedFault.self) { try await enqueue(store, stream: "HKQuantityTypeIdentifierStepCount") }
    // While poisoned, reads must not treat the ambiguously-published entry's anchor as committed, nor
    // pretend the store is otherwise readable. Every public accessor that reads entries, anchors, or index
    // state is routed through the same check, not just the two exercised above.
    await #expect(throws: OutboxError.poisoned) { try await store.anchor(for: "HKQuantityTypeIdentifierStepCount") }
    await #expect(throws: OutboxError.poisoned) { try await store.entries() }
    await #expect(throws: OutboxError.poisoned) { try await enqueue(store, stream: "HKQuantityTypeIdentifierHeartRate") }
    await #expect(throws: OutboxError.poisoned) { try await store.nextEntry(stream: first.stream) }
    await #expect(throws: OutboxError.poisoned) { try await store.initialHistory(for: first.stream) }
    await #expect(throws: OutboxError.poisoned) { try await store.pendingCount() }
    await #expect(throws: OutboxError.poisoned) { try await store.checkpoint(for: first.stream) }
    await #expect(throws: OutboxError.poisoned) { try await store.checkpoints() }
    await #expect(throws: OutboxError.poisoned) { try await store.latestAckedAt() }
    // Once the directory fsync can be confirmed again, the poison clears and every accessor resumes
    // returning correct data, including for the entry that had been ambiguously published.
    files.disarm()
    #expect(try await store.anchor(for: first.stream) == Data([1, 2, 3]))
    #expect(try await store.entries().count == 2)
    #expect(try await store.nextEntry(stream: first.stream)?.batch.batchID == first.batch.batchID)
    #expect(try await store.initialHistory(for: first.stream).complete == false)
    #expect(try await store.pendingCount() == 2)
    #expect(try await store.checkpoint(for: first.stream).lastAckedSequence == -1)
    #expect(try await store.checkpoints().isEmpty)
    #expect(try await store.latestAckedAt() == nil)
}

@Test func unreadableIndexFailsOpenInsteadOfResettingCheckpoints() async throws {
    let (store, directory) = try makeStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let entry = try await enqueue(store)
    try await store.acknowledge(batchID: entry.batch.batchID,
                                ack: BatchAcknowledgement(batchID: entry.batch.batchID, requestHash: "h", committed: true))
    let index = directory.appendingPathComponent("index.json")
    let before = try Data(contentsOf: index)
    try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: index.path)
    #expect(throws: (any Error).self) { _ = try OutboxStore(directory: directory) }
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: index.path)
    #expect(try Data(contentsOf: index) == before)
}

// MARK: - F31 drain reentrancy

private actor TokenGate {
    private(set) var calls = 0
    private var open = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func token() async -> String? {
        calls += 1
        if !open { await withCheckedContinuation { waiters.append($0) } }
        return "token"
    }
    func release() { open = true; waiters.forEach { $0.resume() }; waiters.removeAll() }
}

@Test func simultaneousDrainsWithSuspendedTokenUploadOnce() async throws {
    let (store, directory) = try makeStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let entry = try await enqueue(store)
    let mock = MockUploader([.ack, .ack])
    let gate = TokenGate()
    let engine = SyncEngine(store: store, uploader: mock, token: { await gate.token() })
    let first = Task { await engine.drain() }
    while await gate.calls == 0 { await Task.yield() }
    let second = Task { await engine.drain() }
    try await Task.sleep(nanoseconds: 50_000_000)
    await gate.release()
    await first.value
    await second.value
    #expect(await gate.calls == 1)
    #expect(await mock.sent == [entry.batch.batchID])
}

// MARK: - F32 TLS challenge completes exactly once

private final class Space: URLProtectionSpace, @unchecked Sendable {
    private let trust: SecTrust?
    init(method: String, trust: SecTrust?) {
        self.trust = trust
        super.init(host: "127.0.0.1", port: 8443, protocol: "https", realm: nil, authenticationMethod: method)
    }
    required init?(coder: NSCoder) { nil }
    override var serverTrust: SecTrust? { trust }
}

private final class Sender: NSObject, URLAuthenticationChallengeSender {
    func use(_ credential: URLCredential, for challenge: URLAuthenticationChallenge) {}
    func continueWithoutCredential(for challenge: URLAuthenticationChallenge) {}
    func cancel(_ challenge: URLAuthenticationChallenge) {}
}

/// Synthetic self-signed P-256 leaf for 127.0.0.1 (serverAuth EKU); generated for tests only.
private let testCertificateB64 = "MIIBtDCCAVqgAwIBAgIUJ2FtpE+ErMZ4FXNKF9dp/xhVWRQwCgYIKoZIzj0EAwIwFTETMBEGA1UEAwwKcGhjdHgtdGVzdDAeFw0yNjA5MjQwNDE5NThaFw0yODEyMDIwNDE5NThaMBUxEzARBgNVBAMMCnBoY3R4LXRlc3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQG28t+OPzxIIAFnlP1xzdA9iDLztUp+QT6cCPe7NusKNACKeVt4+B4KKTja2JTjgZijr/bE66znZVAWYaNzW0So4GHMIGEMB0GA1UdDgQWBBQAzhjCMoU7SxFJuxYGCGuet0vmUjAfBgNVHSMEGDAWgBQAzhjCMoU7SxFJuxYGCGuet0vmUjAPBgNVHREECDAGhwR/AAABMBMGA1UdJQQMMAoGCCsGAQUFBwMBMA4GA1UdDwEB/wQEAwIDiDAMBgNVHRMBAf8EAjAAMAoGCCqGSM49BAMCA0gAMEUCIGqk6w5B91UspKWAKNAEmCn2v+u/Qkxxq291C5v81xwPAiEAwJadJ3dzqSPcZvlulYEbJ1LyH094I0pTFUv0xDTcaFY="
private let testCertificatePin = "84d6c58a3732959a277872c0619904db5fc4c13dc43bd3e277f3c4c50eaa78e9"

private func trust() throws -> SecTrust {
    let der = try #require(Data(base64Encoded: testCertificateB64))
    let certificate = try #require(SecCertificateCreateWithData(nil, der as CFData))
    var trust: SecTrust?
    #expect(SecTrustCreateWithCertificates(certificate, SecPolicyCreateBasicX509(), &trust) == errSecSuccess)
    return try #require(trust)
}

@Test func everyChallengePathCompletesExactlyOnce() throws {
    let cases: [(String, String, SecTrust?, URLSession.AuthChallengeDisposition)] = [
        ("accepted", testCertificatePin, try trust(), .useCredential),
        ("wrong pin", String(repeating: "0", count: 64), try trust(), .cancelAuthenticationChallenge),
        ("server trust missing", testCertificatePin, nil, .cancelAuthenticationChallenge),
        ("basic auth", testCertificatePin, try trust(), .cancelAuthenticationChallenge),
        ("client certificate", testCertificatePin, try trust(), .cancelAuthenticationChallenge)
    ]
    let methods = ["basic auth": NSURLAuthenticationMethodHTTPBasic,
                   "client certificate": NSURLAuthenticationMethodClientCertificate]
    for (name, pin, trust, expected) in cases {
        let uploader = try URLSessionUploader(host: "127.0.0.1", port: 8443, certificateSHA256: pin)
        let space = Space(method: methods[name] ?? NSURLAuthenticationMethodServerTrust, trust: trust)
        let challenge = URLAuthenticationChallenge(protectionSpace: space, proposedCredential: nil, previousFailureCount: 0,
                                                   failureResponse: nil, error: nil, sender: Sender())
        var calls: [URLSession.AuthChallengeDisposition] = []
        uploader.urlSession(URLSession.shared, didReceive: challenge) { disposition, _ in calls.append(disposition) }
        #expect(calls == [expected], "case: \(name)")
        uploader.invalidate()
    }
}

// MARK: - F33 paging after bootstrap

/// Drains `changes` pending changes through pages of at most `PageDecision.limit`; returns queries run.
private func drainPages(changes: Int, historyComplete: Bool, deletedShare: Int = 0) -> (queries: Int, remaining: Int, complete: Bool) {
    var remaining = changes, complete = historyComplete, queries = 0
    while queries < 100 {
        let page = min(remaining, PageDecision.limit)
        let deleted = min(page, deletedShare)
        remaining -= page
        queries += 1
        let decision = PageDecision(historyWasComplete: complete, samples: page - deleted, deleted: deleted)
        complete = decision.historyComplete
        if !decision.queryAgain { break }
    }
    return (queries, remaining, complete)
}

@Test(arguments: [(501, 2), (1001, 3), (500, 2), (499, 1), (0, 1)])
func fullPagesAfterBootstrapKeepQuerying(changes: Int, queries: Int) {
    let result = drainPages(changes: changes, historyComplete: true)
    #expect(result.remaining == 0)
    #expect(result.queries == queries)
    #expect(result.complete)
}

@Test func bootstrapCompletesOnlyOnShortPage() {
    #expect(PageDecision(historyWasComplete: false, samples: 500, deleted: 0) ==
            PageDecision(historyWasComplete: false, samples: 0, deleted: 500))
    let full = PageDecision(historyWasComplete: false, samples: 500, deleted: 0)
    #expect(!full.historyComplete && full.queryAgain)
    let short = PageDecision(historyWasComplete: false, samples: 499, deleted: 0)
    #expect(short.historyComplete && !short.queryAgain)
    let bootstrap = drainPages(changes: 1001, historyComplete: false)
    #expect(bootstrap.remaining == 0 && bootstrap.queries == 3 && bootstrap.complete)
}

@Test func mixedSamplesAndDeletionsFillAPage() {
    // The query limit counts samples and deleted objects together.
    let mixed = PageDecision(historyWasComplete: true, samples: 300, deleted: 200)
    #expect(mixed.queryAgain)
    #expect(drainPages(changes: 1001, historyComplete: true, deletedShare: 200).queries == 3)
}

// MARK: - F05 producer equivalence with export.xml strings

/// Synthetic export.xml rows written in the exact attribute form Apple's exporter uses.
private let exportXML = """
<HealthData locale="en_US">
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch" value="HKCategoryValueSleepAnalysisInBed" startDate="2026-09-01 22:00:00 -0500" endDate="2026-09-02 06:00:00 -0500"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch" value="HKCategoryValueSleepAnalysisAsleepUnspecified" startDate="2026-09-01 22:10:00 -0500" endDate="2026-09-01 22:20:00 -0500"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch" value="HKCategoryValueSleepAnalysisAwake" startDate="2026-09-01 22:20:00 -0500" endDate="2026-09-01 22:25:00 -0500"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch" value="HKCategoryValueSleepAnalysisAsleepCore" startDate="2026-09-01 22:25:00 -0500" endDate="2026-09-01 23:00:00 -0500"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch" value="HKCategoryValueSleepAnalysisAsleepDeep" startDate="2026-09-01 23:00:00 -0500" endDate="2026-09-01 23:40:00 -0500"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch" value="HKCategoryValueSleepAnalysisAsleepREM" startDate="2026-09-01 23:40:00 -0500" endDate="2026-09-02 00:10:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Phone" unit="count" value="12" startDate="2026-09-01 10:00:00 -0500" endDate="2026-09-01 10:01:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierActiveEnergyBurned" sourceName="Synthetic Watch" unit="kcal" value="0.5" startDate="2026-09-01 10:00:00 -0500" endDate="2026-09-01 10:01:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierAppleExerciseTime" sourceName="Synthetic Watch" unit="min" value="1" startDate="2026-09-01 10:00:00 -0500" endDate="2026-09-01 10:01:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Synthetic Watch" unit="count/min" value="61" startDate="2026-09-01 10:00:00 -0500" endDate="2026-09-01 10:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Synthetic Watch" unit="count/min" value="55" startDate="2026-09-01 00:00:00 -0500" endDate="2026-09-01 23:59:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" sourceName="Synthetic Watch" unit="ms" value="40" startDate="2026-09-01 03:00:00 -0500" endDate="2026-09-01 03:01:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierRespiratoryRate" sourceName="Synthetic Watch" unit="count/min" value="14" startDate="2026-09-01 03:00:00 -0500" endDate="2026-09-01 03:01:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="Synthetic Watch" unit="%" value="0.97" startDate="2026-09-01 03:00:00 -0500" endDate="2026-09-01 03:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierVO2Max" sourceName="Synthetic Watch" unit="mL/min·kg" value="40" startDate="2026-09-01 10:00:00 -0500" endDate="2026-09-01 10:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Synthetic Scale" unit="kg" value="70" startDate="2026-09-01 07:00:00 -0500" endDate="2026-09-01 07:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierBodyFatPercentage" sourceName="Synthetic Scale" unit="%" value="0.2" startDate="2026-09-01 07:00:00 -0500" endDate="2026-09-01 07:00:00 -0500"/>
 <Record type="HKQuantityTypeIdentifierLeanBodyMass" sourceName="Synthetic Scale" unit="kg" value="56" startDate="2026-09-01 07:00:00 -0500" endDate="2026-09-01 07:00:00 -0500"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30.5" durationUnit="min" sourceName="Synthetic Watch" startDate="2026-09-01 06:00:00 -0500" endDate="2026-09-01 06:30:30 -0500"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeTraditionalStrengthTraining" duration="45" durationUnit="min" sourceName="Synthetic Watch" startDate="2026-09-01 17:00:00 -0500" endDate="2026-09-01 17:45:00 -0500"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeHighIntensityIntervalTraining" duration="20" durationUnit="min" sourceName="Synthetic Watch" startDate="2026-09-02 17:00:00 -0500" endDate="2026-09-02 17:20:00 -0500"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeOther" duration="10" durationUnit="min" sourceName="Synthetic Watch" startDate="2026-09-03 17:00:00 -0500" endDate="2026-09-03 17:10:00 -0500"/>
</HealthData>
"""

private final class ExportRows: NSObject, XMLParserDelegate {
    var records: [[String: String]] = [], workouts: [[String: String]] = []
    func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?,
                attributes: [String: String] = [:]) {
        if name == "Record" { records.append(attributes) } else if name == "Workout" { workouts.append(attributes) }
    }
    static func parse() throws -> ExportRows {
        let rows = ExportRows(), parser = XMLParser(data: Data(exportXML.utf8))
        parser.delegate = rows
        #expect(parser.parse())
        return rows
    }
}

@Test func quantityUnitsMatchExportXML() throws {
    let rows = try ExportRows.parse().records.filter { $0["type"]!.hasPrefix("HKQuantityTypeIdentifier") }
    #expect(rows.count == Normalization.quantityUnits.count)
    for row in rows { #expect(Normalization.unit(forQuantity: row["type"]!) == row["unit"], "\(row["type"]!)") }
}

@Test func sleepCategoryWireFieldsMatchExportXML() throws {
    let values = try ExportRows.parse().records.filter { $0["type"] == Normalization.sleepType }.map { $0["value"]! }
    #expect(values.count == 6)
    // Raw ints are HKCategoryValueSleepAnalysis: inBed 0, asleepUnspecified 1, awake 2, core 3, deep 4, REM 5.
    for (raw, xml) in values.enumerated() {
        let wire = Normalization.category(type: Normalization.sleepType, value: raw)
        #expect(wire.text == xml)
        #expect(wire.num == Double(raw))
        #expect(wire.unit == "")
    }
    #expect(Normalization.category(type: Normalization.sleepType, value: 99).text == nil)
    #expect(Normalization.category(type: "HKCategoryTypeIdentifierMindfulSession", value: 0).text == nil)
}

@Test func workoutWireFieldsMatchExportXML() throws {
    // HKWorkoutActivityType raw values: running 37, traditionalStrengthTraining 50, HIIT 63, other 3000.
    let raws: [UInt] = [37, 50, 63, 3000]
    let rows = try ExportRows.parse().workouts
    #expect(rows.count == raws.count)
    for (raw, row) in zip(raws, rows) {
        let seconds = Double(row["duration"]!)! * (row["durationUnit"] == "min" ? 60 : 1)
        let wire = Normalization.workout(activity: raw, duration: seconds)
        #expect(wire.text == row["workoutActivityType"])
        #expect(wire.num == seconds)
        #expect(wire.unit == "s")
    }
    // An unknown raw value is not the known Other category: it preserves the raw value instead, so it can
    // never equal an export.xml row's `workoutActivityType` and false-match during equivalence hashing.
    #expect(Normalization.workoutActivity(81) == "HKWorkoutActivityTypeUnknown(81)")
    #expect(Normalization.workoutActivity(9_999) == "HKWorkoutActivityTypeUnknown(9999)")
}

#if canImport(HealthKit)
import HealthKit

/// Cross-check the platform-independent tables against the SDK's own HealthKit constants.
@Test func tablesMatchHealthKitSDK() {
    let sleep: [(HKCategoryValueSleepAnalysis, String)] = [
        (.inBed, "InBed"), (.asleepUnspecified, "AsleepUnspecified"), (.awake, "Awake"),
        (.asleepCore, "AsleepCore"), (.asleepDeep, "AsleepDeep"), (.asleepREM, "AsleepREM")
    ]
    for (value, name) in sleep {
        #expect(Normalization.category(type: HKCategoryTypeIdentifier.sleepAnalysis.rawValue, value: value.rawValue).text ==
                "HKCategoryValueSleepAnalysis" + name)
    }
    let workouts: [(HKWorkoutActivityType, String)] = [
        (.running, "Running"), (.walking, "Walking"), (.cycling, "Cycling"), (.swimming, "Swimming"),
        (.hiking, "Hiking"), (.yoga, "Yoga"), (.traditionalStrengthTraining, "TraditionalStrengthTraining"),
        (.functionalStrengthTraining, "FunctionalStrengthTraining"), (.coreTraining, "CoreTraining"),
        (.highIntensityIntervalTraining, "HighIntensityIntervalTraining"), (.elliptical, "Elliptical"),
        (.rowing, "Rowing"), (.pilates, "Pilates"), (.mixedCardio, "MixedCardio"), (.cooldown, "Cooldown"),
        (.pickleball, "Pickleball"), (.underwaterDiving, "UnderwaterDiving"), (.other, "Other")
    ]
    for (type, name) in workouts { #expect(Normalization.workoutActivity(type.rawValue) == "HKWorkoutActivityType" + name) }
    #expect(Normalization.workoutType == HKObjectType.workoutType().identifier)
    let units: [String: HKUnit] = [
        "count": .count(), "kcal": .kilocalorie(), "min": .minute(), "count/min": .count().unitDivided(by: .minute()),
        "ms": .secondUnit(with: .milli), "%": .percent(), "kg": .gramUnit(with: .kilo),
        "mL/min·kg": .literUnit(with: .milli).unitDivided(by: .gramUnit(with: .kilo).unitMultiplied(by: .minute()))
    ]
    for (type, unit) in Normalization.quantityUnits {
        #expect(HKQuantityType(HKQuantityTypeIdentifier(rawValue: type)).is(compatibleWith: HKUnit(from: unit)), "\(type)")
        #expect(units[unit]?.unitString == unit, "\(unit)")
    }
}
#endif

@Test func pendingCompletingPageEndsInitialHistoryBeforeAck() async throws {
    let (store, directory) = try makeStore()
    defer { try? FileManager.default.removeItem(at: directory) }
    let stream = "HKQuantityTypeIdentifierHeartRate"
    let coverage: [String: JSONValue] = ["initial_history_start_epoch": .number(1_000)]
    _ = try await store.enqueue(installationID: "i", stream: stream, nextAnchor: Data([1]), queryCompletedAt: "2026-09-23T10:00:00-05:00",
                                samples: [], deletedIDs: [], coverage: coverage.merging(["initial_history_complete": .bool(false)]) { $1 })
    #expect(try await store.initialHistory(for: stream).complete == false)
    _ = try await store.enqueue(installationID: "i", stream: stream, nextAnchor: Data([2]), queryCompletedAt: "2026-09-23T10:00:00-05:00",
                                samples: [], deletedIDs: [], coverage: coverage.merging(["initial_history_complete": .bool(true)]) { $1 })
    let history = try await store.initialHistory(for: stream)
    #expect(history.complete)
    #expect(history.startEpoch == 1_000)
}
