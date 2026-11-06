import Foundation
import MarketUSCore

/// 把 HTTP 状态码翻译为领域错误。
enum HTTPErrorMapper {
    static func validate(_ response: HTTPURLResponse) throws {
        switch response.statusCode {
        case 200 ..< 300:
            return
        case 401:
            throw MarketError.unauthorized
        case 429:
            let retryAfter = response.value(forHTTPHeaderField: "Retry-After").flatMap(TimeInterval.init)
            throw MarketError.rateLimited(retryAfter: retryAfter)
        default:
            throw MarketError.server(status: response.statusCode)
        }
    }

    static func map(_ error: Error) -> MarketError {
        if let marketError = error as? MarketError {
            return marketError
        }
        if error is DecodingError {
            return .decoding
        }
        if let urlError = error as? URLError, urlError.code == .cancelled {
            return .cancelled
        }
        return .unknown(String(describing: error))
    }
}
