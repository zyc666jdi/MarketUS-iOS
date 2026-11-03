import MarketUSCore
import MarketUSDesignSystem
import SwiftUI

public struct WatchlistView: View {
    @StateObject private var viewModel: WatchlistViewModel

    public init(viewModel: WatchlistViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    public var body: some View {
        List(viewModel.quotes) { quote in
            QuoteRow(quote: quote)
        }
        .listStyle(.plain)
        .overlay {
            if viewModel.quotes.isEmpty, !viewModel.isLoading {
                Text("watchlist.empty")
                    .foregroundStyle(.secondary)
            }
        }
        .overlay {
            if viewModel.isLoading {
                ProgressView()
            }
        }
        .task {
            await viewModel.load()
        }
    }
}

private struct QuoteRow: View {
    let quote: Quote

    var body: some View {
        HStack {
            Text(quote.symbol.ticker)
                .font(.headline)
            Spacer()
            VStack(alignment: .trailing) {
                Text(quote.priceText)
                    .monospacedDigit()
                Text(quote.changeText)
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(quote.isPositive ? MarketTheme.gain : MarketTheme.loss)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(Text(verbatim: "\(quote.symbol.ticker) \(quote.priceText) \(quote.changeText)"))
    }
}
