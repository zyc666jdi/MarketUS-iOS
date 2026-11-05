# Dispatcher（Phase 3a · 只读）

设计文档：[`docs/dispatcher.md`](../../docs/dispatcher.md)
选型记录：[`ADR-0005`](../../docs/adr/0005-dispatcher-architecture.md)

`status` / `plan` 只读；`once` 默认 dry-run，只有加 `--execute` 才会写 GitHub 并调用 `dsh headless`。

## 用法

```bash
# 各状态数量 + 活跃 ticket
python3 Tools/dispatcher/dispatcher.py status

# 列出 status:ready 的 ticket，逐条跑 DoR 校验
python3 Tools/dispatcher/dispatcher.py plan

# 机器可读输出
python3 Tools/dispatcher/dispatcher.py plan --json

# 手动派发（默认 dry-run；--execute 才会真正执行）
python3 Tools/dispatcher/dispatcher.py once --issue 8
python3 Tools/dispatcher/dispatcher.py once --issue 8 --execute --timeout 90
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

## 凭据（`dsh headless`）

`dsh headless` 不使用 Web 的交互式凭据，需显式提供 `DEEPSEEK_API_KEY`。按优先级：

1. 当前 shell：`export DEEPSEEK_API_KEY=sk-...`
2. 600 权限文件：`~/.config/marketUS/deepseek.env`（或 `AGENT_ENV_FILE` 指定）
3. macOS Keychain：

```bash
security add-generic-password -a "$USER" -s DEEPSEEK_API_KEY -w 'sk-...' -U
```

Dispatcher 只在需要时读取，不打印、不落盘。使用 `AGENT_CMD="codex exec ..."` 时无需该 key。

## 后续阶段

| 阶段 | 能力 |
| --- | --- |
| 3a（当前） | `status` / `plan`，只读 |
| 3b（已实现） | `once --issue N --execute`：claim、worktree、`dsh headless` 实现、校验 PR |
| 3c | launchd 常驻、Reviewer 自动评审、合并后收尾 |
| 3d | 2 并发 + 路径锁 + 指标 |

## 自测

```bash
python3 -m py_compile Tools/dispatcher/dispatcher.py
python3 Tools/dispatcher/dispatcher.py status
```
