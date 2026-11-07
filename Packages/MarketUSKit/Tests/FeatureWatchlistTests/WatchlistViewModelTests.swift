@testable import FeatureWatchlist
@testable import MarketUSCore
import XCTest

@MainActor
final class WatchlistViewModelTests: XCTestCase {
    private let symbols = [Symbol(ticker: "AAPL"), Symbol(ticker: "MSFT")]

    func test_load_success_publishesQuotesAndClearsError() async {
        let repository = StubQuoteRepository(result: .success([makeQuote()]))
        let viewModel = WatchlistViewModel(repository: repository, symbols: symbols)

        await viewModel.load()

        XCTAssertEqual(viewModel.quotes.count, 1)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    func test_load_offlineError_publishesOfflineMessage() async {
        let repository = StubQuoteRepository(result: .failure(MarketError.offline))
        let viewModel = WatchlistViewModel(repository: repository, symbols: symbols)

        await viewModel.load()

        XCTAssertEqual(viewModel.errorMessage, String(localized: "watchlist.error.offline"))
        XCTAssertTrue(viewModel.quotes.isEmpty)
        XCTAssertFalse(viewModel.isLoading)
    }

    func test_load_staleQuote_marksStale() async {
        let repository = StubQuoteRepository(result: .success([makeQuote(isStale: true)]))
        let viewModel = WatchlistViewModel(repository: repository, symbols: symbols)

        await viewModel.load()

        XCTAssertTrue(viewModel.isStale)
    }

    func test_load_concurrentCalls_issueSingleRequest() async {
        let repository = StubQuoteRepository(result: .success([makeQuote()]))
        let viewModel = WatchlistViewModel(repository: repository, symbols: symbols)

        async let first: Void = viewModel.load()
        async let second: Void = viewModel.load()
        _ = await (first, second)

        XCTAssertEqual(repository.callCount, 1)
    }

    func test_refresh_afterSuccess_reloads() async {
        let repository = StubQuoteRepository(result: .success([makeQuote()]))
        let viewModel = WatchlistViewModel(repository: repository, symbols: symbols)

        await viewModel.load()
        await viewModel.refresh()

        XCTAssertEqual(repository.callCount, 2)
    }

    // MARK: - Helpers

    private func makeQuote(isStale: Bool = false) -> Quote {
        Quote(
            symbol: Symbol(ticker: "AAPL"),
            price: 189.12,
            change: 1.24,
            changePercent: 0.66,
            timestamp: Date(timeIntervalSince1970: 0),
            isStale: isStale
        )
    }
}

private final class StubQuoteRepository: QuoteRepository, @unchecked Sendable {
    private(set) var callCount = 0
    private let result: Result<[Quote], Error>

    init(result: Result<[Quote], Error>) {
        self.result = result
    }

    func quotes(for _: [Symbol]) async throws -> [Quote] {
        callCount += 1
        return try result.get()
    }
}
