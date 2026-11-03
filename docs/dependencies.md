# 依赖基线

> 原则：**运行时零第三方依赖。** 每个新依赖都是一次架构决策，需要 ADR + 人类批准。
> 本文件的清单就是白名单，不在清单内的一律视为未批准。

## 1. 分层策略

| 类别 | 是否进入 App 二进制 | 审批要求 |
| --- | --- | --- |
| 运行时依赖 | ✅ | ADR + 人类批准 + 许可证检查 |
| 测试依赖 | ❌（仅 test target） | 人类批准 + 许可证检查 |
| 开发工具（CLI） | ❌ | 记录版本，无需 ADR |
| CI Action | ❌ | 人类批准 + pin 到 commit SHA |

## 2. 运行时依赖

**当前：0 个。**

原生能力覆盖了 MVP 的全部需求：

| 需求 | 原生方案 | 备注 |
| --- | --- | --- |
| 网络 | `URLSession` + `async/await` | 不引入 Alamofire |
| WebSocket | `URLSessionWebSocketTask` | 自处理重连/心跳 |
| 状态管理 | `ObservableObject` + `@Published` | iOS 16 不支持 `@Observable` |
| 持久化 | `Codable` + 原子写文件 | 需要关系查询时再评估 Core Data/GRDB |
| 图表 | Swift Charts | iOS 16+ |
| 图片 | `AsyncImage` | 需要缓存时再评估 Nuke/Kingfisher |
| 日志 | `os.Logger` | 不引入任何日志框架 |
| 依赖注入 | 构造器注入 + 组合根 | 不引入 DI 框架 |
| 布局 | SwiftUI Layout | 不引入 SnapKit |
| 日期/时区 | `Calendar` / `TimeZone` / `DateFormatter` | 固定 `America/New_York` |

## 3. 测试依赖

| 库 | 用途 | 许可 | 状态 |
| --- | --- | --- | --- |
| XCTest | 单元 / UI 测试 | Apple | ✅ 内置 |
| `pointfreeco/swift-snapshot-testing` | 视图快照 | MIT | ⏳ 待批准（Phase 2 评估） |
| `URLProtocol` stub | 网络桩 | 原生 | ✅ 自实现 |
| 手写 fake/spy | 依赖替身 | 原生 | ✅ 自实现 |

> **为什么不用 Swift Testing**：其最低平台为 iOS 17 / macOS 14（见
> [swift-testing Package.swift](https://github.com/swiftlang/swift-testing/blob/main/Package.swift)），
> 本项目最低 iOS 16。升级最低版本后再评估迁移；届时可 XCTest 与 Swift Testing 并存。

## 4. 开发工具（不进入产物）

| 工具 | 用途 | 许可 | 安装方式 |
| --- | --- | --- | --- |
| XcodeGen | 从 `project.yml` 生成工程 | MIT | Brewfile |
| SwiftLint | 静态分析 | MIT | Brewfile |
| SwiftFormat | 格式化 | MIT | Brewfile |
| gitleaks | 密钥扫描 | MIT | Brewfile |
| xcbeautify | 美化 CI 日志 | MIT | Brewfile（可选） |
| Docker | 跑 mock-market | Apache-2.0 | 手动 |
| Python 3.12 + FastAPI | mock server 运行时 | MIT/BSD | 仅容器内 |
| fastlane | TestFlight 分发（Phase 4） | MIT | Brewfile（届时） |

版本通过 **`Brewfile` + `.xcode-version`** 锁定，CI 与本地一致。

## 5. 明确不引入（及原因）

| 库 | 不引入原因 |
| --- | --- |
| Alamofire | `URLSession` 足够；多一层抽象，评审成本高 |
| RxSwift / CombineX | 原生 async/await + Combine 够用；学习与调试成本 |
| Realm / GRDB | MVP 数据量小；先用文件存储，需要再以 ADR 引入 |
| SwiftData | 需 iOS 17，本项目最低 16 |
| ComposableArchitecture / RIBs | 团队小，架构复杂度不值 |
| Factory / Swinject | 构造器注入 + 组合根足够 |
| Sourcery / R.swift | 生成代码增加不可见耦合；用 String Catalog 与手写资源访问 |
| Kingfisher / Nuke | 先用 `AsyncImage`，有缓存需求再评估（Phase 3） |
| Lottie | 动效优先用原生 SwiftUI；确有复杂动画再评估 |
| Firebase / Sentry / 三方崩溃平台 | 涉及隐私清单与合规，Phase 3 单独决策 |
| 任何二进制 framework | 无法审计、供应链风险 |

## 6. 准入标准（新依赖必须全部满足）

- [ ] 许可证为 MIT / Apache-2.0 / BSD（GPL/AGPL/SSPL 一律拒绝）
- [ ] 近 12 个月有维护（提交、issue 响应、发版）
- [ ] 有安全策略（SECURITY.md）与可追溯的发版
- [ ] 传递依赖可接受（≤ 3 个，且都是知名库）
- [ ] 原生 API 无法合理替代（需在 ADR 中说明对比）
- [ ] 可被协议隔离（不渗入 Core/Feature 层）
- [ ] 移除成本可接受（≤ 1 天工作量）
- [ ] 精确版本 pin + `Package.resolved` 提交
- [ ] 人类批准（ADR 状态改为 Accepted）

## 7. 版本与升级

- **精确版本**（`.exact(...)`），不用 `from:` 浮动区间。
- `Package.resolved` 必须提交，保证所有人与 CI 解析一致。
- 每月一次 Dependabot PR 批量升级（按 minor/patch 分组）；**不自动合并**。
- 安全补丁：7 天内升级；major 升级需要 ADR 与回归验证。
- 升级 PR 必须跑全量 `make verify` + 真机冒烟（UI 相关）。

## 8. 供应链安全

- Action 必须 pin 到 **commit SHA**，禁止 `@main` / 浮动 tag。
- 不执行 `curl | bash` 安装；工具只从 Homebrew 或官方发行版安装。
- 公开仓库的依赖 PR 需人工审查 diff（尤其 `Package.swift` 与 workflow）。
- 新增二进制/插件（macro、build tool plugin）视同新增依赖，需 ADR。

## 9. 待确认

| # | 决策 | 选项 | 建议 |
| --- | --- | --- | --- |
| 1 | 是否引入快照测试库 | A. 引入 swift-snapshot-testing　B. 只做逻辑测试 | **A（Phase 2）**，UI 回归价值高 |
| 2 | Mock Server 技术 | A. Python FastAPI　B. Node `ws` | **A**，代码量与依赖最小 |
| 3 | 崩溃/分析平台 | A. 暂不引入　B. Sentry　C. Firebase | **A**，Phase 3 再决策 |
