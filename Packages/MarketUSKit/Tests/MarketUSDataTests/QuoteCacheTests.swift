import Foundation
import XCTest
@testable import MarketUSCore
@testable import MarketUSData
@testable import MarketUSPersistence

final class QuoteCacheTests: XCTestCase {
    private let fixedDate = Date(timeIntervalSince1970: 1_700_000_000)
    private let symbols = [Symbol(ticker: "AAPL"), Symbol(ticker: "MSFT")]

    func test_save_thenLoad_returnsSameQuotes() throws {
        let (cache, _) = makeCache()
        let quotes = [
            makeQuote(ticker: "AAPL", price: 189.12),
            makeQuote(ticker: "MSFT", price: 420.55),
        ]

        try cache.save(quotes, for: symbols)

        XCTAssertEqual(try cache.load(for: symbols), quotes)
    }

    func test_save_symbolsInGivenOrder_writesOrderedKey() throws {
        let (cache, directory) = makeCache()
        try cache.save([makeQuote(ticker: "AAPL", price: 189.12)], for: symbols)

        let keyFile = directory.appendingPathComponent("quotes-AAPL-MSFT.json", isDirectory: false)
        XCTAssertTrue(FileManager.default.fileExists(atPath: keyFile.path))
    }

    func test_load_differentSymbolOrder_returnsNil() throws {
        let (cache, _) = makeCache()
        try cache.save([makeQuote(ticker: "AAPL", price: 189.12)], for: symbols)

        XCTAssertNil(try cache.load(for: Array(symbols.reversed())))
    }

    func test_save_sameSymbolOrder_overwritesPreviousQuotes() throws {
        let (cache, _) = makeCache()
        let first = [makeQuote(ticker: "AAPL", price: 100)]
        let second = [makeQuote(ticker: "AAPL", price: 200)]

        try cache.save(first, for: symbols)
        try cache.save(second, for: symbols)

        XCTAssertEqual(try cache.load(for: symbols), second)
    }

    func test_load_missingKey_returnsNil() throws {
        let (cache, _) = makeCache()

        XCTAssertNil(try cache.load(for: symbols))
    }

    // MARK: - Helpers

    private func makeCache() -> (cache: QuoteCache, directory: URL) {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuoteCacheTests-\(UUID().uuidString)", isDirectory: true)
        addTeardownBlock {
            // 测试清理失败不影响断言结果。
            try? FileManager.default.removeItem(at: directory)
        }
        return (QuoteCache(store: JSONFileStore(directory: directory)), directory)
    }

    private func makeQuote(ticker: String, price: Decimal) -> Quote {
        Quote(
            symbol: Symbol(ticker: ticker),
            price: price,
            change: 1.24,
            changePercent: 0.66,
            timestamp: fixedDate
        )
    }
}
