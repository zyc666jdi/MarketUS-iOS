import MarketUSCore
import MarketUSDesignSystem
import SwiftUI

public struct WatchlistView: View {
    @StateObject private var viewModel: WatchlistViewModel

    public init(viewModel: WatchlistViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    public var body: some View {
        VStack(spacing: 0) {
            if let message = viewModel.errorMessage {
                banner(message: message, isError: true)
            } else if viewModel.isStale {
                banner(message: String(localized: "watchlist.stale"), isError: false)
            }

            List(viewModel.quotes) { quote in
                QuoteRow(quote: quote)
            }
            .listStyle(.plain)
            .overlay {
                if viewModel.isEmpty {
                    Text("watchlist.empty")
                        .foregroundStyle(.secondary)
                }
            }
            .overlay {
                if viewModel.isLoading {
                    ProgressView()
                }
            }
            .refreshable {
                await viewModel.refresh()
            }
        }
        .task {
            await viewModel.load()
        }
    }

    private func banner(message: String, isError: Bool) -> some View {
        HStack(spacing: 8) {
            Text(message)
                .font(.footnote)
            Spacer()
            if isError {
                Button("watchlist.retry") {
                    Task { await viewModel.refresh() }
                }
                .font(.footnote.weight(.semibold))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(isError ? Color.red.opacity(0.12) : Color.orange.opacity(0.15))
        .accessibilityElement(children: .combine)
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
