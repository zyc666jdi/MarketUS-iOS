import Combine
import Foundation
import MarketUSCore
import MarketUSObservability

@MainActor
public final class WatchlistViewModel: ObservableObject {
    @Published public private(set) var quotes: [Quote] = []
    @Published public private(set) var isLoading = false
    @Published public private(set) var errorMessage: String?

    public var isStale: Bool {
        quotes.contains(where: \.isStale)
    }

    public var isEmpty: Bool {
        quotes.isEmpty && !isLoading && errorMessage == nil
    }

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
            let result = try await repository.quotes(for: symbols)
            guard !Task.isCancelled else {
                return
            }
            quotes = result
            errorMessage = nil
        } catch MarketError.cancelled {
            return
        } catch {
            guard !Task.isCancelled else {
                return
            }
            errorMessage = Self.message(for: error)
            logger.error("watchlist load failed: \(String(describing: error), privacy: .public)")
        }
    }

    public func refresh() async {
        await load()
    }

    private static func message(for error: Error) -> String {
        guard let marketError = error as? MarketError else {
            return String(localized: "watchlist.error")
        }
        switch marketError {
        case .offline:
            return String(localized: "watchlist.error.offline")
        case .unauthorized:
            return String(localized: "watchlist.error.unauthorized")
        case .rateLimited:
            return String(localized: "watchlist.error.rateLimited")
        default:
            return String(localized: "watchlist.error")
        }
    }
}
