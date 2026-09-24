import Foundation
import CryptoKit
import Security

public struct PairRequest: Codable, Sendable {
    public let pairingCode: String
    public let installationID: String
    public let deviceName: String
    enum CodingKeys: String, CodingKey {
        case pairingCode = "pairing_code", installationID = "installation_id", deviceName = "device_name"
    }

    public init(pairingCode: String, installationID: String, deviceName: String) {
        self.pairingCode = pairingCode
        self.installationID = installationID
        self.deviceName = deviceName
    }
}

public struct PairResponse: Codable, Sendable {
    public let deviceToken: String
    public let installationID: String
    public let sourceID: String
    enum CodingKeys: String, CodingKey {
        case deviceToken = "device_token", installationID = "installation_id", sourceID = "source_id"
    }
}

public struct BatchAcknowledgement: Codable, Equatable, Sendable {
    public let batchID: String
    public let requestHash: String
    public let committed: Bool
    public let upserted: Int?
    public let deleted: Int?
    public let filteredRestricted: Int?
    public let serverTime: String?
    enum CodingKeys: String, CodingKey {
        case batchID = "batch_id", requestHash = "request_hash", committed, upserted, deleted
        case filteredRestricted = "filtered_restricted", serverTime = "server_time"
    }

    public init(batchID: String, requestHash: String, committed: Bool, upserted: Int? = nil, deleted: Int? = nil,
                filteredRestricted: Int? = nil, serverTime: String? = nil) {
        self.batchID = batchID
        self.requestHash = requestHash
        self.committed = committed
        self.upserted = upserted
        self.deleted = deleted
        self.filteredRestricted = filteredRestricted
        self.serverTime = serverTime
    }
}

public struct IngestStatus: Decodable, Sendable {
    public struct Stream: Decodable, Sendable {
        public let lastSequence: Int
        public let lastBatchID: String?
        enum CodingKeys: String, CodingKey { case lastSequence = "last_sequence", lastBatchID = "last_batch_id" }
    }
    public let serverTime: String
    public let streams: [String: Stream]
    enum CodingKeys: String, CodingKey { case serverTime = "server_time", streams }
}

public struct SequenceGap: Decodable, Sendable {
    public let expectedSequence: Int
    public let expectedPreviousBatchID: String?
    enum CodingKeys: String, CodingKey {
        case expectedSequence = "expected_sequence", expectedPreviousBatchID = "expected_previous_batch_id"
    }
}

public enum UploadResult: Sendable {
    case acknowledged(BatchAcknowledgement)
    case sequenceGap(SequenceGap)
    case unauthorized
    case conflict(String)
    case retryable
}

public protocol Uploader: Sendable {
    func pair(_ request: PairRequest) async throws -> PairResponse
    func upload(_ entry: OutboxEntry, token: String) async -> UploadResult
    func status(token: String) async throws -> IngestStatus
}

public enum UploaderError: Error, Equatable {
    case invalidConfiguration, invalidResponse, pairingRejected(Int), statusRejected(Int)
}

public final class URLSessionUploader: NSObject, Uploader, URLSessionDelegate, @unchecked Sendable {
    private let baseURL: URL
    private let pinnedSHA256: String
    private var session: URLSession!
    private let sessionConfiguration: URLSessionConfiguration
    private let encoder: JSONEncoder
    private let decoder = JSONDecoder()

    public init(host: String, port: Int, certificateSHA256: String, sessionConfiguration: URLSessionConfiguration = .ephemeral) throws {
        let pin = certificateSHA256.lowercased()
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        guard port > 0, port <= 65535, !host.isEmpty, pin.count == 64, pin.allSatisfy({ $0.isHexDigit }),
              let url = components.url else { throw UploaderError.invalidConfiguration }
        self.baseURL = url
        self.pinnedSHA256 = pin
        self.sessionConfiguration = sessionConfiguration
        self.encoder = JSONEncoder()
        self.encoder.outputFormatting = [.sortedKeys]
        super.init()
        self.session = URLSession(configuration: sessionConfiguration, delegate: self, delegateQueue: nil)
    }

    public func pair(_ request: PairRequest) async throws -> PairResponse {
        let data = try encoder.encode(request)
        let (body, response) = try await send(path: "/v1/pair", method: "POST", body: data, token: nil)
        guard let http = response as? HTTPURLResponse else { throw UploaderError.invalidResponse }
        guard http.statusCode == 200 else { throw UploaderError.pairingRejected(http.statusCode) }
        return try decoder.decode(PairResponse.self, from: body)
    }

    public func upload(_ entry: OutboxEntry, token: String) async -> UploadResult {
        do {
            let (body, response) = try await send(path: "/v1/batches", method: "POST", body: entry.body, token: token)
            guard let http = response as? HTTPURLResponse else { return .retryable }
            switch http.statusCode {
            case 200:
                guard let ack = try? decoder.decode(BatchAcknowledgement.self, from: body) else { return .retryable }
                return .acknowledged(ack)
            case 401: return .unauthorized
            case 409:
                let error = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
                if error?["error"] as? String == "sequence_gap",
                   let gap = try? decoder.decode(SequenceGap.self, from: body) { return .sequenceGap(gap) }
                return .conflict((error?["error"] as? String) ?? "http_409")
            case 413: return .conflict("http_413")
            case 422: return .conflict("http_422")
            case 503: return .retryable
            default: return .retryable
            }
        } catch { return .retryable }
    }

    public func invalidate() {
        session.invalidateAndCancel()
        session = URLSession(configuration: sessionConfiguration, delegate: self, delegateQueue: nil)
    }

    public func status(token: String) async throws -> IngestStatus {
        let (body, response) = try await send(path: "/v1/status", method: "GET", body: nil, token: token)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw UploaderError.statusRejected((response as? HTTPURLResponse)?.statusCode ?? 0)
        }
        return try decoder.decode(IngestStatus.self, from: body)
    }

    private func send(path: String, method: String, body: Data?, token: String?) async throws -> (Data, URLResponse) {
        var request = URLRequest(url: baseURL.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return try await session.data(for: request)
    }

    public func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                           completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let certificates = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let certificate = certificates.first else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let der = SecCertificateCopyData(certificate) as Data
        let digest = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
        let anchorsStatus = SecTrustSetAnchorCertificates(trust, [certificate] as CFArray)
        let anchorsOnlyStatus = SecTrustSetAnchorCertificatesOnly(trust, true)
        let policyStatus: OSStatus
        if let host = baseURL.host {
            policyStatus = SecTrustSetPolicies(trust, SecPolicyCreateSSL(true, host as CFString))
        } else {
            policyStatus = errSecParam
        }
        let trustAccepted = SecTrustEvaluateWithError(trust, nil)
        guard digest == pinnedSHA256, anchorsStatus == errSecSuccess, anchorsOnlyStatus == errSecSuccess,
              policyStatus == errSecSuccess, trustAccepted else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
