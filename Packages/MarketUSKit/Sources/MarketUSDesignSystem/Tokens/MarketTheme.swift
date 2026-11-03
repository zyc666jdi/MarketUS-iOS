import SwiftUI

/// 设计 Token。语义色允许后续切换「绿涨红跌 / 红涨绿跌」而不改业务代码。
public enum MarketTheme {
    public static let gain = Color(red: 0.13, green: 0.65, blue: 0.35)
    public static let loss = Color(red: 0.86, green: 0.26, blue: 0.27)

    public static let spacing: CGFloat = 12
    public static let cornerRadius: CGFloat = 10
}
