import Foundation
import Darwin
import HealthSyncCore

@main
struct InteropProbe {
    static func main() async {
        do { try await run() }
        catch {
            FileHandle.standardError.write(Data("InteropProbe failed: \(error)\n".utf8))
            exit(EXIT_FAILURE)
        }
    }

    private static func run() async throws {
        guard ProcessInfo.processInfo.environment["PHCTX_INTEROP"] == "1" else {
            print("SKIP: set PHCTX_INTEROP=1 to run the synthetic ingest interoperability probe")
            return
        }
        guard CommandLine.arguments.count == 5,
              let port = Int(CommandLine.arguments[2]), (1...65535).contains(port) else {
            throw ProbeError.usage
        }

        let host = CommandLine.arguments[1]
        let reportDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("test-report", isDirectory: true).standardizedFileURL.path
        guard host == "127.0.0.1", let outboxPath = ProcessInfo.processInfo.environment["PHCTX_INTEROP_OUTBOX"],
              URL(fileURLWithPath: outboxPath).standardizedFileURL.path.hasPrefix(reportDirectory + "/") else {
            throw ProbeError.localOnly
        }
        let pin = CommandLine.arguments[3]
        let pairingCode = CommandLine.arguments[4]
        let installationID = ProcessInfo.processInfo.environment["PHCTX_INTEROP_INSTALLATION_ID"] ??
            "interop-\(UUID().uuidString.lowercased())"
        let wrongPin = try URLSessionUploader(host: host, port: port,
                                              certificateSHA256: String(repeating: "0", count: 64))
        do {
            _ = try await wrongPin.pair(PairRequest(pairingCode: pairingCode, installationID: installationID,
                                                    deviceName: "Synthetic Interop Probe"))
            throw ProbeError.wrongPinAccepted
        } catch is ProbeError {
            throw ProbeError.wrongPinAccepted
        } catch {
            print("PASS: wrong certificate pin rejected before pairing")
        }

        let uploader = try URLSessionUploader(host: host, port: port, certificateSHA256: pin)
        let pair = try await uploader.pair(PairRequest(pairingCode: pairingCode, installationID: installationID,
                                                       deviceName: "Synthetic Interop Probe"))
        guard pair.installationID == installationID else { throw ProbeError.installationMismatch }
        print("PASS: wrong pin generated no accepted pairing request; valid pairing succeeded")
        print("PASS: pairing and tolerant PairResponse decoding")

        let outboxURL = URL(fileURLWithPath: outboxPath, isDirectory: true)
        try FileManager.default.createDirectory(at: outboxURL, withIntermediateDirectories: true)
        let store = try OutboxStore(directory: outboxURL)
        let streamA = "SyntheticMetricA"
        let streamB = "SyntheticMetricB"
        let completedAt = ISO8601OffsetDateFormatter().string(from: Date(), timeZone: TimeZone(secondsFromGMT: 0)!)
        let allowedA = sample(id: UUID().uuidString, metric: streamA, name: "Synthetic Watch", value: 61)
        let blockedOura = sample(id: UUID().uuidString, metric: streamA, name: "Oura Synthetic", value: 999)
        let filteredA = filter([allowedA, blockedOura])
        guard filteredA.filtered == 1, filteredA.samples == [allowedA] else {
            throw ProbeError.restrictedFilterMismatch
        }
        let first = try await store.enqueue(installationID: installationID, stream: streamA, nextAnchor: Data([1]),
                                            queryCompletedAt: completedAt, samples: filteredA.samples,
                                            deletedIDs: [], coverage: ["interop_synthetic": .bool(true)])
        let second = try await store.enqueue(installationID: installationID, stream: streamA, nextAnchor: Data([2]),
                                             queryCompletedAt: completedAt,
                                             samples: filter([sample(id: UUID().uuidString, metric: streamA,
                                                                     name: "Synthetic Watch", value: 62)]).samples,
                                             deletedIDs: [], coverage: ["interop_synthetic": .bool(true)])
        let otherStream = try await store.enqueue(installationID: installationID, stream: streamB,
                                                  nextAnchor: Data([3]), queryCompletedAt: completedAt,
                                                  samples: filter([sample(id: UUID().uuidString, metric: streamB,
                                                                          name: "Synthetic Watch", value: 1)]).samples,
                                                  deletedIDs: [], coverage: ["interop_synthetic": .bool(true)])
        guard !first.body.contains(Data("Oura Synthetic".utf8)) else { throw ProbeError.restrictedSampleQueued }
        print("PASS: restricted-source sample removed before durable outbox")

        let engine = SyncEngine(store: store, uploader: uploader, token: { pair.deviceToken })
        await engine.drain()
        guard try await store.pendingCount() == 0 else { throw ProbeError.pendingEntriesRemain }
        for entry in [first, second, otherStream] {
            guard let acked = await store.checkpoint(for: entry.stream).lastAckedBatchID,
                  acked == (entry.stream == streamA ? second.batch.batchID : otherStream.batch.batchID) else {
                throw ProbeError.acknowledgementMissing(entry.stream)
            }
        }
        print("PASS: two ordered batches on one stream and one batch on second stream committed")

        let replay = await uploader.upload(first, token: pair.deviceToken)
        guard case .acknowledged(let replayAck) = replay,
              replayAck.batchID == first.batch.batchID, replayAck.committed else {
            throw ProbeError.replayNotAcknowledged
        }
        print("PASS: identical raw outbox body replay returns committed ACK")

        let status = try await uploader.status(token: pair.deviceToken)
        guard status.streams[streamA]?.lastSequence == 1,
              status.streams[streamB]?.lastSequence == 0 else { throw ProbeError.statusMismatch }
        print("PASS: authenticated status reports per-stream sequences")
    }

    private static func sample(id: String, metric: String, name: String, value: Double) -> HealthSample {
        HealthSample(nativeID: id, metric: metric, startAt: "2026-09-23T10:00:00-05:00",
                     endAt: "2026-09-23T10:00:01-05:00", timezone: "America/Chicago", valueNum: value,
                     unit: "count", sourceBundleID: "synthetic.test", sourceName: name)
    }

    private static func filter(_ samples: [HealthSample]) -> (samples: [HealthSample], filtered: Int) {
        var restricted = RestrictedSourceFilter()
        return (restricted.filter(samples), restricted.filteredCount)
    }
}

enum ProbeError: Error, CustomStringConvertible {
    case usage, localOnly, wrongPinAccepted, installationMismatch, restrictedFilterMismatch, restrictedSampleQueued, pendingEntriesRemain
    case acknowledgementMissing(String), replayNotAcknowledged, statusMismatch

    var description: String {
        switch self {
        case .usage: return "usage: InteropProbe <host> <port> <certificate-sha256> <pairing-code>"
        case .localOnly: return "interop probe only permits loopback host and explicit disposable outbox path"
        case .wrongPinAccepted: return "wrong certificate pin was accepted"
        case .installationMismatch: return "pairing returned a different installation id"
        case .restrictedFilterMismatch: return "restricted-source filter did not remove expected synthetic sample"
        case .restrictedSampleQueued: return "restricted source entered durable outbox"
        case .pendingEntriesRemain: return "outbox still has pending entries after drain"
        case .acknowledgementMissing(let stream): return "missing final ACK checkpoint for stream \(stream)"
        case .replayNotAcknowledged: return "identical batch replay did not return committed ACK"
        case .statusMismatch: return "server stream status differed from committed batches"
        }
    }
}
