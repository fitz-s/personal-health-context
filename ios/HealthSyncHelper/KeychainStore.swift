import Foundation
import Security

public final class KeychainStore {
    private let service: String
    private let account = "device_token"
    private let installationAccount = "installation_id"

    public init(service: String = "com.personalhealthcontext.healthsync") { self.service = service }

    public func token() throws -> String? { try read(account: account) }

    public func saveToken(_ token: String) throws { try write(token, account: account) }

    public func deleteToken() throws { try delete(account: account) }

    public func installationID() throws -> String {
        if let value = try read(account: installationAccount) { return value }
        let value = UUID().uuidString.lowercased()
        try write(value, account: installationAccount)
        return value
    }

    public func deleteInstallationID() throws { try delete(account: installationAccount) }

    private func read(account: String) throws -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: account,
                                    kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data, let value = String(data: data, encoding: .utf8) else {
            throw KeychainError.status(status)
        }
        return value
    }

    private func write(_ value: String, account: String) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: account]
        let attributes: [String: Any] = [kSecValueData as String: data,
                                         kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let update = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if update == errSecItemNotFound {
            var insert = query
            attributes.forEach { insert[$0.key] = $0.value }
            let status = SecItemAdd(insert as CFDictionary, nil)
            guard status == errSecSuccess else { throw KeychainError.status(status) }
        } else if update != errSecSuccess { throw KeychainError.status(update) }
    }

    private func delete(account: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: account]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError.status(status) }
    }

    public enum KeychainError: Error { case status(OSStatus) }
}
