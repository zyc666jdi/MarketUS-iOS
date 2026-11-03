# 测试策略

> 测试是 Agent 交付质量的**唯一客观证据**。评审看的是「测试能否失败」，不是「测试通过」。

## 1. 目标与比例

| 层级 | 占比 | 运行时机 | 运行时间预算 |
| --- | --- | --- | --- |
| 单元测试 | ~70% | 每次提交 / PR | < 60s（核心模块 `swift test`） |
| 集成测试 | ~20% | PR | < 5min |
| UI 测试 | ~10% | PR 冒烟 + nightly 全量 | 冒烟 < 5min，全量 < 30min |
| 快照测试 | 覆盖所有 UI 组件/页面状态 | PR | < 3min |

原则：**能单元测的绝不放到 UI 测**；UI 测试只验证关键用户路径，不做全面覆盖。

## 2. 技术选型

| 用途 | 工具 |
| --- | --- |
| 单元/集成测试 | **XCTest**（`async` 测试方法），UI 用 XCUITest |
| UI 测试 | XCUITest |
| 快照 | `pointfreeco/swift-snapshot-testing`（需人类批准引入；当前未引入） |
| 网络桩 | `URLProtocol` stub（不引入 Mockingbird 等框架） |
| 依赖替身 | 手写 fake / spy（协议注入，不用 mock 框架） |
| 覆盖率 | `xcodebuild -enableCodeCoverage YES` + `xccov` / `llvm-cov` |
| 性能 | XCTest `measure` + Signpost 基准 |

## 3. 覆盖率政策

> **Phase 1 现状**：`make coverage` 只生成报告，不阻断（App 骨架阶段噪声过大）。
> Phase 2 开始时按上表启用门禁。

| 指标 | 阈值 | 说明 |
| --- | --- | --- |
| 变更行覆盖率 | ≥ 80% | PR 门禁，硬性 |
| 模块行覆盖率 | ≥ 70% | `MarketUSCore` / `MarketUSData` 重点 |
| 总体覆盖率 | 不得下降 | 与 main 基线比较 |
| 豁免 | 纯资源/文案/`Package.swift` | PR 中说明 |

覆盖率是**下限而非目标**：100% 覆盖的错误实现依然是错误实现。

## 4. 必测清单

**每个 ViewModel**

- [ ] 初始状态
- [ ] 加载成功 → 状态正确
- [ ] 每种错误 → 对应提示与可恢复动作
- [ ] 取消（视图消失）后不再写状态
- [ ] 并发触发（连续点击/下拉）不重复请求、状态不卡死

**每个 Repository / 网络层**

- [ ] 成功解码（含可选字段缺失）
- [ ] 4xx / 5xx / 超时 / 离线映射
- [ ] 重试次数与退避（用注入的 `Clock` 断言，不真的 sleep）
- [ ] 缓存命中 / 过期 / ETag 304
- [ ] 限流与 `Retry-After`

**每个持久化**

- [ ] 写入 → 读取一致
- [ ] 迁移路径（旧 schema → 新 schema）
- [ ] 清空/登出后的数据隔离
- [ ] 并发写入不丢数据

**每个格式化/日期逻辑**

- [ ] 时区（`America/New_York`）、夏令时切换
- [ ] 盘前/盘中/盘后/休市
- [ ] 大数、负数、零、极小值

## 5. 测试写法规则

```swift
func test_refresh_concurrentCalls_issuesSingleRequest() async throws {
    let client = StubHTTPClient(response: .quote(.aapl))
    let sut = WatchlistViewModel(repository: QuoteRepositoryStub(client: client),
                                 clock: TestClock())

    async let a: Void = sut.refresh()
    async let b: Void = sut.refresh()
    _ = await (a, b)

    XCTAssertEqual(await client.requestCount, 1)
    XCTAssertFalse(sut.isLoading)
}
```

- 命名：`test_<行为>_<条件>_<预期>()`
- 一个测试一个断言主题；失败信息要能直接定位问题
- **禁止** `sleep`、真实网络、真实时间、依赖执行顺序、依赖共享可变全局状态
- **禁止** 恒真断言：`#expect(true)`、`XCTAssertNotNil(x)` 了事
- 快照必须有设备/语言/外观矩阵，容差不能为了通过而放宽
- 测试数据用代码内 fixture，不依赖真实行情（授权数据不可入库）

## 6. Bug 修复的测试要求

1. 先写**能复现 bug 的失败测试**，提交记录中可见（或 PR 说明中贴失败日志）。
2. 修复后该测试通过，且不修改其断言。
3. 若旧测试本身有误 → 单独说明为什么错、错在哪，并加新测试覆盖原意图。

## 7. Flaky 测试政策

| 情况 | 动作 |
| --- | --- |
| 首次 flaky | 记录到 `docs/runbooks/ci-failure.md` 的已知问题；重跑 1 次 |
| 一周内 ≥ 2 次 flaky | 移入 quarantine（`XCTSkip("flaky: #issue")`）+ 建 ticket |
| quarantine 超 48h | 升级为 P1，阻断新功能合并 |
| 修复方式 | 消除时序依赖/网络依赖；**禁止**加重试掩盖 |

CI 不允许「失败自动重试直到通过」的配置。

## 8. UI 测试策略

- PR 冒烟集（3–5 条）：启动 → 自选股列表 → 打开详情 → 返回 → 搜索。
- nightly 全量：覆盖所有主路径 + 深链 + 空态/错误态。
- 使用 accessibility identifier（稳定），不用可见文案定位（易因本地化失效）。
- 每个 UI 测试必须能独立运行，不依赖前一个测试的状态。

## 9. 性能测试

| 指标 | 方法 | 阈值 |
| --- | --- | --- |
| 冷启动 | Signpost 打点 + XCTest measure | < 1.5s（P90） |
| 列表滚动 | 滚动 1000 行，记录 hitch time | 掉帧 < 1% |
| 内存 | 常规使用 10 分钟 | < 200MB |
| 包体积 | `xcrun assetutil` / App Thinning 报告 | 增量 < 5MB/版本 |

性能回归不阻断合并，但必须建 ticket 并在发布前评估。

## 10. Agent 的测试职责

- 行为变更必须附带测试变更；纯文案/资源可豁免并在 PR 说明。
- 不得为了让 CI 通过而：删除测试、放宽断言、加 `skip`、降低覆盖率阈值。
- 不得引入 `sleep` 或依赖真实时间/网络来「稳定」测试。
- 新增测试必须在 PR 的「验收标准对照」表中被引用。
- 若认为既有测试本身错误，必须单独说明并**新增**测试，而非直接改写。

## 11. 本地与 CI 命令

```bash
# 快速反馈（核心逻辑，不启模拟器）
swift test --package-path Packages/MarketUSKit

# 全量（模拟器）
make test

# 覆盖率
make coverage

# 单个测试
xcodebuild test -scheme MarketUS \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:MarketUSKitTests/WatchlistViewModelTests
```

> Phase 0 尚未落地工程，以上命令在 Phase 1 生效（见 `implementation-plan.md`）。
