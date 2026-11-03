import Foundation

/// 可注入的时间源。测试中替换为固定时间，保证断言与快照可复现。
public protocol MarketClock: Sendable {
    var now: Date { get }
}

/// 生产环境使用的系统时钟。
public struct SystemClock: MarketClock {
    public init() {}

    public var now: Date { Date() }
}
