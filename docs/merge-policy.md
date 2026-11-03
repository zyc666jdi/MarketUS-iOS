# 合并策略

> **Agent 永不合并。人类是唯一合法的合并者。** 本文件定义「请求人工合并」的标准动作
> 与人类的验收标准。

## 1. 合并前置条件（全部满足）

| # | 条件 | 校验 |
| --- | --- | --- |
| 1 | 关联 ticket 且 `Fixes #n` 在 PR body 中 | 人工 / CI |
| 2 | 必需检查全绿（lint/build/unit/snapshot/secret-scan） | 分支保护 |
| 3 | 独立 Reviewer 已 `APPROVE`，P0/P1 为 0 | GitHub Review |
| 4 | 人类审批人数达到风险要求 | CODEOWNERS + 分支保护 |
| 5 | 分支与 `main` 同步（`require_up_to_date`） | 分支保护 |
| 6 | 所有 review thread 已 resolved | 分支保护 |
| 7 | 无 `merge:hold` 标签 | 人工 |
| 8 | 变更规模 ≤ 800 行（软限 400） | 人工 |
| 9 | PR 模板必填段完整（含证据、回滚方案） | 人工 |
| 10 | 未修改受保护路径，或已获得对应 owner 批准 | CODEOWNERS |

## 2. 风险分级与审批

| 风险 | 判定标准 | 人类审批 | 额外要求 |
| --- | --- | --- | --- |
| `low` | 局部 UI/文案/单模块内部逻辑，易回滚 | 1 位 | CI 绿 + Agent 评审通过 |
| `medium` | 跨模块、网络协议、缓存策略、性能相关 | 1 位 owner | 需真机/模拟器手工验证记录 |
| `high` | 鉴权、支付、隐私、数据迁移、签名、发布配置 | 2 位（含 `ios-leads`） | 手工验证 + 回滚演练 + 可能需 ADR |

标签由 ticket 的 `risk:*` 继承；实现过程中发现风险升级 → **必须改标签并说明**。

## 3. 受保护路径

以下路径**只能由人类提交**，或必须由 CODEOWNERS 指定的人类专属审批：

| 路径 | 原因 |
| --- | --- |
| `AGENTS.md`、`.agent/**` | 定义 Agent 权限边界，Agent 不能自我扩权 |
| `.github/**`（workflows、CODEOWNERS、模板、labels） | 影响门禁本身 |
| `project.yml`、`Packages/MarketUSKit/Package.swift` | 依赖与构建结构 |
| `**/*.entitlements`、`**/Info.plist`、`**/PrivacyInfo.xcprivacy` | 权限、隐私、审核合规 |
| `Tools/signing/**`、`*.xcconfig`（含签名/环境） | 证书与密钥 |
| `docs/adr/**` | 架构决策需人类确认 |

Agent 命中受保护路径 → 立即停止，输出：

```markdown
## 🛑 受保护路径

需要修改：`project.yml`（原因：新增 test target）
按 `docs/merge-policy.md` §3，我无权提交此类变更。
建议：由 @ios-leads 直接提交，或授予本 PR 人类专属审批。
我已在分支中准备好补丁，可粘贴 diff。
```

## 4. 请求人工合并的标准动作

实现 Agent 在评审通过后执行：

```bash
# 1) 确认 CI 与审批
gh pr checks 124
gh pr view 124 --json reviewDecision,mergeable,statusCheckRollup

# 2) 打标 + 指派
gh pr edit 124 --add-label "merge:requested" --add-reviewer YOUR_ORG/ios-owners
gh issue edit 123 --add-label "status:awaiting-human" --remove-label "status:in-review"

# 3) 合并说明书
gh pr comment 124 --body-file .agent/runs/123/merge-request.md
```

合并说明书模板：

```markdown
## 🙋 请求人工合并

| 项 | 值 |
| --- | --- |
| Ticket | #123 [Feature] 自选股拖拽排序 |
| 风险 | low |
| 规模 | 3 文件 / +86 −12 |
| CI | ✅ lint / build / unit(128) / snapshot(24) / secret-scan |
| Agent 评审 | ✅ APPROVE（run-20261103-…-review01） |
| 尝试轮次 | 1 / 3 |

### 验收标准对照
- [x] 拖拽改变顺序 → `WatchlistReorderTests.test_move_*`（4 个用例）
- [x] 重启保持 → `WatchlistPersistenceTests`
- [x] 中断回滚 → `WatchlistReorderTests.test_interrupt_rollsBack`

### 回滚方案
squash revert 本 PR，无数据迁移，无需回滚脚本。

### 需要你确认
1. 拖拽动画曲线是否与设计稿一致（附录屏）
2. 是否同意 P3 意见留到后续 ticket（已建 #135）
```

**Agent 到此停止。** 不发「Merge 一下」的私聊轰炸；通知走 `#ios-review`。

## 5. 人类合并操作清单

```bash
gh pr view 124 --web     # 或直接用 GUI
```

1. 读 PR 描述与合并说明书，确认与 ticket 一致。
2. 抽查 diff：测试是否有效、有无夹带、有无密钥。
3. 对 UI 变更看截图/录屏；medium/high 风险必须本地跑一次。
4. 确认 CI 绿、审批数达标、无 `merge:hold`。
5. **Squash and merge**，标题符合 Conventional Commits，删除分支。
6. 合并后确认 main 的 CI 绿；红了立即 revert（见 §8）。
7. 确认 `status:done`（调度器监听 merged 事件自动打；无调度器时手动补）；
   high 风险变更同步到发布说明。

## 6. 合并方式

- **仅 squash merge**（分支保护强制），main 保持线性。
- squash 标题 = PR 标题；body 保留 PR 摘要与 `Refs`。
- 合并后删除远端分支；本地 worktree 由收尾流程清理。
- **禁止**：merge commit、rebase merge、直接 push main、`--admin` 绕过保护。

## 7. 冻结窗口

- 发版前 24h 至提审通过：`merge:hold` 生效，只允许 hotfix 与文案。
- 线上事故处理期间：所有合并暂停，事故负责人可单独放行。
- 冻结由人类打标/撤标，Agent 不得自行移除。

## 8. 回滚

| 场景 | 动作 | 负责人 |
| --- | --- | --- |
| 合并后 main CI 红 | `gh pr revert <n>`，或手动 revert 提交 | 人类 |
| 已发版出现严重问题 | 关闭服务端开关 → 发 hotfix | 人类 |
| 数据迁移出错 | 停止写入 → 走迁移回滚脚本（必须预先演练） | 人类 |

Agent 可以**提议**回滚并准备好 revert 分支，但不得执行合并类操作。

## 9. Hotfix 流程（人类主导，Agent 可辅助）

1. 从最近 tag 切 `hotfix/<version>-<slug>`。
2. 最小修复 + 回归测试；**禁止**夹带重构。
3. CI 绿 + 1 位 owner 审批即可合并。
4. 合并回 `main`，打 tag，走 TestFlight。
5. 24h 内补 ADR / 复盘，并检查同类问题。

## 10. 发布与版本

- 版本号语义化：`MAJOR.MINOR.PATCH`；build 号单调递增。
- 发布由人类触发（Phase 3 可自动化到 TestFlight，但仍需人类点按钮）。
- 每次发布必须有 changelog（从 Conventional Commits 生成）。
- App Store 提审清单见 `security-and-compliance.md`。

## 11. 审计

每次合并应能回答：

- 谁批准的？何时？
- CI 在哪个 commit 上通过？
- 对应 ticket 与 Agent run 是哪个？
- 若回滚过，为什么？

这些信息分布在 PR、Actions、`.agent/runs/` 归档中，均为永久或 90 天保留。
