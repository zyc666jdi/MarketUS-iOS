# 基础设施（公开仓库）

> 仓库形态：**GitHub Public**。这既降低了 CI 成本，也放大了攻击面：
> 任何人都能开 issue、提 fork PR、评论。基础设施必须假设**外部输入不可信**。

## 1. 一次性创建清单

```bash
# 1) 建仓（你也可以先在网页建好，再 add remote）
gh repo create YOUR_ORG/MarketUS-iOS --public --source=. --remote=origin

# 2) 基础设置
gh repo edit --enable-issues --enable-discussions \
  --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false \
  --delete-branch-on-merge

# 3) 标签
yq -r '.[] | [.name, .color, .description] | @tsv' .github/labels.yml \
  | while IFS=$'\t' read -r n c d; do
      gh label create "$n" --color "$c" --description "$d" --force
    done
```

GitHub UI 需要手动开启的项（公开仓库免费）：

- [ ] **Settings → Code security**：Secret scanning、Push protection、Dependabot alerts、Dependabot security updates
- [ ] **Private vulnerability reporting**：开启（配合 `SECURITY.md`）
- [ ] **Settings → Actions → General → Fork pull request workflows**：
      *Require approval for all outside collaborators*
- [ ] **Settings → Actions → General → Workflow permissions**：
      *Read repository contents permission*（默认只读）
- [ ] **不要**勾选 *Allow GitHub Actions to create and approve pull requests*
      （否则机器人可能自我批准）
- [ ] **Settings → Rules → main**：按下表配置

## 2. `main` 分支规则

| 规则 | 值 | 原因 |
| --- | --- | --- |
| Require a pull request | ✅ | 禁止直接 push |
| Required approvals | 1（high 风险 2） | 人类是唯一合并者 |
| Dismiss stale approvals | ✅ | 新 commit 需重新批准 |
| Require review from Code Owners | ✅ | 受保护路径强制人类 |
| Required status checks | Phase 1：`lint` `package-tests` `build-and-test`；Phase 2 再拆细 | 机器门禁 |
| Require branches up to date | ✅ | 防止「合并后才发现冲突」 |
| Require conversation resolution | ✅ | 评审意见不得被忽略 |
| Require linear history | ✅ | 仅 squash |
| Block force pushes / deletions | ✅ | 保护历史 |
| Bypass list | 空 | 管理员也不能绕过 |

Agent 的 bot 账号**不得**在 bypass 列表中，也不得拥有 admin 权限。

## 3. Actions 权限与 fork 策略

公开仓库最大的风险是「外部 PR 的代码在带凭据的环境里执行」。

| 配置 | 值 | 说明 |
| --- | --- | --- |
| `GITHUB_TOKEN` 默认权限 | read-only | 只有显式声明的 job 才提权 |
| Fork PR 运行 | 需维护者批准 | 默认关闭自动运行 |
| `pull_request_target` | **禁止**与 checkout fork 代码组合使用 | 典型提权漏洞 |
| Action 引用 | pin 到 commit SHA | 防止 tag 被改写 |
| 自托管 runner | 不用于公开 PR | 否则等于把内网暴露给陌生人 |
| Secrets | 不注入到 fork PR 的 job | 环境级审批 |

工作流头部统一写：

```yaml
permissions:
  contents: read
```

需要写权限的 job（如评论 issue）单独声明，且只在 `workflow_dispatch` 或维护者触发的路径上运行。

## 4. Agent 身份与触发边界

| 项 | 建议 |
| --- | --- |
| 身份 | **GitHub App**（短期 token）优先；其次 fine-grained PAT（90 天轮换） |
| 权限 | `contents:write`（仅本仓库）、`pull_requests:write`、`issues:write` |
| 禁止 | `administration`、`secrets`、`workflows`、`actions:write` |
| 触发 | ①维护者给 issue 打 `status:ready`；②维护者 `workflow_dispatch` |
| 不触发 | 外部用户创建的 issue、外部评论、fork PR、`issue_comment` 事件 |
| 可处理作者 | `author_association` ∈ `OWNER` / `MEMBER` / `COLLABORATOR` |
| 写入范围 | 只能推 `ai/*` 分支，不能推 `main` |

**任何由外部账号提供的文本都不进入 Agent 的指令区**，只作为「需求数据」展示，并且
需要在 prompt 中显式包裹与声明为不可信（见 `.agent/config.yml` 的 `trust.public_repo`）。

## 5. Secrets 策略

默认**不需要任何密钥**（mock 数据源）。可选：

| Secret | 用途 | 存放 | 触发方式 |
| --- | --- | --- | --- |
| `ALPACA_KEY_ID` / `ALPACA_SECRET` | 真实行情冒烟 | Environment `market-live`（需审批） | 手动 `workflow_dispatch` |
| `APPSTORE_*` | TestFlight（Phase 4） | Environment `release` | 手动，需 1 人审批 |

规则：

- 不在 PR 中回显、不打印 `env`、不把密钥写进测试 fixture。
- 一旦进入 git 历史 → 立即轮换（删除提交无效）。
- 公开仓库默认开启 secret scanning + push protection，作为最后一道闸。

## 6. CI 成本模型

| 项 | 公开仓库 |
| --- | --- |
| 标准 GitHub-hosted runner（含 macOS） | **免费**，不计入额度（以 GitHub 当前计费页为准） |
| macOS 并发 | 受账号并发限制，通常是最紧张的资源 |
| 主要成本 | **排队时间** 与 **PR 反馈时长**，不是钱 |

策略：

- PR 只跑必需检查，目标 **< 10 分钟**；全量 UI/性能放 nightly。
- `concurrency: cancel-in-progress` 取消过期运行。
- 缓存 SPM + DerivedData；避免无意义 matrix。
- 不在公开 PR 上跑任何需要密钥的 job。

## 7. 环境与发布

| Environment | 保护规则 | 用途 |
| --- | --- | --- |
| `market-live` | 需 1 人审批 | 真实行情冒烟 |
| `release` | 需 1 人审批 + 仅 `main` | TestFlight / App Store |

发布流水线只能由人类 `workflow_dispatch` 触发；Agent 不得调用。

## 8. 公开仓库的内容卫生

- 不提交：密钥、内网地址、客户数据、真实持仓/账号截图、第三方授权数据。
- 不提交：内部文档链接、招聘/财务信息。
- issue 模板中提示「不要粘贴 token / 个人数据」。
- 提供 `SECURITY.md`（漏洞披露）与 `LICENSE`（MIT）。
- README 顶部保留免责声明：**不构成投资建议**。

## 9. 验收清单（Phase 1 结束前）

- [ ] `main` 直接 push 被拒绝
- [ ] 未通过 CI 的 PR 无法合并
- [ ] 机器人账号无法合并、无法改 workflow
- [ ] fork PR 默认不自动运行
- [ ] secret scanning / push protection 已开启
- [ ] Dependabot alerts 已开启
- [ ] 一次「只改 README」的 PR 走完全流程（人类合并）
- [ ] Actions 实际耗时记录在案（为后续调优做基线）
