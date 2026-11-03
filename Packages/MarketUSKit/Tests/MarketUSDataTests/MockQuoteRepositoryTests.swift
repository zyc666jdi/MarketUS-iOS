import XCTest
@testable import MarketUSCore
@testable import MarketUSData

final class MockQuoteRepositoryTests: XCTestCase {
    private let fixedDate = Date(timeIntervalSince1970: 0)
    private let symbols = [Symbol(ticker: "AAPL"), Symbol(ticker: "MSFT")]

    func test_quotes_sameSeed_producesIdenticalPrices() async throws {
        let repository = MockQuoteRepository(seed: 7, clock: FixedClock(date: fixedDate))

        let first = try await repository.quotes(for: symbols)
        let second = try await repository.quotes(for: symbols)

        XCTAssertEqual(first.map(\.price), second.map(\.price))
        XCTAssertEqual(first.map(\.symbol.ticker), ["AAPL", "MSFT"])
    }

    func test_quotes_usesInjectedClock() async throws {
        let repository = MockQuoteRepository(seed: 7, clock: FixedClock(date: fixedDate))

        let quotes = try await repository.quotes(for: symbols)

        XCTAssertEqual(quotes.map(\.timestamp), [fixedDate, fixedDate])
    }

    func test_quotes_bullScenario_isAlwaysPositive() async throws {
        let repository = MockQuoteRepository(seed: 7, scenario: .bull, clock: FixedClock(date: fixedDate))

        let quotes = try await repository.quotes(for: symbols)

        XCTAssertTrue(quotes.allSatisfy(\.isPositive))
    }

    func test_quotes_emptyScenario_returnsNoQuotes() async throws {
        let repository = MockQuoteRepository(seed: 7, scenario: .empty, clock: FixedClock(date: fixedDate))

        let quotes = try await repository.quotes(for: symbols)

        XCTAssertTrue(quotes.isEmpty)
    }
}

private struct FixedClock: MarketClock {
    let date: Date
    var now: Date { date }
}
