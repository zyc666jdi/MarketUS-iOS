import Foundation
import MarketUSCore
import MarketUSNetworking

/// DTO → 领域模型的唯一入口。
enum QuoteMapper {
    static func map(_ dto: QuoteDTO) throws -> Quote {
        guard let price = decimal(dto.price),
              let change = decimal(dto.change),
              let changePercent = decimal(dto.changePercent),
              let timestamp = date(from: dto.ts) else {
            throw MarketError.decoding
        }
        return Quote(
            symbol: Symbol(ticker: dto.symbol),
            price: price,
            change: change,
            changePercent: changePercent,
            timestamp: timestamp,
            isStale: dto.stale ?? false
        )
    }

    private static func decimal(_ text: String) -> Decimal? {
        Decimal(string: text, locale: Locale(identifier: "en_US_POSIX"))
    }

    private static func date(from text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) {
            return date
        }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }
}
