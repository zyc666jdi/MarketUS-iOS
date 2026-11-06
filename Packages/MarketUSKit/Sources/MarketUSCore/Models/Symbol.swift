/// 美股标的。ticker 统一大写，避免大小写导致的缓存与去重问题。
public struct Symbol: Hashable, Codable, Sendable, Identifiable, CustomStringConvertible {
    public let ticker: String

    public var id: String {
        ticker
    }

    public var description: String {
        ticker
    }

    public init(ticker: String) {
        self.ticker = ticker.uppercased()
    }
}
