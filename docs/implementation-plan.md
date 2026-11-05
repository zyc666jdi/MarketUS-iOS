# 落地计划

> 本文档把「规划」变成「可执行任务」。每个 Phase 有明确产出与退出条件，
> **未通过退出条件不得进入下一阶段**。

## 总览

| Phase | 目标 | 预计 | 状态 |
| --- | --- | --- | --- |
| **0 · 规划** | 架构、AGENTS.md、docs、模板、标签体系 | — | ✅ 已完成 |
| **1 · 骨架** | 可构建的空 App + CI + Makefile + 分支保护 | 1–2 天 | ⬜ |
| **2 · 垂直切片** | 自选股端到端（模型→网络→VM→UI→测试） | 3–5 天 | ⬜ |
| **3 · Agent 激活** | 调度器 + 3 个真实 ticket 跑通 | 1 周 | ⬜ |
| **4 · 加固** | UI 测试、签名分发、性能与安全门禁 | 持续 | ⬜ |

---

## Phase 1 · 仓库骨架

### 任务

- [ ] `git init -b main`，推送到 **GitHub 公开仓库**（`gh repo create --public`）
- [ ] 创建标签：`yq` 遍历 `.github/labels.yml` 批量 `gh label create`
- [ ] 按 `docs/infrastructure.md` §1 开启：secret scanning + push protection、Dependabot、
      private vulnerability reporting、Actions 默认只读、fork PR 需批准
- [ ] 配置 `main` 分支规则（清单见 `docs/infrastructure.md` §2 / `ci-cd.md` §6）
- [ ] 创建 bot 账号（优先 GitHub App）+ 最小权限 token
- [x] 提交 `Brewfile` 锁定工具链版本
- [x] 创建 `.swiftlint.yml` / `.swiftformat` / `.gitleaks.toml`
- [x] 创建 `Tools/mock-market/`（FastAPI + Docker，语法校验通过）
- [x] 写入 `.xcode-version`（安装 Xcode 后核对版本）
- [x] 创建 `Packages/MarketUSKit/Package.swift`（下方模板）
- [x] 创建 `project.yml`（下方模板，`xcodegen generate` 已验证）
- [x] 创建 `App/Sources/MarketUSApp.swift`（最小可启动）
- [x] 创建 `App/Supporting/Info.plist`、`MarketUS.entitlements`、`PrivacyInfo.xcprivacy`
- [x] 创建 `Makefile` + `Tools/scripts/coverage.sh`
- [x] 创建 `.github/workflows/ci.yml`（规格见 `ci-cd.md`），⬜ 推送后跑通一次
- [ ] 提交 ADR-0001 状态从 `Proposed` 改为 `Accepted`

### 起手模板：Package.swift

```swift
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MarketUSKit",
    platforms: [.iOS(.v16)],
    products: [
        .library(name: "MarketUSCore", targets: ["MarketUSCore"]),
        .library(name: "MarketUSNetworking", targets: ["MarketUSNetworking"]),
        .library(name: "MarketUSPersistence", targets: ["MarketUSPersistence"]),
        .library(name: "MarketUSData", targets: ["MarketUSData"]),
        .library(name: "MarketUSDesignSystem", targets: ["MarketUSDesignSystem"]),
        .library(name: "MarketUSObservability", targets: ["MarketUSObservability"]),
        .library(name: "FeatureWatchlist", targets: ["FeatureWatchlist"]),
        .library(name: "FeatureQuotes", targets: ["FeatureQuotes"]),
        .library(name: "FeatureNews", targets: ["FeatureNews"]),
        .library(name: "FeaturePortfolio", targets: ["FeaturePortfolio"]),
    ],
    targets: [
        .target(name: "MarketUSCore"),
        .target(name: "MarketUSNetworking", dependencies: ["MarketUSCore"]),
        .target(name: "MarketUSPersistence", dependencies: ["MarketUSCore"]),
        .target(name: "MarketUSData",
                dependencies: ["MarketUSCore", "MarketUSNetworking", "MarketUSPersistence"]),
        .target(name: "MarketUSDesignSystem"),
        .target(name: "MarketUSObservability", dependencies: ["MarketUSCore"]),
        .target(name: "FeatureWatchlist",
                dependencies: ["MarketUSCore", "MarketUSDesignSystem", "MarketUSObservability"]),
        .target(name: "FeatureQuotes",
                dependencies: ["MarketUSCore", "MarketUSDesignSystem", "MarketUSObservability"]),
        .target(name: "FeatureNews",
                dependencies: ["MarketUSCore", "MarketUSDesignSystem", "MarketUSObservability"]),
        .target(name: "FeaturePortfolio",
                dependencies: ["MarketUSCore", "MarketUSDesignSystem", "MarketUSObservability"]),

        .testTarget(name: "MarketUSCoreTests", dependencies: ["MarketUSCore"]),
        .testTarget(name: "MarketUSNetworkingTests", dependencies: ["MarketUSNetworking"]),
        .testTarget(name: "MarketUSDataTests", dependencies: ["MarketUSData"]),
        .testTarget(name: "FeatureWatchlistTests", dependencies: ["FeatureWatchlist"]),
    ]
)
```

### 起手模板：project.yml

```yaml
name: MarketUS
options:
  bundleIdPrefix: com.marketus
  deploymentTarget: { iOS: "16.0" }
  createIntermediateGroups: true
packages:
  MarketUSKit:
    path: Packages/MarketUSKit
settings:
  base:
    SWIFT_VERSION: "6.0"
    SWIFT_STRICT_CONCURRENCY: complete
    ENABLE_USER_SCRIPT_SANDBOXING: YES
targets:
  MarketUS:
    type: application
    platform: iOS
    sources: [App/Sources]
    info:
      path: App/Supporting/Info.plist
      properties:
        CFBundleDisplayName: MarketUS
        ITSAppUsesNonExemptEncryption: false
        UILaunchScreen: {}
    entitlements:
      path: App/Supporting/MarketUS.entitlements
      properties: {}
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.marketus.app
        MARKETING_VERSION: "0.1.0"
        CURRENT_PROJECT_VERSION: "1"
        TARGETED_DEVICE_FAMILY: "1,2"
    dependencies:
      - package: MarketUSKit
        product: MarketUSCore
      - package: MarketUSKit
        product: MarketUSData
      - package: MarketUSKit
        product: MarketUSDesignSystem
      - package: MarketUSKit
        product: FeatureWatchlist
    scheme:
      testTargets: [MarketUSUITests]
  MarketUSUITests:
    type: bundle.ui-testing
    platform: iOS
    sources: [Tests/UITests]
    dependencies:
      - target: MarketUS
```

### 起手模板：Makefile

```makefile
SCHEME := MarketUS
DEST   := platform=iOS Simulator,name=iPhone 16   # 按本机 simctl 可用设备调整

.PHONY: bootstrap lint format build test coverage verify

bootstrap:
	@which xcodegen >/dev/null || brew install xcodegen
	xcodegen generate

lint:
	swiftformat --lint .
	swiftlint --strict

format:
	swiftformat .

build: bootstrap
	xcodebuild -scheme $(SCHEME) -destination '$(DEST)' \
	  build CODE_SIGNING_ALLOWED=NO

test: bootstrap
	xcodebuild test -scheme $(SCHEME) -destination '$(DEST)' \
	  -enableCodeCoverage YES CODE_SIGNING_ALLOWED=NO

coverage:
	./Tools/scripts/coverage.sh

verify: lint build test coverage
	gitleaks detect --no-git -v
```

### 退出条件

> 已完成：`project.yml`、`Package.swift`、最小 App、`Makefile`、`ci.yml`、`Brewfile`、
> 工具链配置、Mock 行情服务。**未完成**：本机构建验证（缺 Xcode）与 GitHub 侧配置。

- [x] `make bootstrap && make verify` 通过（Xcode 26.6 + iOS 26.5，已验证）
- [ ] CI 在 PR 上全绿，且分支保护生效（直接 push main 会被拒绝）
- [ ] 空 App 能在模拟器启动，显示 "MarketUS"
- [ ] `swift test --package-path Packages/MarketUSKit` 通过（可为空测）
- [ ] 用一次「只改 README」的 PR 验证：Agent 能开 PR、Reviewer 能评论、人类能合并

---

## Phase 2 · 垂直切片（自选股）

> 状态：核心链路已完成（PR #4，2026-11-05 合并）；行情 fixture、覆盖率门禁与独立评审留待 Phase 3。

目标：打通「模型 → 网络 → 仓储 → ViewModel → UI → 测试」全链路，作为后续 ticket 的模板。

### 任务

- [x] `MarketUSCore`：`Symbol`、`Quote`、`MarketError`、`QuoteRepository` 协议、`MarketClock`
- [x] `MarketUSNetworking`：`HTTPClient` + `URLSessionHTTPClient` + DTO + `MarketDataEndpoint`
- [x] `MarketUSData`：`MockQuoteRepository`（确定性随机游走 + 场景）
- [x] `MarketUSData`：`LiveQuoteRepository`（网络优先 + 缓存回退 + 错误映射）
- [ ] 定义行情 JSON 契约 + fixture（见 `docs/data-sources.md` §4）
- [x] `FeatureWatchlist`：`WatchlistViewModel`（`@MainActor`、去重、取消）+ `WatchlistView`（错误/延迟/重试）
- [x] `MarketUS`：组合根注入 + 根路由 + `MARKET_BASE_URL` 切换
- [x] 测试：ViewModel 全状态、Repository 错误映射、HTTPClient stub（20 tests；快照库待批准）
- [ ] 新增 3–5 个真实 ticket，验证 DoR 与拆分规则

### 退出条件

- [ ] 变更行覆盖率 ≥ 80%（当前 app 76.6%，Phase 3 启用门禁）
- [ ] 冷启动到列表可交互 < 1.5s（未测量）
- [x] 弱网/离线/401 有明确 UI 表现
- [ ] 代码通过一次独立 Agent 评审且无 P1/P2（尚未执行）

---

## Phase 3 · 激活 Agent 工作流

> 状态：3a（设计 + 只读 `status`/`plan`）已落地；3b（`once --issue`，默认 dry-run）已实现，待首次真实运行。

### 任务

- [ ] 实现 Dispatcher（先 `workflow_dispatch` 手动触发，后加 `schedule`）
- [ ] 接入 Implementer（新会话 + `AGENTS.md` + ticket 正文）
- [ ] 接入 Reviewer（独立会话 + 只读权限 + `docs/code-review.md`）
- [ ] 跑通 3 个真实 ticket（推荐顺序：docs → 文案/空态 → 小 UI 交互）
- [ ] 根据实际失败调整 `AGENTS.md`、`.agent/config.yml`、prompts
- [ ] 建立度量看板（周期时间、一次通过率、返工率）

### 第一张 Agent ticket 建议

```markdown
[Chore] 为自选股空态补充文案与截图占位

- risk: low
- affected_paths: Packages/MarketUSKit/Sources/FeatureWatchlist/**
- 验收：空态显示标题/说明/CTA；有快照测试；不改变现有逻辑
```

**不要**用第一张票做：网络层重构、鉴权、CI 修改、多模块迁移。

### 退出条件

- [ ] 3 个 ticket 中至少 2 个一次通过评审
- [ ] 人类评审耗时 < 10 min/PR
- [ ] 无 Agent 越权尝试（受保护路径 / 合并 / 密钥）
- [ ] 合并后 7 天内无 revert

---

## Phase 4 · 加固

- [ ] UI 测试 nightly + quarantine 机制
- [ ] 性能基线（启动、滚动、内存）纳入 CI 报告
- [ ] TestFlight 分发（fastlane，需 ADR）
- [ ] 签名与证书管理（人类持有）
- [ ] docs-lint、依赖审计、Action pinning 检查
- [ ] 每季度复盘：`AGENTS.md` 与流程是否仍匹配实际

---

## 风险登记

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| macOS CI 成本高 | 流水线慢/贵 | PR 只跑必需检查，nightly 跑全量 |
| Agent 生成测试「看起来对」但无效 | 假绿 | 评审强制检查「测试能否失败」+ mutation 抽检 |
| ticket 质量低导致频繁 blocked | 效率低 | DoR 硬门禁 + 模板引导 + 退回率度量 |
| 模块边界被侵蚀 | 后期难维护 | Package.swift 强制 + 评审检查越层 import |
| 行情数据合规 | 法律风险 | 授权先行，缓存与再分发策略固化在架构里 |
| 单人维护 + 多 Agent | 上下文过载 | 严格限额、并行 2、每日上限 12 |
