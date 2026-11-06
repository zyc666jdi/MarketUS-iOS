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
