@testable import MarketUSCore
@testable import MarketUSData
@testable import MarketUSNetworking
import XCTest

final class QuoteMapperTests: XCTestCase {
    func test_map_validDTO_mapsFieldsExactly() throws {
        let dto = makeDTO(
            price: "189.12",
            change: "-1.24",
            changePercent: "0.66",
            ts: "2026-11-03T14:30:00.123Z"
        )

        let quote = try QuoteMapper.map(dto)

        let expectedPrice = try decimal("189.12")
        let expectedChange = try decimal("-1.24")
        let expectedChangePercent = try decimal("0.66")
        let expectedTimestamp = try date(
            year: 2026, month: 11, day: 3,
            hour: 14, minute: 30, second: 0,
            fractionalSeconds: 0.123
        )

        XCTAssertEqual(quote.symbol.ticker, "AAPL")
        XCTAssertEqual(quote.price, expectedPrice)
        XCTAssertEqual(quote.change, expectedChange)
        XCTAssertEqual(quote.changePercent, expectedChangePercent)
        XCTAssertEqual(quote.timestamp.timeIntervalSince1970, expectedTimestamp.timeIntervalSince1970, accuracy: 0.001)
    }

    func test_map_invalidPrice_throwsDecoding() {
        assertMappingThrowsDecoding(makeDTO(price: "not-a-decimal"))
    }

    func test_map_invalidChange_throwsDecoding() {
        assertMappingThrowsDecoding(makeDTO(change: "abc"))
    }

    func test_map_invalidChangePercent_throwsDecoding() {
        assertMappingThrowsDecoding(makeDTO(changePercent: ""))
    }

    func test_map_invalidTimestamp_throwsDecoding() {
        assertMappingThrowsDecoding(makeDTO(ts: "not-a-timestamp"))
    }

    func test_map_staleMissing_defaultsToFalse() throws {
        let quote = try QuoteMapper.map(makeDTO(stale: nil))

        XCTAssertFalse(quote.isStale)
    }

    func test_map_staleTrue_preservesTrue() throws {
        let quote = try QuoteMapper.map(makeDTO(stale: true))

        XCTAssertTrue(quote.isStale)
    }

    // MARK: - Helpers

    private func makeDTO(
        price: String = "189.12",
        change: String = "1.24",
        changePercent: String = "0.66",
        ts: String = "2026-11-03T14:30:00.123Z",
        stale: Bool? = nil
    ) -> QuoteDTO {
        QuoteDTO(
            symbol: "AAPL",
            price: price,
            change: change,
            changePercent: changePercent,
            ts: ts,
            stale: stale
        )
    }

    private func decimal(_ text: String) throws -> Decimal {
        try XCTUnwrap(Decimal(string: text, locale: Locale(identifier: "en_US_POSIX")))
    }

    private func date(
        year: Int,
        month: Int,
        day: Int,
        hour: Int,
        minute: Int,
        second: Int,
        fractionalSeconds: TimeInterval
    ) throws -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(secondsFromGMT: 0))
        let components = DateComponents(
            year: year,
            month: month,
            day: day,
            hour: hour,
            minute: minute,
            second: second
        )
        let seconds = try XCTUnwrap(calendar.date(from: components))
        return seconds.addingTimeInterval(fractionalSeconds)
    }

    private func assertMappingThrowsDecoding(
        _ dto: QuoteDTO,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(try QuoteMapper.map(dto), file: file, line: line) { error in
            guard let marketError = error as? MarketError, marketError == .decoding else {
                return XCTFail("expected MarketError.decoding, got \(error)", file: file, line: line)
            }
        }
    }
}
