import Foundation
import MarketUSCore

/// Mock 行情场景。`seed + scenario` 唯一确定输出，可复现。
public enum MockScenario: String, Sendable, CaseIterable {
    case defaultMarket = "default"
    case bull
    case bear
    case stale
    case empty
}

/// 确定性 Mock 报价仓库：不访问网络，同一 seed 下结果完全一致。
public struct MockQuoteRepository: QuoteRepository {
    private let seed: UInt64
    private let scenario: MockScenario
    private let clock: any MarketClock

    public init(
        seed: UInt64 = 42,
        scenario: MockScenario = .defaultMarket,
        clock: any MarketClock = SystemClock()
    ) {
        self.seed = seed
        self.scenario = scenario
        self.clock = clock
    }

    public func quotes(for symbols: [Symbol]) async throws -> [Quote] {
        guard scenario != .empty else {
            return []
        }
        let now = clock.now
        return symbols.map { quote(for: $0, at: now) }
    }

    private func quote(for symbol: Symbol, at date: Date) -> Quote {
        let hash = Self.hash(symbol.ticker, seed: seed)
        let basePrice = Decimal(20 + Int(hash % 481))
        let magnitude = Decimal(Int((hash >> 8) % 201)) / 100
        let change: Decimal
        switch scenario {
        case .bull:
            change = magnitude
        case .bear:
            change = -magnitude
        case .defaultMarket, .stale, .empty:
            change = Decimal(Int((hash >> 8) % 401) - 200) / 100
        }
        let price = basePrice + change
        let percent = basePrice == 0 ? 0 : change / basePrice * 100

        return Quote(
            symbol: symbol,
            price: price,
            change: change,
            changePercent: percent,
            timestamp: date,
            isStale: scenario == .stale
        )
    }

    /// FNV-1a：稳定、无依赖、跨平台一致（不要用 `hashValue`，它每次启动都变）。
    private static func hash(_ value: String, seed: UInt64) -> UInt64 {
        var result = seed &+ 14_695_981_039_346_656_037
        for byte in value.utf8 {
            result = (result ^ UInt64(byte)) &* 1_099_511_628_211
        }
        return result
    }
}
