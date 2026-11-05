import Foundation
import MarketUSCore
import MarketUSData
import MarketUSNetworking
import MarketUSPersistence

/// 组合根：App 是唯一知道具体实现的地方，Feature 只拿到协议。
struct AppEnvironment: Sendable {
    let quoteRepository: any QuoteRepository

    static let live = make()

    /// 默认使用确定性 Mock；设置 `MARKET_BASE_URL` 时切换到真实 HTTP 仓库
    /// （本地 Mock Server 或 Alpaca 适配器）。
    private static func make() -> AppEnvironment {
        guard let rawBaseURL = ProcessInfo.processInfo.environment["MARKET_BASE_URL"],
              let baseURL = URL(string: rawBaseURL) else {
            return AppEnvironment(quoteRepository: MockQuoteRepository())
        }

        let directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let store = JSONFileStore(directory: directory.appendingPathComponent("MarketUS", isDirectory: true))
        let repository = LiveQuoteRepository(
            client: URLSessionHTTPClient(),
            endpoint: MarketDataEndpoint(baseURL: baseURL),
            cache: QuoteCache(store: store)
        )
        return AppEnvironment(quoteRepository: repository)
    }
}
