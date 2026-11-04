import Foundation
import MarketUSCore

/// 行情服务的端点构造。只负责拼 URL，不关心如何发送。
public struct MarketDataEndpoint: Sendable {
    public let baseURL: URL

    public init(baseURL: URL) {
        self.baseURL = baseURL
    }

    public func quotes(symbols: [Symbol]) throws -> HTTPRequest {
        guard var components = URLComponents(
            url: baseURL.appendingPathComponent("v1/quotes", isDirectory: false),
            resolvingAgainstBaseURL: false
        ) else {
            throw MarketError.unknown("invalid base url")
        }
        components.queryItems = [
            URLQueryItem(name: "symbols", value: symbols.map(\.ticker).joined(separator: ",")),
        ]
        guard let url = components.url else {
            throw MarketError.unknown("invalid quotes url")
        }
        return HTTPRequest(url: url)
    }
}
