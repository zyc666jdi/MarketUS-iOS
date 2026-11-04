import Foundation
import MarketUSCore
import MarketUSNetworking

/// 真实行情仓库：网络优先，失败时回退到磁盘缓存并标记为延迟。
public struct LiveQuoteRepository: QuoteRepository {
    private let client: any HTTPClient
    private let endpoint: MarketDataEndpoint
    private let cache: QuoteCache?

    public init(
        client: any HTTPClient,
        endpoint: MarketDataEndpoint,
        cache: QuoteCache? = nil
    ) {
        self.client = client
        self.endpoint = endpoint
        self.cache = cache
    }

    public func quotes(for symbols: [Symbol]) async throws -> [Quote] {
        do {
            let request = try endpoint.quotes(symbols: symbols)
            let (data, response) = try await client.send(request)
            try HTTPErrorMapper.validate(response)

            let payload = try JSONDecoder().decode(QuotesResponseDTO.self, from: data)
            let quotes = try payload.quotes.map(QuoteMapper.map)
            persist(quotes, for: symbols)
            return quotes
        } catch {
            if let cached = cachedQuotes(for: symbols), !cached.isEmpty {
                return cached.map { quote in
                    Quote(
                        symbol: quote.symbol,
                        price: quote.price,
                        change: quote.change,
                        changePercent: quote.changePercent,
                        timestamp: quote.timestamp,
                        isStale: true
                    )
                }
            }
            throw HTTPErrorMapper.map(error)
        }
    }

    private func persist(_ quotes: [Quote], for symbols: [Symbol]) {
        guard let cache else {
            return
        }
        // 缓存写入失败不应影响本次请求的成功结果。
        do {
            try cache.save(quotes, for: symbols)
        } catch {
            return
        }
    }

    private func cachedQuotes(for symbols: [Symbol]) -> [Quote]? {
        guard let cache else {
            return nil
        }
        // 缓存读取失败等同于未命中。
        do {
            return try cache.load(for: symbols)
        } catch {
            return nil
        }
    }
}
