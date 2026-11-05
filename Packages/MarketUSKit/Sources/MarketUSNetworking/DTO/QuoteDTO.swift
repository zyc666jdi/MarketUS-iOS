import Foundation

/// 行情服务返回的报价 DTO。
///
/// 金额与涨跌幅为字符串十进制，避免 JSON 浮点误差；时间戳为 RFC 3339。
public struct QuoteDTO: Decodable, Sendable, Equatable {
    public let symbol: String
    public let price: String
    public let change: String
    public let changePercent: String
    public let ts: String
    public let stale: Bool?

    public init(
        symbol: String,
        price: String,
        change: String,
        changePercent: String,
        ts: String,
        stale: Bool? = nil
    ) {
        self.symbol = symbol
        self.price = price
        self.change = change
        self.changePercent = changePercent
        self.ts = ts
        self.stale = stale
    }
}

public struct QuotesResponseDTO: Decodable, Sendable, Equatable {
    public let quotes: [QuoteDTO]

    public init(quotes: [QuoteDTO]) {
        self.quotes = quotes
    }
}
