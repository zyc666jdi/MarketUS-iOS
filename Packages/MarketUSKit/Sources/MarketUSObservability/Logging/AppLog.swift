import os

/// 统一日志入口。禁止业务代码直接 `print`。
public enum AppLog {
    public static let subsystem = "com.marketus.app"

    public static func logger(category: String) -> Logger {
        Logger(subsystem: subsystem, category: category)
    }
}
