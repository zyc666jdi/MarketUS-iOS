# 文档索引

本目录是 MarketUS iOS 的**工程规范与工作流文档**。`AGENTS.md` 是行为契约，
这里是它的展开与依据。

## 阅读路径

**新加入的工程师 / 新接入的 Agent，按此顺序读：**

1. [`../README.md`](../README.md) —— 项目是什么、文件怎么放
2. [`../AGENTS.md`](../AGENTS.md) —— 红线与工作循环
3. [`architecture.md`](architecture.md) —— 分层与模块边界
4. [`agent-workflow.md`](agent-workflow.md) —— ticket 怎么流转
5. [`ticket-protocol.md`](ticket-protocol.md) —— ticket 怎么写才能被领取
6. [`code-review.md`](code-review.md) —— 评审标准
7. [`merge-policy.md`](merge-policy.md) —— 谁能合并

**按角色：**

| 角色 | 必读 |
| --- | --- |
| 产品 / 需求方 | `ticket-protocol.md` §DoR、`agent-workflow.md` §人工介入点 |
| iOS 工程师 | `architecture.md`、`code-review.md`、`testing-strategy.md` |
| 评审人 | `code-review.md`、`merge-policy.md` |
| Agent 集成方 | `../AGENTS.md`、`agent-workflow.md`、`../.agent/config.yml` |
| 安全 / 合规 | `security-and-compliance.md`、`merge-policy.md` §受保护路径 |

## 文档清单

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [architecture.md](architecture.md) | 分层架构、模块依赖、并发模型、网络/存储设计 | ✅ |
| [agent-workflow.md](agent-workflow.md) | 状态机、端到端时序、并发与失败处理、度量 | ✅ |
| [ticket-protocol.md](ticket-protocol.md) | 字段规范、DoR、标签体系、领取协议、示例 | ✅ |
| [git-conventions.md](git-conventions.md) | 分支、提交、PR、冲突与回滚 | ✅ |
| [code-review.md](code-review.md) | 双层评审、评分维度、iOS 缺陷清单、verdict 格式 | ✅ |
| [merge-policy.md](merge-policy.md) | 合并前置条件、风险分级、受保护路径、hotfix | ✅ |
| [testing-strategy.md](testing-strategy.md) | 测试金字塔、覆盖率政策、flaky 处理 | ✅ |
| [ci-cd.md](ci-cd.md) | 流水线阶段、必需检查、密钥与分发 | ✅ |
| [security-and-compliance.md](security-and-compliance.md) | 威胁模型、注入防护、行情数据合规 | ✅ |
| [data-sources.md](data-sources.md) | Mock 行情方案、WebSocket 契约、真实源对比 | ✅ |
| [dependencies.md](dependencies.md) | 依赖白名单、许可证、准入与升级策略 | ✅ |
| [infrastructure.md](infrastructure.md) | 公开仓库设置、分支规则、Actions 权限、CI 成本 | ✅ |
| [dispatcher.md](dispatcher.md) | Phase 3 Dispatcher 设计（状态机/租约/AgentRunner/灰度） | ✅ |
| [implementation-plan.md](implementation-plan.md) | Phase 0→4 落地清单与脚手架代码 | ✅ |
| [glossary.md](glossary.md) | 术语与标签释义 | ✅ |
| [adr/](adr/README.md) | 架构决策记录 | ✅ |
| [runbooks/](runbooks/) | 卡住、CI 失败的处置手册 | ✅ |

## 维护规则

- 文档与代码同 PR 更新；**架构或流程变更必须新增 ADR**（见 [`adr/README.md`](adr/README.md)）。
- `docs/adr/`、`.agent/`、`.github/`、`AGENTS.md` 属于**受保护路径**：Agent 不得直接提交。
- `docs/` 其余部分 Agent 可在 PR 中更新（例如架构变更时同步 `architecture.md`），
  但 CODEOWNERS 要求 `@ios-leads` 人类审批。
- 文档中的命令必须可复制执行；失效链接由 CI 的 `docs-lint` 检查（Phase 2 引入）。
