import MarketUSCore
import MarketUSData

/// 组合根：App 是唯一知道具体实现的地方，Feature 只拿到协议。
struct AppEnvironment: Sendable {
    let quoteRepository: any QuoteRepository

    static let live = AppEnvironment(quoteRepository: MockQuoteRepository())
}
