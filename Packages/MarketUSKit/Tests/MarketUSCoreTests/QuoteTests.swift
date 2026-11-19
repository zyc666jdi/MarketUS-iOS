import Foundation
@testable import MarketUSCore
import XCTest

final class QuoteTests: XCTestCase {
    private let date = Date(timeIntervalSince1970: 1_700_000_000)
    private let localeIdentifiers = ["en_US", "de_DE", "fr_FR", "ar_EG"]

    /// 各 Locale 下 `NumberFormatter` 对同一数值的固定输出，用作 POSIX 期望值的正向对照。
    /// 显式传入 Locale 后，这些期望值不受运行机默认 Locale 影响。
    private let expectedLocalizedPrices: [String: String] = [
        "en_US": "1,234.50",
        "de_DE": "1.234,50",
        "fr_FR": "1\u{202F}234,50", // U+202F NARROW NO-BREAK SPACE
        "ar_EG": "\u{0661}\u{066C}\u{0662}\u{0663}\u{0664}\u{066B}\u{0665}\u{0660}", // ١٬٢٣٤٫٥٠
    ]
    private let expectedLocalizedPercents: [String: String] = [
        "en_US": "12.34",
        "de_DE": "12,34",
        "fr_FR": "12,34",
        "ar_EG": "\u{0661}\u{0662}\u{066B}\u{0663}\u{0664}", // ١٢٫٣٤
    ]

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

    func test_priceText_isIndependentOfLocale() throws {
        let quote = makeQuote(price: 1234.5)
        let posixText = "1234.50"

        // 固定 POSIX 期望：`.` 作小数分隔符，且不输出千分位。
        XCTAssertEqual(quote.priceText, posixText)

        for identifier in localeIdentifiers {
            let localized = try localizedText(1234.5, localeIdentifier: identifier)
            let expectedLocalized = try XCTUnwrap(expectedLocalizedPrices[identifier])

            XCTAssertEqual(localized, expectedLocalized, "\(identifier) 的本地化格式应与 NumberFormatter 预期一致")
            XCTAssertEqual(quote.priceText, posixText, "priceText 应固定为 POSIX 格式，不随 \(identifier) 变化")
        }
    }

    func test_changeText_isIndependentOfLocale() throws {
        let quote = makeQuote(change: 1234.5, changePercent: 12.34)
        let posixText = "+1234.50 (+12.34%)"

        // 固定 POSIX 期望：`.` 作小数分隔符，且不输出千分位。
        XCTAssertEqual(quote.changeText, posixText)

        for identifier in localeIdentifiers {
            let expectedChange = try XCTUnwrap(expectedLocalizedPrices[identifier])
            let expectedPercent = try XCTUnwrap(expectedLocalizedPercents[identifier])

            XCTAssertEqual(
                try localizedText(1234.5, localeIdentifier: identifier),
                expectedChange,
                "\(identifier) 的本地化价格格式应与 NumberFormatter 预期一致"
            )
            XCTAssertEqual(
                try localizedText(12.34, localeIdentifier: identifier),
                expectedPercent,
                "\(identifier) 的本地化百分比格式应与 NumberFormatter 预期一致"
            )
            XCTAssertEqual(quote.changeText, posixText, "changeText 应固定为 POSIX 格式，不随 \(identifier) 变化")
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

    private func localizedText(_ value: Decimal, localeIdentifier: String) throws -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: localeIdentifier)
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = true
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2

        return try XCTUnwrap(formatter.string(from: NSDecimalNumber(decimal: value)))
    }
}
