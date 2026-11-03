# 术语表

统一用词，避免「工单 / issue / ticket」「评审 / review / CR」混用导致 Agent 理解偏差。

## 流程术语

| 术语 | 英文 | 含义 |
| --- | --- | --- |
| Ticket | issue | 唯一的任务来源，GitHub Issue |
| DoR | Definition of Ready | 可被 Agent 领取的最低信息标准 |
| DoD | Definition of Done | 可请求合并的完成标准 |
| Claim | 领取 | 通过 `status:claimed` 标签 + 租约取得独占权 |
| Lease | 租约 | 领取的有效期（默认 120 分钟），需心跳续租 |
| Worktree | 工作树 | `git worktree`，每个 ticket 独立的物理目录 |
| Gate | 门禁 | 必须通过的检查（lint/build/test/覆盖率/密钥扫描） |
| Verdict | 评审结论 | `APPROVE` / `REQUEST_CHANGES` |
| Packet | 合并说明书 | 请求人工合并时附的证据包 |
| Protected path | 受保护路径 | Agent 无权修改的文件/目录 |
| Escalation | 升级 | 停止执行并请求人类决策 |
| Run | 一次 Agent 运行 | 对应 `.agent/runs/<ticket>/` 的一次执行记录 |
| Dispatcher | 调度器 | 扫描与派发 ticket 的程序 |
| Implementer | 实现 Agent | 写代码、开 PR 的 Agent |
| Reviewer | 评审 Agent | 只读、独立上下文的评审者 |

## 严重级别

| 级别 | 含义 | 是否阻断合并 |
| --- | --- | --- |
| P0 | 安全/数据丢失/崩溃主路径 | ✅ 立即 |
| P1 | 功能错误/缺测试/并发缺陷 | ✅ |
| P2 | 可维护性/架构/性能隐患 | 建议修复，可转 ticket |
| P3 | 风格/命名/可选优化 | ❌ |

## 风险等级

| 级别 | 含义 | 审批 |
| --- | --- | --- |
| low | 局部 UI/文案，易回滚 | 1 位人类 |
| medium | 跨模块、网络、缓存 | 1 位 owner + 手工验证 |
| high | 鉴权、支付、隐私、迁移、签名 | 2 位人类 + 回滚演练 |

## 状态标签

| 标签 | 含义 |
| --- | --- |
| `status:ready` | 通过 DoR，可领取 |
| `status:claimed` | 已被领取（租约内） |
| `status:in-progress` | 实现中 |
| `status:in-review` | PR 已开，等评审 |
| `status:awaiting-human` | 已通过评审，等人类合并 |
| `status:blocked` | 受阻，需人类介入 |
| `status:done` | 合并并验证完成 |

## 模块缩写

| 名称 | 含义 |
| --- | --- |
| Core | `MarketUSCore`，领域模型与协议 |
| Networking | `MarketUSNetworking`，HTTP/WebSocket |
| Persistence | `MarketUSPersistence`，文件存储/Keychain |
| Data | `MarketUSData`，仓储实现与映射 |
| DesignSystem | `MarketUSDesignSystem`，Token 与组件 |
| Observability | `MarketUSObservability`，日志/分析/性能 |
| Feature* | 具体功能模块，仅依赖 Core/DesignSystem/Observability |

## 常见缩写

| 缩写 | 全称 | 说明 |
| --- | --- | --- |
| ADR | Architecture Decision Record | 架构决策记录，见 `docs/adr/` |
| ATS | App Transport Security | iOS 网络安全策略 |
| ETag | Entity Tag | HTTP 缓存校验 |
| HIG | Human Interface Guidelines | Apple 设计规范 |
| PII | Personally Identifiable Information | 个人身份信息 |
| Mock | 模拟行情 | 确定性假数据，不走真实网络 |
| Provider | 数据源适配器 | 实现 `QuoteFetching`/`QuoteStreaming` 的具体来源 |
| Scenario | 行情场景 | `bull`/`stale`/`disconnect` 等可复现状态 |
| SPM | Swift Package Manager | 包管理 |
| VM | ViewModel | 视图模型 |
