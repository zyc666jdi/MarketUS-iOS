// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MarketUSKit",
    // macOS 仅用于在 CI/本地跑 Core 与 Data 的快速测试（swift test），
    // App 本身只构建 iOS。
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [
        .library(name: "MarketUSCore", targets: ["MarketUSCore"]),
        .library(name: "MarketUSNetworking", targets: ["MarketUSNetworking"]),
        .library(name: "MarketUSPersistence", targets: ["MarketUSPersistence"]),
        .library(name: "MarketUSData", targets: ["MarketUSData"]),
        .library(name: "MarketUSDesignSystem", targets: ["MarketUSDesignSystem"]),
        .library(name: "MarketUSObservability", targets: ["MarketUSObservability"]),
        .library(name: "FeatureWatchlist", targets: ["FeatureWatchlist"]),
    ],
    targets: [
        .target(name: "MarketUSCore"),
        .target(name: "MarketUSNetworking", dependencies: ["MarketUSCore"]),
        .target(name: "MarketUSPersistence", dependencies: ["MarketUSCore"]),
        .target(
            name: "MarketUSData",
            dependencies: ["MarketUSCore", "MarketUSNetworking", "MarketUSPersistence"]
        ),
        .target(name: "MarketUSDesignSystem"),
        .target(name: "MarketUSObservability", dependencies: ["MarketUSCore"]),
        .target(
            name: "FeatureWatchlist",
            dependencies: ["MarketUSCore", "MarketUSDesignSystem", "MarketUSObservability"]
        ),
        .testTarget(name: "MarketUSCoreTests", dependencies: ["MarketUSCore"]),
        .testTarget(
            name: "MarketUSDataTests",
            dependencies: ["MarketUSData", "MarketUSNetworking", "MarketUSPersistence"]
        ),
        .testTarget(name: "FeatureWatchlistTests", dependencies: ["FeatureWatchlist"]),
    ]
)
