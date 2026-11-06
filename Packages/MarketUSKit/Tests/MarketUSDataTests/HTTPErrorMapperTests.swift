@testable import MarketUSCore
@testable import MarketUSData
import XCTest

final class HTTPErrorMapperTests: XCTestCase {
    func test_validate_successStatus_doesNotThrow() throws {
        for statusCode in [200, 201, 204, 299] {
            let response = try makeResponse(statusCode: statusCode)

            XCTAssertNoThrow(try HTTPErrorMapper.validate(response), "status \(statusCode)")
        }
    }

    func test_validate_unauthorized_throwsUnauthorized() throws {
        let response = try makeResponse(statusCode: 401)

        XCTAssertThrowsError(try HTTPErrorMapper.validate(response)) { error in
            XCTAssertEqual(error as? MarketError, .unauthorized)
        }
    }

    func test_validate_rateLimitedWithRetryAfter_throwsRateLimitedWithInterval() throws {
        let response = try makeResponse(statusCode: 429, headers: ["Retry-After": "2"])

        XCTAssertThrowsError(try HTTPErrorMapper.validate(response)) { error in
            XCTAssertEqual(error as? MarketError, .rateLimited(retryAfter: 2))
        }
    }

    func test_validate_rateLimitedWithoutRetryAfter_throwsRateLimitedWithNilInterval() throws {
        let response = try makeResponse(statusCode: 429)

        XCTAssertThrowsError(try HTTPErrorMapper.validate(response)) { error in
            XCTAssertEqual(error as? MarketError, .rateLimited(retryAfter: nil))
        }
    }

    func test_validate_rateLimitedWithInvalidRetryAfter_throwsRateLimitedWithNilInterval() throws {
        let response = try makeResponse(statusCode: 429, headers: ["Retry-After": "later"])

        XCTAssertThrowsError(try HTTPErrorMapper.validate(response)) { error in
            XCTAssertEqual(error as? MarketError, .rateLimited(retryAfter: nil))
        }
    }

    func test_validate_serverErrors_throwServerWithStatusCode() throws {
        for statusCode in [500, 503] {
            let response = try makeResponse(statusCode: statusCode)

            XCTAssertThrowsError(try HTTPErrorMapper.validate(response), "status \(statusCode)") { error in
                XCTAssertEqual(error as? MarketError, .server(status: statusCode))
            }
        }
    }

    func test_map_cancelledURLError_returnsCancelled() {
        XCTAssertEqual(HTTPErrorMapper.map(URLError(.cancelled)), .cancelled)
    }

    func test_map_decodingError_returnsDecoding() {
        let error = DecodingError.dataCorrupted(
            DecodingError.Context(codingPath: [], debugDescription: "malformed payload")
        )

        XCTAssertEqual(HTTPErrorMapper.map(error), .decoding)
    }

    func test_map_marketError_returnsSameError() {
        let errors: [MarketError] = [.offline, .unauthorized]
        for error in errors {
            XCTAssertEqual(HTTPErrorMapper.map(error), error)
        }
    }

    func test_map_unknownError_returnsUnknownWithDescription() {
        struct SampleError: Error, CustomStringConvertible {
            var description: String {
                "sample failure"
            }
        }

        XCTAssertEqual(HTTPErrorMapper.map(SampleError()), .unknown("sample failure"))
    }

    // MARK: - Helpers

    private func makeResponse(
        statusCode: Int,
        headers: [String: String]? = nil
    ) throws -> HTTPURLResponse {
        let url = try XCTUnwrap(URL(string: "https://example.com/quotes"))
        return try XCTUnwrap(
            HTTPURLResponse(
                url: url,
                statusCode: statusCode,
                httpVersion: "HTTP/1.1",
                headerFields: headers
            )
        )
    }
}
