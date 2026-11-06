@testable import MarketUSCore
@testable import MarketUSData
@testable import MarketUSNetworking
@testable import MarketUSPersistence
import XCTest

final class LiveQuoteRepositoryTests: XCTestCase {
    private let validJSON = """
    {"quotes":[{"symbol":"AAPL","price":"189.12","change":"1.24","changePercent":"0.66","ts":"2026-11-03T14:30:00.123Z","stale":false}]}
    """
    private let symbols = [Symbol(ticker: "AAPL")]

    func test_quotes_success_mapsDomainModel() async throws {
        let repository = try makeRepository(status: 200, body: Data(validJSON.utf8))

        let quotes = try await repository.quotes(for: symbols)

        XCTAssertEqual(quotes.count, 1)
        XCTAssertEqual(quotes.first?.symbol.ticker, "AAPL")
        XCTAssertEqual(quotes.first?.priceText, "189.12")
        XCTAssertEqual(quotes.first?.isStale, false)
    }

    func test_quotes_unauthorized_mapsError() async throws {
        let repository = try makeRepository(status: 401, body: Data())

        await assertThrows(repository, equals: .unauthorized)
    }

    func test_quotes_rateLimited_mapsError() async throws {
        let repository = try makeRepository(status: 429, body: Data())

        do {
            _ = try await repository.quotes(for: symbols)
            XCTFail("expected rateLimited")
        } catch let error as MarketError {
            guard case .rateLimited = error else {
                return XCTFail("expected rateLimited, got \(error)")
            }
        }
    }

    func test_quotes_serverError_mapsStatus() async throws {
        let repository = try makeRepository(status: 503, body: Data())

        await assertThrows(repository, equals: .server(status: 503))
    }

    func test_quotes_malformedJSON_mapsDecodingError() async throws {
        let repository = try makeRepository(status: 200, body: Data("{not-json".utf8))

        await assertThrows(repository, equals: .decoding)
    }

    func test_quotes_failure_fallsBackToCacheAsStale() async throws {
        let cache = try makeCache()
        let quote = Quote(
            symbol: Symbol(ticker: "AAPL"),
            price: 100,
            change: 1,
            changePercent: 1,
            timestamp: Date(timeIntervalSince1970: 0)
        )
        try cache.save([quote], for: symbols)

        let repository = try makeRepository(status: 500, body: Data(), cache: cache)
        let quotes = try await repository.quotes(for: symbols)

        XCTAssertEqual(quotes.count, 1)
        XCTAssertTrue(quotes[0].isStale)
        XCTAssertEqual(quotes[0].priceText, "100.00")
    }

    func test_quotes_success_savesCache() async throws {
        let cache = try makeCache()
        let repository = try makeRepository(status: 200, body: Data(validJSON.utf8), cache: cache)

        _ = try await repository.quotes(for: symbols)

        let cached = try cache.load(for: symbols)
        XCTAssertEqual(cached?.first?.priceText, "189.12")
    }

    // MARK: - Helpers

    private func makeRepository(
        status: Int,
        body: Data,
        cache: QuoteCache? = nil
    ) throws -> LiveQuoteRepository {
        let baseURL = try XCTUnwrap(URL(string: "https://example.invalid"))
        return LiveQuoteRepository(
            client: StubHTTPClient(statusCode: status, body: body),
            endpoint: MarketDataEndpoint(baseURL: baseURL),
            cache: cache
        )
    }

    private func makeCache() throws -> QuoteCache {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock {
            // 测试清理失败不影响断言结果。
            try? FileManager.default.removeItem(at: directory)
        }
        return QuoteCache(store: JSONFileStore(directory: directory))
    }

    private func assertThrows(
        _ repository: LiveQuoteRepository,
        equals expected: MarketError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        do {
            _ = try await repository.quotes(for: symbols)
            XCTFail("expected \(expected)", file: file, line: line)
        } catch let error as MarketError {
            XCTAssertEqual(error, expected, file: file, line: line)
        } catch {
            XCTFail("expected \(expected), got \(error)", file: file, line: line)
        }
    }
}

private struct StubHTTPClient: HTTPClient {
    let statusCode: Int
    let body: Data

    func send(_ request: HTTPRequest) async throws -> (Data, HTTPURLResponse) {
        let response = try XCTUnwrap(
            HTTPURLResponse(url: request.url, statusCode: statusCode, httpVersion: nil, headerFields: nil)
        )
        return (body, response)
    }
}
