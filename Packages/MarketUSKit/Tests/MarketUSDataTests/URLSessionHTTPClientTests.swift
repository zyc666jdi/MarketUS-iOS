import Foundation
@testable import MarketUSCore
@testable import MarketUSNetworking
import XCTest

final class URLSessionHTTPClientTests: XCTestCase {
    override func tearDown() {
        StubURLProtocol.setError(nil)
        super.tearDown()
    }

    func test_send_notConnectedToInternet_throwsOffline() async throws {
        try await assertSendFails(with: .notConnectedToInternet, expected: .offline)
    }

    func test_send_networkConnectionLost_throwsOffline() async throws {
        try await assertSendFails(with: .networkConnectionLost, expected: .offline)
    }

    func test_send_dataNotAllowed_throwsOffline() async throws {
        try await assertSendFails(with: .dataNotAllowed, expected: .offline)
    }

    func test_send_internationalRoamingOff_throwsOffline() async throws {
        try await assertSendFails(with: .internationalRoamingOff, expected: .offline)
    }

    func test_send_cancelled_throwsCancelled() async throws {
        try await assertSendFails(with: .cancelled, expected: .cancelled)
    }

    func test_send_timedOut_throwsUnknownWithErrorCode() async throws {
        let code = URLError.Code.timedOut
        try await assertSendFails(with: code, expected: .unknown("transport: \(code.rawValue)"))
    }

    func test_send_badURL_throwsUnknownWithErrorCode() async throws {
        let code = URLError.Code.badURL
        try await assertSendFails(with: code, expected: .unknown("transport: \(code.rawValue)"))
    }

    // MARK: - Helpers

    private func assertSendFails(
        with code: URLError.Code,
        expected: MarketError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async throws {
        StubURLProtocol.setError(URLError(code))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = URLSessionHTTPClient(session: session)

        do {
            _ = try await client.send(makeRequest())
            XCTFail("expected \(expected), but the request succeeded", file: file, line: line)
        } catch {
            XCTAssertEqual(error as? MarketError, expected, file: file, line: line)
        }
    }

    private func makeRequest() throws -> HTTPRequest {
        let url = try XCTUnwrap(URL(string: "https://example.com/quotes"))
        return HTTPRequest(url: url)
    }
}

private final class StubURLProtocol: URLProtocol {
    private static let store = StubURLProtocolStore()

    static func setError(_ error: URLError?) {
        store.set(error)
    }

    override class func canInit(with _: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let error = Self.store.get() else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        client?.urlProtocol(self, didFailWithError: error)
    }

    override func stopLoading() {}
}

private final class StubURLProtocolStore: @unchecked Sendable {
    private let lock = NSLock()
    private var error: URLError?

    func set(_ error: URLError?) {
        lock.lock()
        defer { lock.unlock() }
        self.error = error
    }

    func get() -> URLError? {
        lock.lock()
        defer { lock.unlock() }
        return error
    }
}
