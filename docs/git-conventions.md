# Git 规范

## 1. 分支模型

Trunk-based：`main` 始终可发布，分支存活 ≤ 3 天。

| 分支 | 用途 | 创建者 | 命名示例 |
| --- | --- | --- | --- |
| `main` | 唯一长期分支，受保护 | — | — |
| `ai/<ticket>-<slug>` | Agent 实现 | Implementer | `ai/123-watchlist-reorder` |
| `human/<ticket>-<slug>` | 人类实现 | 人类 | `human/140-settings-theme` |
| `hotfix/<version>-<slug>` | 线上紧急修复 | 人类 | `hotfix/1.0.2-quote-crash` |

规则：

- 分支名全小写、连字符分隔、≤ 50 字符，slug 用英文（便于脚本与 URL）。
- **禁止** `develop`、`release/*`、长期 feature 分支。
- 一个 ticket 一个分支；禁止一个分支开多个 PR。

## 2. 工作树（worktree）

并行 Agent 必须各自使用独立 worktree，避免共享工作目录互相覆盖：

```bash
git fetch origin main
git worktree add ".worktrees/123-watchlist-reorder" -b "ai/123-watchlist-reorder" origin/main
cd ".worktrees/123-watchlist-reorder"

# 完成后清理
cd -
git worktree remove ".worktrees/123-watchlist-reorder"
git branch -d ai/123-watchlist-reorder    # 合并后
```

`.worktrees/` 已在 `.gitignore` 中。

## 3. 提交信息

[Conventional Commits](https://www.conventionalcommits.org/)，与 squash 后的 main 历史一致。

```
<type>(<scope>): <summary>

<body：为什么这么改，权衡是什么>

Refs: #123
Agent-Run: run-20261103-142233-impl02
```

- `type`：`feat` `fix` `refactor` `perf` `test` `docs` `chore` `build` `ci` `revert`
- `scope`：模块名小写（`watchlist` `quotes` `networking` `design-system` `ci`）
- `summary`：≤ 72 字符，祈使句，小写开头，不加句号
- `body`：每行 ≤ 72 字符；解释「为什么」，不复述 diff
- footer：`Refs: #<ticket>`；Agent 运行附 `Agent-Run`；破坏性变更用 `BREAKING CHANGE:`

示例：

```
feat(watchlist): support drag-to-reorder

Persist order in the local file store so the ranking survives relaunch. Roll back on
interruption to avoid partial writes.

Refs: #123
Agent-Run: run-20261103-142233-impl02
```

```
fix(quotes): dedupe concurrent refresh requests

Three rapid pull-to-refresh gestures started three requests and left one
spinner hanging. Coalesce in-flight requests and reset state in defer.

Refs: #130
```

禁止：`update`、`fix bug`、`wip`、`asdf`、纯 emoji、无 scope 的巨型提交。

## 4. 提交粒度

- 一次提交 = 一个可独立理解、可编译的变更。
- 测试与实现同一提交（保证任一 commit 可 bisect）。
- **禁止**把格式化/重命名混进功能提交（单独 `chore` 提交或 PR）。
- 提交前必须本地通过：`make format && make lint && make test`。

## 5. PR 规范

| 项 | 规则 |
| --- | --- |
| 标题 | 与 squash 提交标题一致，含 `(#ticket)` |
| 目标分支 | `main` |
| 规模 | 建议 ≤ 400 行；> 800 行直接要求拆分 |
| 文件数 | ≤ 25 |
| 描述 | 必须用 `.github/PULL_REQUEST_TEMPLATE.md`，填满所有必填段 |
| 标签 | `ai:generated`（Agent）+ `risk:*` + `size:*` |
| 草稿 | 未通过本地验证前用 Draft PR |
| 自动化 | CI 全绿后才请求评审 |

PR 中禁止：

- 直接粘贴大段日志（附 artifact 链接）
- 无证据的「已测试」
- 与 ticket 无关的重构
- 自批准、自行 resolve 评审意见

## 6. 同步与冲突

```bash
# 落后 main：在自己的分支上 rebase
git fetch origin main
git rebase origin/main

# 解决冲突后继续
git add <files>
git rebase --continue

# 推送（已开 PR 且已请求评审时，禁止 force push）
git push
```

规则：

- **force push 仅允许**在自己尚未请求评审、且无其他人基于该分支工作时使用
  （`--force-with-lease`，绝不 `--force`）。
- 已进入 `status:in-review` 的分支：**只追加 commit**，不 rewrite。
- 合并冲突涉及语义（不是行冲突）→ 停下来问人类，不要猜。
- 发生冲突后必须重跑 `make verify`，并在 PR 中说明冲突解决方式。

## 7. 合并方式

- **仅 squash merge**，保证 main 线性可读。
- squash 标题 = PR 标题（Conventional Commits 格式）。
- 合并后删除分支。
- 合并后验证 main 的 CI；红则立即 revert（见 `merge-policy.md` §回滚）。

## 8. 回滚

```bash
# 首选：revert PR（保留历史，可再次合入）
gh pr revert 124

# 仅在事故且 revert 不可行时，人类手动修复并走 hotfix 流程
```

Agent **不得**执行 revert 到 `main`（属于合并类操作），只能提议。

## 9. 提交签署与身份

- Agent 使用独立 bot 身份（如 `marketus-agent[bot]`），提交必须可辨识。
- 建议开启 signed commits（Phase 2）；未开启前，`Agent-Run` footer 作为审计依据。
- 禁止使用人类身份的 token 代提交（审计会失真）。

## 10. 常见错误

| 错误 | 后果 | 正确做法 |
| --- | --- | --- |
| 在共享目录直接改代码 | 并行 Agent 互相覆盖 | 用 worktree |
| `git add -A` 带入无关文件 | 评审噪声、泄密风险 | 逐个 `git add <path>` |
| 提交 `.env` / `Secrets.xcconfig` | 密钥泄漏 | 已在 `.gitignore`，立即轮换密钥 |
| 提交 `MarketUS.xcodeproj` | 二进制冲突 | 只提交 `project.yml` |
| 在 PR 里 rebase + force push | 评审 diff 失真 | 追加 commit |
