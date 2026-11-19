import Foundation
@testable import MarketUSCore
import XCTest

final class QuoteTests: XCTestCase {
    private let date = Date(timeIntervalSince1970: 1_700_000_000)
    private let localeIdentifiers = ["en_US", "de_DE", "fr_FR", "ar_EG"]

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

    func test_changeText_whenChangeIsNegative_includesMinusSignAndPercent() {
        let quote = makeQuote(change: -1.24, changePercent: -0.66)

        XCTAssertEqual(quote.changeText, "-1.24 (-0.66%)")
    }

    func test_changeText_whenChangeIsZero_hasNoSign() {
        let quote = makeQuote(change: 0, changePercent: 0)

        XCTAssertEqual(quote.changeText, "0.00 (0.00%)")
    }

    func test_priceText_whenPriceIsZero_formatsTwoFractionDigits() {
        let quote = makeQuote(price: 0)

        XCTAssertEqual(quote.priceText, "0.00")
    }

    func test_priceText_isIndependentOfLocale() {
        let quote = makeQuote(price: 1234.5)

        XCTAssertEqual(quote.priceText, "1234.50")

        for identifier in localeIdentifiers {
            let localized = localizedText(1234.5, localeIdentifier: identifier)
            XCTAssertNotEqual(quote.priceText, localized, "priceText 不应使用 \(identifier) 的本地化格式")
        }
    }

    func test_changeText_isIndependentOfLocale() {
        let quote = makeQuote(change: 1234.5, changePercent: 12.34)

        XCTAssertEqual(quote.changeText, "+1234.50 (+12.34%)")

        for identifier in localeIdentifiers {
            let localizedChange = localizedText(1234.5, localeIdentifier: identifier)
            let localizedPercent = localizedText(12.34, localeIdentifier: identifier)
            let localized = "+\(localizedChange) (+\(localizedPercent)%)"
            XCTAssertNotEqual(quote.changeText, localized, "changeText 不应使用 \(identifier) 的本地化格式")
        }
    }

    // MARK: - Helpers

    private func makeQuote(
        price: Decimal = 189.12,
        change: Decimal = 1.24,
        changePercent: Decimal = 0.66
    ) -> Quote {
        Quote(
            symbol: Symbol(ticker: "AAPL"),
            price: price,
            change: change,
            changePercent: changePercent,
            timestamp: date
        )
    }

    private func localizedText(_ value: Decimal, localeIdentifier: String) -> String {
        String(
            format: "%.2f",
            locale: Locale(identifier: localeIdentifier),
            NSDecimalNumber(decimal: value).doubleValue
        )
    }
}
