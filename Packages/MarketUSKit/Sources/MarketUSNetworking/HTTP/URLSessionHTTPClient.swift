import Foundation
import MarketUSCore

/// 单次 HTTP 请求的描述。刻意保持最小，避免把 URLSession 细节泄漏到调用方。
public struct HTTPRequest: Sendable {
    public let method: String
    public let url: URL
    public let headers: [String: String]
    public let body: Data?

    public init(method: String = "GET", url: URL, headers: [String: String] = [:], body: Data? = nil) {
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body
    }
}

public protocol HTTPClient: Sendable {
    func send(_ request: HTTPRequest) async throws -> (Data, HTTPURLResponse)
}

/// 基于 URLSession 的实现。错误统一翻译为 `MarketError`。
public struct URLSessionHTTPClient: HTTPClient, @unchecked Sendable {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func send(_ request: HTTPRequest) async throws -> (Data, HTTPURLResponse) {
        var urlRequest = URLRequest(url: request.url)
        urlRequest.httpMethod = request.method
        urlRequest.httpBody = request.body
        for (field, value) in request.headers {
            urlRequest.setValue(value, forHTTPHeaderField: field)
        }

        do {
            let (data, response) = try await session.data(for: urlRequest)
            guard let http = response as? HTTPURLResponse else {
                throw MarketError.unknown("non-http response")
            }
            return (data, http)
        } catch is CancellationError {
            throw MarketError.cancelled
        } catch let error as URLError {
            throw Self.marketError(for: error)
        }
    }

    private static func marketError(for error: URLError) -> MarketError {
        switch error.code {
        case .cancelled:
            return .cancelled
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed, .internationalRoamingOff:
            return .offline
        default:
            return .unknown("transport: \(error.code.rawValue)")
        }
    }
}
