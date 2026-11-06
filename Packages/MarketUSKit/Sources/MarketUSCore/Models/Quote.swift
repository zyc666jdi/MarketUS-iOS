import Foundation

/// 一只标的的最新报价快照。
///
/// 金额使用 `Decimal`，避免 `Double` 在累加与比较时产生精度误差。
/// 展示层通过 `priceText` / `changeText` 统一格式化，避免各 Feature 重复实现。
public struct Quote: Hashable, Codable, Sendable, Identifiable {
    public let symbol: Symbol
    public let price: Decimal
    public let change: Decimal
    public let changePercent: Decimal
    public let timestamp: Date
    public let isStale: Bool

    public var id: String {
        symbol.ticker
    }

    public var isPositive: Bool {
        change >= 0
    }

    public init(
        symbol: Symbol,
        price: Decimal,
        change: Decimal,
        changePercent: Decimal,
        timestamp: Date,
        isStale: Bool = false
    ) {
        self.symbol = symbol
        self.price = price
        self.change = change
        self.changePercent = changePercent
        self.timestamp = timestamp
        self.isStale = isStale
    }

    public var priceText: String {
        Self.format(price)
    }

    public var changeText: String {
        let sign = change > 0 ? "+" : ""
        return "\(sign)\(Self.format(change)) (\(sign)\(Self.format(changePercent))%)"
    }

    private static func format(_ value: Decimal) -> String {
        String(format: "%.2f", NSDecimalNumber(decimal: value).doubleValue)
    }
}
