import Foundation
import MarketUSCore
import MarketUSPersistence

/// 报价磁盘缓存。按 symbol 组合生成 key，命中时用于离线/弱网回退。
public struct QuoteCache: Sendable {
    private let store: any FileStore

    public init(store: any FileStore) {
        self.store = store
    }

    public func save(_ quotes: [Quote], for symbols: [Symbol]) throws {
        try store.write(quotes, to: key(for: symbols))
    }

    public func load(for symbols: [Symbol]) throws -> [Quote]? {
        try store.read([Quote].self, from: key(for: symbols))
    }

    private func key(for symbols: [Symbol]) -> String {
        "quotes-" + symbols.map(\.ticker).joined(separator: "-")
    }
}
