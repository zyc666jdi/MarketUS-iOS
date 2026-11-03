import FeatureWatchlist
import MarketUSCore
import SwiftUI

struct RootView: View {
    private let environment = AppEnvironment.live

    var body: some View {
        NavigationStack {
            WatchlistView(
                viewModel: WatchlistViewModel(
                    repository: environment.quoteRepository,
                    symbols: Self.defaultSymbols
                )
            )
            .navigationTitle("watchlist.title")
        }
    }

    private static let defaultSymbols = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
        .map { Symbol(ticker: $0) }
}
