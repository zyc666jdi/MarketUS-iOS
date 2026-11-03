import Foundation
import MarketUSCore

/// 轻量键值存储抽象。Feature 层不得直接读写文件。
public protocol FileStore: Sendable {
    func read<T: Decodable>(_ type: T.Type, from name: String) throws -> T?
    func write<T: Encodable>(_ value: T, to name: String) throws
    func remove(_ name: String) throws
}

/// 以单个 JSON 文件保存一份数据，写入使用原子替换，避免中途崩溃留下半个文件。
public struct JSONFileStore: FileStore {
    private let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    public func read<T: Decodable>(_ type: T.Type, from name: String) throws -> T? {
        let url = fileURL(for: name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        do {
            let data = try Data(contentsOf: url)
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw MarketError.unknown("file read failed: \(name)")
        }
    }

    public func write<T: Encodable>(_ value: T, to name: String) throws {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(value)
            try data.write(to: fileURL(for: name), options: .atomic)
        } catch {
            throw MarketError.unknown("file write failed: \(name)")
        }
    }

    public func remove(_ name: String) throws {
        let url = fileURL(for: name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return
        }
        do {
            try FileManager.default.removeItem(at: url)
        } catch {
            throw MarketError.unknown("file remove failed: \(name)")
        }
    }

    private func fileURL(for name: String) -> URL {
        directory.appendingPathComponent("\(name).json", isDirectory: false)
    }
}
