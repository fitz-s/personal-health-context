import Foundation

public struct PairingDetails: Equatable, Sendable {
    public let host: String
    public let port: Int
    public let certificateSHA256: String
    public let pairingCode: String

    public static func parse(_ value: String) -> PairingDetails? {
        guard let components = URLComponents(string: value), components.scheme == "phctx", components.host == "pair" else { return nil }
        let items = Dictionary(components.queryItems?.map { ($0.name, $0.value ?? "") } ?? [], uniquingKeysWith: { first, _ in first })
        guard let host = items["host"], !host.isEmpty,
              let portString = items["port"], let port = Int(portString), (1...65535).contains(port),
              let certificate = items["cert"]?.lowercased(), certificate.count == 64, certificate.allSatisfy(\.isHexDigit),
              let code = items["code"], !code.isEmpty else { return nil }
        return PairingDetails(host: host, port: port, certificateSHA256: certificate, pairingCode: code)
    }
}
