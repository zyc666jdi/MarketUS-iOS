# Dispatcher（Phase 3a · 只读）

设计文档：[`docs/dispatcher.md`](../../docs/dispatcher.md)
选型记录：[`ADR-0005`](../../docs/adr/0005-dispatcher-architecture.md)

当前阶段只做**发现与校验**，不写 GitHub、不领取 ticket、不调用 Agent。

## 用法

```bash
# 各状态数量 + 活跃 ticket
python3 Tools/dispatcher/dispatcher.py status

# 列出 status:ready 的 ticket，逐条跑 DoR 校验
python3 Tools/dispatcher/dispatcher.py plan

# 机器可读输出
python3 Tools/dispatcher/dispatcher.py plan --json
```

依赖：`gh` 已登录、当前目录是仓库根、`git remote origin` 指向 GitHub。

## DoR 校验范围

- `type:* / area:* / priority:* / risk:*` 四类标签
- `## 验收标准` ≥ 2 条
- `## 范围外` 非空
- 正文包含 `affected_paths`
- 未标记 `security:suspicious`
- 可疑指令粗筛（忽略指令、读 `.ssh`/`.env`、跳过测试、直接合并等）

未通过只会在输出里标 `⛔`，不会修改任何 issue。

## 后续阶段

| 阶段 | 能力 |
| --- | --- |
| 3a（当前） | `status` / `plan`，只读 |
| 3b | `once --issue N`：claim、worktree、`dsh headless` 实现、开 PR |
| 3c | launchd 常驻、Reviewer 自动评审、合并后收尾 |
| 3d | 2 并发 + 路径锁 + 指标 |

## 自测

```bash
python3 -m py_compile Tools/dispatcher/dispatcher.py
python3 Tools/dispatcher/dispatcher.py status
```
