# ADR-0001: iOS 技术栈与模块化方案

- **状态**：Proposed（2026-11-03 修订：最低版本调整为 iOS 16）
- **日期**：2026-11-03
- **决策者**：@ios-leads（待确认）
- **相关**：`docs/architecture.md`、`docs/implementation-plan.md`、`docs/dependencies.md`

## 背景

从零开始一个美股行情 App（MarketUS），开发模式是「少量人类 + 多个 AI Agent 并行」。
两个非常规约束：

1. **工程文件不能是合并冲突源**：`.xcodeproj` 是二进制/XML 混合格式，多 Agent 并行必然冲突。
2. **模块边界必须是编译器强制的**：Agent 会「就近实现」，靠口头约定无法阻止越层依赖。

同时团队规模小，不能承担重量级架构（RIBs、自研 DI、多层抽象）的维护成本。
产品要求覆盖更多存量设备，**最低支持 iOS 16**。

## 选项

| 选项 | 优点 | 缺点 |
| --- | --- | --- |
| A. 单 target + 手写 .xcodeproj | 上手最快 | 工程文件冲突、无边界、后期拆不动 |
| B. XcodeGen + 单 SwiftPM Package（多 target） | 工程可生成、边界由编译器强制、结构轻 | 需维护 `project.yml` |
| C. Tuist + 多 SwiftPM Package | 能力最强、缓存最好 | 学习与维护成本高，单人不划算 |
| D. Bazel | 极致增量与可复现 | 对 iOS 小团队明显过重 |

## 决策

选择 **B**：

- 工程由 **XcodeGen** 生成，`.xcodeproj` 不提交，唯一事实来源是 `project.yml`。
- 代码组织为**单个本地 Swift Package** `Packages/MarketUSKit`，用 **target** 做模块隔离：
  `MarketUSCore`、`MarketUSNetworking`、`MarketUSPersistence`、`MarketUSData`、
  `MarketUSDesignSystem`、`MarketUSObservability`、`Feature*`。
- 依赖单向：Feature → Core/DesignSystem/Observability；Data → Networking/Persistence；
  App 作为组合根依赖全部。**Feature 不得 import Data/Networking/Persistence**。
- **最低 iOS 16.0**，Swift 6 语言模式 + 严格并发，SwiftUI，原生 `URLSession`（不含 Alamofire）。

因最低版本为 iOS 16，以下选型相应调整：

| 能力 | iOS 17 方案（放弃） | 本项目方案（iOS 16） |
| --- | --- | --- |
| 状态观察 | `@Observable` / Observation | `ObservableObject` + `@Published`（`@MainActor`） |
| 持久化 | SwiftData | 协议 + `Codable` 原子文件写入；需要关系查询时再评估 Core Data |
| 单元测试 | Swift Testing | XCTest（Swift Testing 要求 iOS 17 / macOS 14） |
| 空态组件 | `ContentUnavailableView` | 自研空态视图 |
| 滚动 API | `scrollPosition` / `scrollTargetLayout` | iOS 16 可用 API |

## 后果

**正面**

- 工程文件零冲突，Agent 并行安全。
- 越层 import 直接编译失败，评审不必靠人盯。
- 新功能 = 新 target，老代码几乎不动，回滚容易。
- 最低 iOS 16 覆盖更广的设备，包括仍在 iOS 16 的旧机型。

**负面 / 代价**

- 放弃 `@Observable` 与 SwiftData：状态样板更多，持久化需自研（但更可控、更易测）。
- 放弃 Swift Testing：宏与并行测试体验较差，需依赖 XCTest。
- `project.yml` 与 `Package.swift` 变更需重生成工程，且属受保护路径（只能人类改）。
- 需持续防止 Agent 误用 iOS 17+ API（评审清单已加入可用性检查）。

**需要跟进**

- [ ] Phase 1 提交 `project.yml` 与 `Package.swift` 并跑通 CI
- [ ] 确认 `.xcode-version` 与团队 Xcode 一致
- [ ] 持久化层在 Phase 2 用协议隔离，为将来迁移 Core Data 留口
- [ ] 当最低版本升到 17+ 时，单独开 ADR 评估迁移 `@Observable` / SwiftData / Swift Testing

## 重新评估的触发条件

- 最低 iOS 版本提升到 17+
- 单个 target 超过 8k 行或构建超过 60s
- 文件存储无法满足查询/迁移需求（届时评估 Core Data 或 GRDB，需新 ADR）
