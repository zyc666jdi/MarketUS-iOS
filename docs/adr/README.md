# 架构决策记录（ADR）

## 什么是 ADR

一个 ADR 记录**一次重要的技术决策**：当时的背景、考虑过的选项、最终选择、以及代价。
它的价值在于「三个月后有人问为什么这么做」时有据可查——尤其是 Agent 接手时。

## 什么时候必须写

- 引入/移除第三方依赖
- 改变模块边界或分层规则
- 改变数据存储、迁移策略
- 改变并发模型、网络协议、实时通道方案
- 改变 CI/发布/签名流程
- 改变 Agent 权限或工作流规则

**不需要写**：普通功能实现、UI 细节、单文件重构。

## 命名与状态

```
docs/adr/NNNN-<kebab-case-title>.md
```

状态：`Proposed` → `Accepted` → `Superseded by ADR-XXXX` / `Deprecated`

- 编号连续，不复用。
- 被取代时保留原文件，只改状态并链接新 ADR。
- 修改已 `Accepted` 的 ADR 内容 = 新开一个 ADR。

## 流程

1. 复制 [`template.md`](template.md)。
2. 填背景、选项、决策、后果。
3. 在 PR 中请求 `@ios-leads` 评审。
4. 合并后状态改为 `Accepted`，并在相关文档中引用。

> `docs/adr/` 属于受保护路径：Agent 可以**提议**（评论或 draft），但不能直接提交。

## 索引

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| [0001](0001-ios-tech-stack.md) | iOS 技术栈与模块化方案（最低 iOS 16） | Proposed |
| [0002](0002-agent-model.md) | AI Agent 运营模型（有写权、无合并权） | Proposed |
| [0003](0003-market-data.md) | 行情数据策略：Mock 优先，真实源可选 | Proposed |
| [0004](0004-dependencies.md) | 依赖基线：运行时零第三方依赖 | Proposed |
| [0005](0005-dispatcher-architecture.md) | Dispatcher：本地守护进程 + dsh headless | Proposed |
