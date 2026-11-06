@testable import MarketUSCore
import XCTest

final class QuoteTests: XCTestCase {
    private let date = Date(timeIntervalSince1970: 1_700_000_000)

    func test_symbol_uppercasesTicker() {
        XCTAssertEqual(Symbol(ticker: "aapl").ticker, "AAPL")
    }

    func test_isPositive_whenChangeIsZero_isTrue() {
        let quote = Quote(
            symbol: Symbol(ticker: "AAPL"),
            price: 100,
            change: 0,
            changePercent: 0,
            timestamp: date
        )

        XCTAssertTrue(quote.isPositive)
    }

    func test_priceText_formatsTwoFractionDigits() {
        let quote = Quote(
            symbol: Symbol(ticker: "AAPL"),
            price: 189.1,
            change: 1.24,
            changePercent: 0.66,
            timestamp: date
        )

        XCTAssertEqual(quote.priceText, "189.10")
    }

    func test_changeText_includesSignAndPercent() {
        let quote = Quote(
            symbol: Symbol(ticker: "AAPL"),
            price: 189.12,
            change: 1.24,
            changePercent: 0.66,
            timestamp: date
        )

        XCTAssertEqual(quote.changeText, "+1.24 (+0.66%)")
    }
}
