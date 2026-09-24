// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HealthSyncCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "HealthSyncCore", targets: ["HealthSyncCore"]),
        .executable(name: "InteropProbe", targets: ["InteropProbe"])
    ],
    targets: [
        .target(name: "HealthSyncCore"),
        .executableTarget(name: "InteropProbe", dependencies: ["HealthSyncCore"]),
        .testTarget(name: "HealthSyncCoreTests", dependencies: ["HealthSyncCore"], resources: [.copy("Fixtures/apple_batch.schema.json")])
    ]
)
