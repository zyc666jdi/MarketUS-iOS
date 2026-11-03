import Foundation

/// 领域错误。Data 层负责把网络/存储错误翻译成这里定义的 case，
/// Feature 层只做展示映射，不直接处理 `URLError` 或 `DecodingError`。
public enum MarketError: Error, Equatable, Sendable {
    case offline
    case unauthorized
    case rateLimited(retryAfter: TimeInterval?)
    case server(status: Int)
    case decoding
    case cancelled
    case unknown(String)
}
