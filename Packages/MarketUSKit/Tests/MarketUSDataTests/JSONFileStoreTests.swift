@testable import MarketUSPersistence
import XCTest

final class JSONFileStoreTests: XCTestCase {
    func test_write_thenRead_returnsSameValue() throws {
        let store = JSONFileStore(directory: makeTemporaryDirectory())
        let fixture = Fixture(id: 1, name: "AAPL", price: 189.12)

        try store.write(fixture, to: "quote")

        let decoded = try store.read(Fixture.self, from: "quote")
        XCTAssertEqual(decoded, fixture)
    }

    func test_read_missingKey_returnsNil() throws {
        let store = JSONFileStore(directory: makeTemporaryDirectory())

        let decoded = try store.read(Fixture.self, from: "missing")

        XCTAssertNil(decoded)
    }

    func test_write_sameKey_replacesExistingValue() throws {
        let store = JSONFileStore(directory: makeTemporaryDirectory())
        let first = Fixture(id: 1, name: "AAPL", price: 100)
        let second = Fixture(id: 2, name: "MSFT", price: 200)

        try store.write(first, to: "quote")
        try store.write(second, to: "quote")

        let decoded = try store.read(Fixture.self, from: "quote")
        XCTAssertEqual(decoded, second)
    }

    func test_remove_existingKey_thenReadReturnsNil() throws {
        let store = JSONFileStore(directory: makeTemporaryDirectory())
        let fixture = Fixture(id: 1, name: "AAPL", price: 189.12)
        try store.write(fixture, to: "quote")

        try store.remove("quote")

        let decoded = try store.read(Fixture.self, from: "quote")
        XCTAssertNil(decoded)
    }

    func test_write_missingDirectory_createsDirectory() throws {
        let directory = makeTemporaryDirectory()
            .appendingPathComponent("nested/deeper", isDirectory: true)
        let store = JSONFileStore(directory: directory)
        let fixture = Fixture(id: 1, name: "AAPL", price: 189.12)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))

        try store.write(fixture, to: "quote")

        var isDirectory: ObjCBool = false
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory)
        )
        XCTAssertTrue(isDirectory.boolValue)
        XCTAssertEqual(try store.read(Fixture.self, from: "quote"), fixture)
    }

    // MARK: - Helpers

    private func makeTemporaryDirectory() -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("JSONFileStoreTests-\(UUID().uuidString)", isDirectory: true)
        addTeardownBlock {
            // 测试清理失败不影响断言结果。
            try? FileManager.default.removeItem(at: directory)
        }
        return directory
    }
}

private struct Fixture: Codable, Equatable {
    let id: Int
    let name: String
    let price: Double
}
