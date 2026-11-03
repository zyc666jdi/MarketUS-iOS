# 代码评审

> 评审分两层：**自动门禁**（机器，客观、必须全绿）+ **独立评审**（Agent 或人类，主观判断）。
> 任何一层不通过都不得请求合并。

## 1. 为什么需要「独立」评审

实现 Agent 有一个致命弱点：**它倾向于为自己的实现找理由**。同一上下文里的「自查」
无法发现设计层面的错误。因此：

- Reviewer 必须是**新会话**，只接收 ticket + diff + 规范，不接收实现者的思路。
- Reviewer **只读**，避免「评审时顺手改代码」导致职责混淆。
- Reviewer 的结论必须是可验证的事实（命令输出、代码位置），不是感觉。

## 2. 第一层：自动门禁

| 检查 | 命令 / 来源 | 阻断 |
| --- | --- | --- |
| 格式 | `swiftformat --lint .` | ✅ |
| 静态分析 | `swiftlint --strict`（0 warning） | ✅ |
| 构建 | `xcodebuild -scheme MarketUS -destination 'iOS Simulator'` | ✅ |
| 单元测试 | `xcodebuild test -only-testing:...Unit` | ✅ |
| 快照测试 | `xcodebuild test -only-testing:...Snapshot` | ✅ |
| 覆盖率 | 变更行 ≥ 80%，模块 ≥ 70%，不得下降 | ✅ |
| 密钥扫描 | `gitleaks detect` | ✅ |
| 依赖审查 | 新增/升级依赖需 ADR | ✅ |
| 文档链接 | 失效相对链接 | ⚠️ 警告（Phase 2） |
| 性能回归 | 启动/滚动基准超阈值 | ⚠️ 警告（Phase 3） |

> Agent 不得修改这些检查的配置或阈值来「修好」CI。

## 3. 第二层：独立评审

### 3.1 评分维度

| 维度 | 权重 | 关注点 |
| --- | --- | --- |
| 正确性 | 40% | 验收标准是否真的被满足；边界、错误路径、并发 |
| 测试有效性 | 20% | 测试能否失败；断言是否有效；回归测试是否存在 |
| 架构一致性 | 15% | 分层、依赖方向、是否重复实现已有能力 |
| 安全与隐私 | 10% | 密钥、日志、HTTPS、权限、隐私清单 |
| 可维护性 | 10% | 命名、规模、耦合、可读性 |
| UX / 无障碍 | 5% | Dynamic Type、深色模式、VoiceOver、加载/错误态 |

风格问题（缩进、换行、命名大小写）**一律交给 linter**，评审不讨论。

### 3.2 严重级别

| 级别 | 定义 | 处理 |
| --- | --- | --- |
| **P0** | 安全漏洞、数据丢失、密钥泄漏、越权、崩溃主路径 | 立即阻断 + 通知安全 owner |
| **P1** | 验收标准未满足、逻辑错误、缺少必要测试、并发缺陷 | 阻断，必须修复 |
| **P2** | 可维护性问题、架构越层、性能隐患 | 建议本轮修复，或明确转 ticket |
| **P3** | 命名建议、注释、可选优化 | 不阻断，作者决定 |

`APPROVE` 的前提：**P0 / P1 为 0**，且 P2 均已修复或已建后续 ticket。

### 3.3 Verdict 格式

```markdown
## Verdict: REQUEST_CHANGES

## Blockers (P1)
- [ ] `Packages/MarketUSKit/Sources/FeatureWatchlist/WatchlistViewModel.swift:88`
      并发刷新时 `isLoading` 不会复位（cancel 分支未走 defer）
      依据：L74–L96 的三个 return 路径只有一条复位；测试未覆盖取消场景

## Major (P2)
- [ ] `.../WatchlistView.swift:41` 行点击区 32×32，低于 44×44（HIG）

## Nits (P3)
- [ ] `.../WatchlistViewModel.swift:12` `vm` 缩写建议 `viewModel`

## 已验证
- `make lint` → 0 warnings
- `make test` → 通过（独立运行，未引用 PR 自述）

## 需人类判断
- 拖拽动画曲线是否与设计稿一致（无录屏）
```

## 4. 评审流程

1. CI 全绿后 Reviewer 介入（CI 红时不评审，直接退回）。
2. 读 ticket 验收标准 → 读 PR 验收对照表 → 读 diff（先看测试再看实现）。
3. 对存疑处**本地复跑**或写最小复现，不靠推理下结论。
4. 输出 verdict（格式固定），给出可执行的修改建议。
5. 实现者逐条回复（已修复 / 不修的理由 / 转 ticket），然后重新请求评审。
6. 最多 2 轮；第 3 轮仍有 P1 → 升级人类。

## 5. iOS 专项检查清单

评审时按此清单扫一遍，命中即记 P1/P2：

**并发与生命周期**

- [ ] `@MainActor` 隔离是否正确，是否有后台线程触碰 UI 状态
- [ ] `Task` 是否可取消；视图消失后是否继续持有并写入状态
- [ ] `AsyncStream` / 闭包是否造成循环引用（`[weak self]` 是否必要且正确）
- [ ] 连续触发（快速点击/下拉）是否去重或串行化
- [ ] `Sendable` 违规是否被 `@unchecked` 掩盖

**正确性**

- [ ] 空列表、单元素、超长列表、重复 key 的行为
- [ ] 错误路径：超时、401、429、解码失败、离线
- [ ] 时区/夏令时/交易日历处理（美股行情必须用 `America/New_York`）
- [ ] 数值精度：价格用 `Decimal`，不用 `Double` 做金额累加
- [ ] 本地化：字符串是否走 String Catalog，复数/日期格式是否用 Formatter

**性能**

- [ ] 列表是否使用稳定 `id`，避免整表重绘
- [ ] 是否有主线程 IO / JSON 解码 / 图片处理
- [ ] 高频行情是否做了节流与合并
- [ ] 是否引入不必要的 `@Published` 全量刷新

**UI / 无障碍**

- [ ] Dynamic Type（含 AX 尺寸）不截断
- [ ] 深色模式对比度；涨跌色可配置
- [ ] VoiceOver 标签/值/操作（含自定义操作）
- [ ] 点击区 ≥ 44×44pt；支持 Reduce Motion

**安全**

- [ ] 无硬编码密钥、无 `print` 输出 token/用户数据
- [ ] 请求走 HTTPS，ATS 未被放宽
- [ ] Keychain 可访问级别正确
- [ ] 隐私清单 `PrivacyInfo.xcprivacy` 与新增 SDK 一致
- [ ] 无 `WKWebView` 任意 URL 加载 / JS 注入

**工程**

- [ ] 未越过模块依赖（Feature 不 import Data/Networking）
- [ ] 未使用 iOS 17+ 专属 API（`@Observable`、SwiftData、`ContentUnavailableView`、
      `.onChange(of:initial:)` 等）；确需使用时必须有 `@available` 降级路径
- [ ] 状态容器是 `ObservableObject` + `@Published`，且只在主线程写
- [ ] 未新增未批准依赖
- [ ] 文件/函数规模超限否
- [ ] 公开 API 有 `///` 文档

## 6. 评审礼仪与效率

- 一次给全：不要挤牙膏式一条条提，避免多轮往返。
- 意见可执行：指出位置、给出依据、提供方向，不写「这里不太对」。
- 区分「必须」与「偏好」：偏好一律 P3，不阻塞。
- 不做无意义表扬，也不做人身评价；只谈代码与证据。
- 单次评审 ≤ 400 行 diff；超过则要求拆分（这是硬性要求，不是建议）。

## 7. 人类评审 vs Agent 评审

| 维度 | Agent Reviewer | Human Reviewer |
| --- | --- | --- |
| 逻辑/并发/测试 | ✅ 主力 | 抽查 |
| 架构一致性 | ✅ | ✅ 最终裁定 |
| 产品意图 / 设计还原 | ❌ | ✅ |
| 合规 / 法务 / 行情授权 | ❌ | ✅ |
| 真机体验（手感、性能） | ❌ | ✅ |
| 风险接受度 | ❌ | ✅ |

**人类批准不可被 Agent 批准替代。** Agent 的 `APPROVE` 只是「请求合并」的前置条件。

## 8. 评审失效的信号

出现以下情况说明评审在退化，需要调整流程：

- 连续多个 PR 一次通过但合并后出 bug → 评审过浅或测试无效。
- Reviewer 大量评论风格问题 → 应加强 linter 而非增加评审。
- 同一类 P1 反复出现 → 应写进 `AGENTS.md` 或加自动检查。
- 评审轮次 > 2 成为常态 → ticket 太模糊或实现 agent 能力不足。
