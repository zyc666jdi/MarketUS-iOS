/// 报价仓库。Feature 层只依赖这个协议，不感知网络与存储实现。
public protocol QuoteRepository: Sendable {
    func quotes(for symbols: [Symbol]) async throws -> [Quote]
}
