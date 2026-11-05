# ADR-0005: Dispatcher 采用本地守护进程 + `dsh headless`

- **状态**：Proposed
- **日期**：2026-11-05
- **决策者**：@zyc666jdi（待确认）
- **相关**：`docs/dispatcher.md`、`docs/agent-workflow.md`、`.agent/config.yml`

## 背景

Phase 3 要把 ticket → 实现 → 评审 → 请求合并串成无人值守链路。约束：

1. iOS 构建与模拟器依赖 **本机 macOS + Xcode 26.6**，云端 Linux 无法替代。
2. GitHub Actions 的 macOS runner 当前持续获取失败（零步骤、15 分钟取消）。
3. 仓库是**公开**的：不能把模型/API 凭据交给任意 fork PR 或不可信事件。
4. 本机已有可非交互执行的 Agent 入口：`dsh --profile headless`（另有 `claude -p`、`codex exec`）。
5. 硬性安全边界：Agent **永不合并**，人类是唯一合并者。

## 选项

| 选项 | 优点 | 缺点 |
| --- | --- | --- |
| A. GitHub Actions 定时 + Agent CLI | 无需常驻机器 | macOS runner 不可用/排队；凭据进 Actions；单 job 6h 上限；公开仓库风险高 |
| B. 本地 daemon + `dsh headless` | 复用本机 Xcode；凭据不出本机；可调试/可审计 | 机器需开机；单点；需要 launchd 运维 |
| C. 云端常驻调度器 | 不依赖个人机器 | 无 macOS 构建环境；成本高；安全面更大 |
| D. 纯人工触发 | 最简单 | 不满足自动化目标 |

## 决策

采用 **B**：

- Dispatcher 以 Python 单文件实现（`Tools/dispatcher/dispatcher.py`），只依赖 `gh` 与标准库。
- 状态源为 GitHub 标签 + 评论；本地 `.agent/state/` 只存租约与 run 索引。
- 实现/评审 Agent 通过 `dsh --profile headless --json` 调用，prompt 走 stdin。
- 每张 ticket 独占 `git worktree` 与 `ai/*` 分支；默认 2 并发。
- 合并由人类完成；Dispatcher 只发 `merge:requested` 并等待。
- 合并检测用本地轮询（`gh pr list --state merged`），不依赖 Actions。
- GitHub Actions 仅在 Phase 3c 作为可选的「合并事件 bookkeeping」补充。

## 后果

**正面**

- 不需要等待 GitHub macOS runner；构建/模拟器与本地一致。
- 模型凭据、SSH key、Xcode 都留在本机，公开仓库不暴露。
- 失败可直接看 `.agent/runs/` 日志与 worktree，调试成本低。

**负面 / 代价**

- 机器关机/休眠时调度停止（可用 launchd + 电源策略缓解）。
- 单实例，无法多机并行；扩展需要分布式锁（未决问题）。
- 需要维护 launchd、日志轮转与磁盘清理。

**需要跟进**

- [ ] Phase 3a：只读 `status` / `plan`（本 PR）
- [ ] Phase 3b：`once --issue` 调 `dsh headless`，人工评审批次
- [ ] Phase 3c：launchd 常驻 + Reviewer + PostMerge
- [ ] 确认 `dsh headless` 在 worktree 中的权限与沙箱参数
- [ ] 评估 Reviewer 只读隔离方式（独立 token / 独立 clone）

## 重新评估的触发条件

- GitHub macOS runner 恢复稳定且成本可接受
- 团队需要多机并行或多仓库调度
- `dsh headless` 的权限模型无法满足只读评审隔离
- 机器无法长期在线（需要迁移到常驻 Mac mini / 专用 runner）
