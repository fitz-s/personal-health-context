import Foundation

public actor SyncEngine {
    private let store: OutboxStore
    private let uploader: any Uploader
    private let token: @Sendable () async -> String?
    private var isDraining = false
    public private(set) var requiresRepair = false

    public init(store: OutboxStore, uploader: any Uploader, token: @escaping @Sendable () async -> String?) {
        self.store = store
        self.uploader = uploader
        self.token = token
    }

    public func drain(now: Date = Date()) async {
        // Reserve before the first suspension: actor reentrancy lets a second drain in during `await token()`.
        guard !isDraining, !requiresRepair else { return }
        isDraining = true
        defer { isDraining = false }
        guard let token = await token() else { return }
        guard let entries = try? await store.entries() else { return }
        for stream in Set(entries.map(\.stream)).sorted() {
            while !requiresRepair, let entry = try? await store.nextEntry(stream: stream) {
                guard entry.state == .pending else { break }
                if let at = entry.nextAttemptAt, at > now { break }
                switch await uploader.upload(entry, token: token) {
                case .acknowledged(let ack):
                    do { try await store.acknowledge(batchID: entry.batch.batchID, ack: ack) }
                    catch { return }
                    continue
                case .sequenceGap(let gap):
                    do {
                        try await store.resync(stream: stream, expectedSequence: gap.expectedSequence,
                                               expectedPreviousBatchID: gap.expectedPreviousBatchID)
                    } catch {
                        try? await store.markConflict(batchID: entry.batch.batchID, reason: "sequence_gap_unrecoverable")
                        break
                    }
                    continue
                case .unauthorized:
                    requiresRepair = true
                    return
                case .conflict(let reason):
                    try? await store.markConflict(batchID: entry.batch.batchID, reason: reason)
                    break
                case .retryable:
                    _ = try? await store.recordRetry(batchID: entry.batch.batchID, now: now)
                    break
                }
                break
            }
        }
    }
}
