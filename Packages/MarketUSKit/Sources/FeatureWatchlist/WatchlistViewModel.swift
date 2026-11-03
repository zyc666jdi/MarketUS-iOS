import Combine
import Foundation
import MarketUSCore
import MarketUSObservability

@MainActor
public final class WatchlistViewModel: ObservableObject {
    @Published public private(set) var quotes: [Quote] = []
    @Published public private(set) var isLoading = false
    @Published public private(set) var errorMessage: String?

    private let repository: any QuoteRepository
    private let symbols: [Symbol]
    private let logger = AppLog.logger(category: "watchlist")

    public init(repository: any QuoteRepository, symbols: [Symbol]) {
        self.repository = repository
        self.symbols = symbols
    }

    public func load() async {
        guard !isLoading else {
            return
        }
        isLoading = true
        defer { isLoading = false }

        do {
            quotes = try await repository.quotes(for: symbols)
            errorMessage = nil
        } catch MarketError.cancelled {
            return
        } catch {
            errorMessage = String(localized: "watchlist.error")
            logger.error("watchlist load failed: \(String(describing: error), privacy: .public)")
        }
    }
}
