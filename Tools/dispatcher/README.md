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
| 3c（当前） | `run` 单次巡检、Reviewer 自动评审、合并后收尾、launchd 模板 |
| 3d | 2 并发 + 路径锁 + 指标 |

## 自测

```bash
python3 -m py_compile Tools/dispatcher/dispatcher.py
python3 Tools/dispatcher/dispatcher.py status
```


## 3c：巡检与 Reviewer

```bash
# 单次巡检（postmerge → 派发 1 张 → 评审 1 个 PR）
python3 Tools/dispatcher/dispatcher.py run --dry-run
python3 Tools/dispatcher/dispatcher.py run

# 单独评审 / 收尾
python3 Tools/dispatcher/dispatcher.py review --pr 18 --dry-run
python3 Tools/dispatcher/dispatcher.py review --pr 18 --execute
python3 Tools/dispatcher/dispatcher.py postmerge --dry-run
```

Reviewer 以 `DSH_PERMISSION_MODE=read-only` 运行；因为是同一个 GitHub 账号，
GitHub 不允许 approve 自己的 PR，所以 Dispatcher 用 **评论 verdict + `merge:requested` 标签**
表示通过，最终仍由人类合并。

### launchd（不会自动安装）

```bash
sed -e "s|__REPO__|$PWD|g" -e "s|__HOME__|$HOME|g" \
  Tools/dispatcher/com.marketus.dispatcher.plist.template \
  > ~/Library/LaunchAgents/com.marketus.dispatcher.plist

launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.marketus.dispatcher.plist
launchctl kickstart -k gui/$UID/com.marketus.dispatcher   # 立即跑一次
launchctl bootout gui/$UID/com.marketus.dispatcher        # 停止
```

日志：`~/Library/Logs/marketus-dispatcher.log`。
启用后它会真的领取 `status:ready` 的 ticket；先确认 `.agent/config.yml` 与凭据无误。


## 通知

仅在 Reviewer APPROVE 后发送「可合并」macOS 通知（含 PR 编号）；PR 创建时不再通知。
关闭：`MARKETUS_NOTIFY=0`。非 macOS 平台自动跳过。


## 自动修复轮

Reviewer 给出 `REQUEST_CHANGES` 后，下一轮 `run` 会在后台启动 `repair --pr N`
（每轮最多一个，日志追加到 `.agent/runs/repair-N.out`）：
Implementer 在 PR 分支的临时 worktree 修复、push，issue 回到 `status:in-review`。
每个 PR 最多 2 轮；仍失败则 `status:blocked` 并发送通知。PR 创建/修复本身不通知，
只有 Reviewer APPROVE 后才通知合并。

review 与 repair 使用各自独立的 per-PR 锁（`.agent/state/review-N.lock` /
`.agent/state/repair-N.lock`），两者可同时运行；同一 PR 重复触发会直接跳过。
某个 PR 的 review 已在运行时，`run` 会继续尝试本轮下一张 PR，而不是空转。


## 漏审兜底

`run` 会补审满足以下条件的 PR：`ai/*`、issue 为 `status:in-progress`、
当前 head 没有 `dispatcher-review` 标记、创建超过 10 分钟（严格大于）。用于手动创建或标签未更新的 PR。

- Workflow watcher dummy PR（验证后删除）


## 实时观测

```bash
python3 Tools/dispatcher/dispatcher.py observe
python3 Tools/dispatcher/dispatcher.py observe --watch
```

显示 launchd 状态、当前 agent/dispatcher PID 与耗时、open PR、最近 loop 与 run 日志。


## Loop Trace

每次 `run` 生成一个 `loop_id`（UTC 时间戳 + 短 uuid），追加写入
`.agent/loops/<loop_id>/timeline.jsonl`。事件为 JSONL，包含
`ts / loop_id / event / step / status / duration_ms / details`，
覆盖 `postmerge/watch/repair/dispatch/review/planner` 六个阶段；
implementer/reviewer/repair 子进程额外记录 `agent_start` / `agent_end`
（role、pid、exit_code、duration_ms）。后台 repair 的 `step_end` 与
`loop_end` 由子进程在结束时写入，父循环不会提前把 loop 标成 `ok`。
单条事件失败不会中断调度，但会把该 loop 标记为 `failed`。

```bash
# 最近 loop：id、开始时间、耗时、状态、步骤
python3 Tools/dispatcher/dispatcher.py loop list
python3 Tools/dispatcher/dispatcher.py loop list --limit 5

# 单个 loop 的完整时间线与每步耗时
python3 Tools/dispatcher/dispatcher.py loop show <loop_id>
```
