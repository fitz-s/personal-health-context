import SwiftUI
import HealthKit
#if os(iOS)
import UIKit
import BackgroundTasks
#endif
import HealthSyncCore

@main
struct HealthSyncHelperApp: App {
    @StateObject private var model = SetupModel()

    var body: some Scene {
        WindowGroup { SetupView().environmentObject(model) }
    }
}

@MainActor
final class SetupModel: ObservableObject {
    @Published var host = ""
    @Published var port = "47821"
    @Published var certificate = ""
    @Published var pairingCode = ""
    @Published var pairingURL = ""
    @Published var statusText = "Not paired / 尚未配对"
    @Published var lastSync = "—"
    @Published var pendingCount = 0
    @Published var errorText: String?
    @Published var paired = false

    private let keychain = KeychainStore()
    private var outbox: OutboxStore?
    private var engine: SyncEngine?
    private var syncCoordinator: AnchoredSyncCoordinator?
    private var uploader: URLSessionUploader?
    private var refreshTaskRegistered = false
    private var installationID: String?

    init() {
        do {
            try FileManager.default.createDirectory(at: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0],
                                                    withIntermediateDirectories: true)
            let id = try keychain.installationID()
            installationID = id
            let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("HealthSyncOutbox", isDirectory: true)
            let store = try OutboxStore(directory: directory)
            outbox = store
            Task { await refreshStatus() }
            if let token = try keychain.token(), let details = loadEndpoint() {
                try configure(details: details, token: token, installationID: id)
                paired = true
                statusText = "Paired / 已配对"
            }
        } catch { errorText = "Setup initialization failed / 初始化失败" }
    }

    func requestPermissions() async {
        if installationID == nil { installationID = try? keychain.installationID() }
        do { try await HealthAuthorizationCoordinator().requestReadAuthorization() }
        catch { errorText = "Health permissions unavailable / 无法获取健康权限" }
    }

    func pair() async {
        do {
            if installationID == nil { installationID = try keychain.installationID() }
            if !pairingURL.isEmpty {
                guard let details = PairingDetails.parse(pairingURL) else { throw SetupError.invalidPairingURL }
                host = details.host
                port = String(details.port)
                certificate = details.certificateSHA256
                pairingCode = details.pairingCode
            }
            guard !host.isEmpty, let portValue = Int(port), let id = installationID else { throw SetupError.invalidPairingURL }
            let client = try URLSessionUploader(host: host, port: portValue, certificateSHA256: certificate)
            let response = try await client.pair(PairRequest(pairingCode: pairingCode, installationID: id, deviceName: Self.deviceName))
            guard response.installationID == id else { throw SetupError.installationMismatch }
            try keychain.saveToken(response.deviceToken)
            let details = PairingDetails(host: host, port: portValue, certificateSHA256: certificate.lowercased(), pairingCode: "")
            try saveEndpoint(details)
            try configure(details: details, token: response.deviceToken, installationID: id)
            paired = true
            statusText = "Paired / 已配对"
            errorText = nil
            await engine?.drain()
        } catch { errorText = "Pairing failed; verify host, pin, and one-time code / 配对失败，请检查地址、证书指纹和一次性配对码" }
    }

    func refreshStatus() async {
        do {
            guard let outbox else { return }
            pendingCount = try await outbox.pendingCount()
            lastSync = await outbox.latestAckedAt() ?? "—"
            if let engine, await engine.requiresRepair {
                paired = false
                statusText = "Re-pair needed / 需要重新配对"
            } else if let uploader, let token = try keychain.token() {
                _ = try await uploader.status(token: token)
            }
        } catch {
            if let error = error as? UploaderError, case .statusRejected(401) = error {
                paired = false
                statusText = "Re-pair needed / 需要重新配对"
            } else { errorText = "Status unavailable / 状态不可用" }
        }
    }

    func disconnect() {
        do {
            try keychain.deleteToken()
            uploader?.invalidate()
            try? FileManager.default.removeItem(at: endpointURL())
            engine = nil
            syncCoordinator?.stop()
            syncCoordinator = nil
            uploader = nil
            paired = false
            statusText = "Disconnected / 已断开"
        } catch { errorText = "Could not remove token / 无法删除令牌" }
    }

    private func configure(details: PairingDetails, token: String, installationID: String) throws {
        guard let outbox else { throw SetupError.storeUnavailable }
        uploader?.invalidate()
        let client = try URLSessionUploader(host: details.host, port: details.port, certificateSHA256: details.certificateSHA256)
        uploader = client
        let keychain = self.keychain
        let syncEngine = SyncEngine(store: outbox, uploader: client, token: {
            try? keychain.token()
        })
        engine = syncEngine
        let coordinator = AnchoredSyncCoordinator(outbox: outbox, engine: syncEngine, installationID: installationID)
        syncCoordinator = coordinator
        coordinator.start()
        #if os(iOS)
        if !refreshTaskRegistered {
            coordinator.registerBackgroundRefresh()
            refreshTaskRegistered = true
        }
        coordinator.scheduleBackgroundRefresh()
        #endif
        Task { await refreshStatus() }
    }

    private func endpointURL() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("endpoint.json")
    }

    private func saveEndpoint(_ details: PairingDetails) throws {
        let data = try JSONEncoder().encode(Endpoint(host: details.host, port: details.port, certificate: details.certificateSHA256))
        try data.write(to: endpointURL(), options: .atomic)
    }

    private func loadEndpoint() -> PairingDetails? {
        guard let data = try? Data(contentsOf: endpointURL()),
              let endpoint = try? JSONDecoder().decode(Endpoint.self, from: data) else { return nil }
        return PairingDetails(host: endpoint.host, port: endpoint.port, certificateSHA256: endpoint.certificate, pairingCode: "")
    }

    private static var deviceName: String {
        #if os(iOS)
        UIDevice.current.name
        #else
        Host.current().localizedName ?? "Health Sync Helper"
        #endif
    }

    private struct Endpoint: Codable { let host: String; let port: Int; let certificate: String }
    enum SetupError: Error { case invalidPairingURL, installationMismatch, storeUnavailable }
}

struct SetupView: View {
    @EnvironmentObject private var model: SetupModel

    var body: some View {
        NavigationStack {
            Form {
                Section("Purpose / 用途") {
                    Text("Personal Health Context reads selected Apple Health data and sends it only to your paired Mac. It cannot write to Health.")
                    Text("个人健康上下文仅读取选定的 Apple 健康数据，并发送到已配对的 Mac；不会写入健康数据。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Permissions / 权限") {
                    Button("Request read access / 请求读取权限") { Task { await model.requestPermissions() } }
                }
                Section("Mac pairing / Mac 配对") {
                    TextField("Paste phctx://pair… (optional)", text: $model.pairingURL)
                    TextField("Host / 主机", text: $model.host)
                    TextField("Port / 端口", text: $model.port)
                    TextField("Certificate SHA-256", text: $model.certificate)
                    TextField("One-time pairing code / 一次性配对码", text: $model.pairingCode)
                    Button("Pair / 配对") { Task { await model.pair() } }
                    if model.paired { Button("Disconnect / 断开连接", role: .destructive) { model.disconnect() } }
                }
                Section("Sync status / 同步状态") {
                    LabeledContent("Connection / 连接", value: model.statusText)
                    LabeledContent("Last successful sync / 最近成功同步", value: model.lastSync)
                    LabeledContent("Pending batches / 待发送批次", value: "\(model.pendingCount)")
                    Button("Refresh / 刷新") { Task { await model.refreshStatus() } }
                    if let error = model.errorText { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Health Sync / 健康同步")
        }
    }
}
