# PR Review 系统设计

> 状态：v1 草案  
> 适用仓库：MarketUS-iOS  
> 上位契约：`AGENTS.md`。本文只收紧评审流程，不改变“Agent 永不合并”的红线。

## 1. 目标与非目标

### 目标

- 尽早发现功能错误、回归、安全和兼容性问题。
- 减少人工重复阅读低风险 diff 的时间。
- 让评审结论可追踪、可复现、可度量。
- 对低风险 PR 保持低延迟，对高风险 PR 增加分析深度。
- 不因引入 Agent 扩大代码、凭证或生产系统权限。

### 非目标

- 不让 Agent 代替人类合并。
- 不让通用模型评论直接阻断合并。
- 不默认加载完整 Git 历史。
- 不把“发现的问题数量”当作唯一产出指标。

## 2. 核心原则

1. Agent 默认只读，不具备合并、force push、部署权限。
2. 规则能判断的问题不交给模型。
3. 模型只在需要代码理解、上下文判断时介入。
4. Git 历史按需查询，不默认加载完整历史。
5. Agent Review 初期只评论，不阻断合并。
6. 每次任务在一次性隔离环境中运行，结束销毁。
7. 所有结论必须引用文件、行号、代码路径或测试证据。
8. 评审输入、测试执行、GitHub 写回三者分离。

## 3. 三层架构

| 层级 | 适用场景 | 执行方式 | 模型 |
| --- | --- | --- | --- |
| L0 | 格式、命名、危险 API、secret、构建/测试、文件范围 | 固定规则、Lint、静态扫描、CI | 否 |
| L1 | 普通业务 PR | Agent 快速审查 diff + 局部上下文 | 是 |
| L2 | 高风险或专项问题 | 专项 Agent、按需历史、专项测试 | 是 |

说明：

- L0 先运行，结果作为 L1/L2 输入，Agent 不重复执行 L0。
- L1 默认不搜索全仓库、不读完整历史、不访问互联网、不改代码。
- L2 才允许按需查询历史、运行专项验证。

## 4. 事件与路由

### 4.1 第一版触发

- PR 创建。
- PR 新增 commit。
- 手动 `/agent review`。

后续再增加：重新打开、标签 `agent-review`、定时扫描、CI 成功后复审。

### 4.2 预筛选

```text
PR 事件
  → 是否修改代码？
      ├─ 否：只跑文档/配置检查
      └─ 是
          → 是否命中高风险目录/规则？
              ├─ 是：L2
              └─ 否
                  → 是否命中专项规则？
                      ├─ 是：对应专项 L2
                      └─ 否：L1 或跳过
```

### 4.3 风险评分

| 信号 | 分值 |
| --- | ---: |
| 高风险路径 | +3 |
| 权限 / 认证 / 数据迁移 | +3 |
| 公共 API 或协议变化 | +2 |
| 并发 / 缓存 / 持久化 | +2 |
| 删除代码超过一定比例 | +2 |
| 依赖或构建配置变化 | +1 |
| PR 超过 500 行 | +1 |
| 该模块历史回归多 | +2 |

路由：

- 0–2：L0 或 L1
- 3–5：L1
- 6+：L2
- 命中安全、数据迁移、权限模型：直接 L2

## 5. Review Job

### 5.1 输入：Review Bundle

Reviewer 只能读取 Review Bundle，不直接接触完整仓库。

```text
.agent/reviews/<pr>-<head>/
  meta.json          # base/head/merge_base/ticket/diff_stat
  diff.patch         # 只含 PR diff
  files/             # 变更文件完整内容
  context/
    imports.md       # 直接调用方/被调用方摘要
    tests.md         # 相关测试文件与命令
  tests.json         # L0/CI 测试结果
  policy.md          # P1/P2/P3 + 项目规范摘要
```

约束：

- Bundle 不包含 `.git`。
- Bundle 不包含仓库其他未变更文件。
- 需要额外上下文时，由 Dispatcher 显式加入，不开放全仓库。
- 测试在独立 sandbox 执行，结果写入 `tests.json`。

### 5.2 隔离环境

- 一次性目录或容器。
- 默认断网。
- 不注入 GitHub、云平台、生产凭证。
- 限制 CPU、内存、磁盘、执行时间。
- 任务结束销毁。
- 共享 Git mirror 只读；Agent 只写任务工作区。

### 5.3 执行阶段

1. 创建隔离环境。
2. 固定 Git 信息：`base_commit`、`head_commit`、`merge_base`、`changed_files`、`diff_stat`。
3. 构建 Review Bundle。
4. 执行 L0：编译、测试、Lint、Secret、依赖漏洞、危险 API、schema。
5. L1 Agent 审查 Bundle。
6. 命中历史信号时，按需查询有限历史。
7. 命中专项时进入 L2。
8. 汇总测试与 CI 结果。
9. 生成结构化结果。
10. 发布评论；默认不阻断。

## 6. Git 历史策略

默认不加载完整历史。仅在出现以下信号时按需查询：

- 特殊兼容代码、legacy、workaround。
- 删除兼容分支。
- 与附近代码风格明显不一致。
- 无法判断条件是否必要。
- 可能重新引入旧 bug。
- 稳定 API、协议、数据库、缓存、同步、线程、状态机变更。
- TODO / FIXME 附近代码。

查询顺序：

```bash
git blame -L <start>,<end> -- <file>
git log -p -- <file>
git log -S'<string>' -- <file>
git log -G'<regex>' -- <file>
git log --all --grep='<keyword>'
```

历史只能作为证据，不能自动推翻当前代码。报告中必须区分：

- 当前代码证据
- 历史演进证据
- 测试验证证据
- Agent 推断

## 7. 结构化输出

```json
{
  "status": "pass|needs_attention|blocked|error",
  "risk": "low|medium|high|critical",
  "findings": [
    {
      "severity": "P0|P1|P2|P3",
      "file": "path/to/file",
      "line": 123,
      "category": "correctness|security|performance|compatibility|testing",
      "title": "简短问题描述",
      "evidence": "代码或测试证据",
      "impact": "可能造成的影响",
      "suggestion": "建议方向",
      "confidence": 0.87,
      "history_checked": true
    }
  ],
  "checks": {
    "tests": "passed",
    "lint": "passed",
    "ci": "passed"
  }
}
```

严重度：

- P0：安全、数据丢失、生产不可用。
- P1：高概率功能错误或严重回归，阻断合并。
- P2：需要修复但不阻断合并。
- P3：低风险改进建议。

## 8. 评论与阻断

### 初期：非阻断

- 只发评论，不改 PR 状态。
- 评论必须包含：文件、行号、证据、影响、建议、置信度。
- 低置信度猜测不发布。

### 中期：有限阻断

只对高置信度、低争议规则阻断：

- Secret 泄露。
- 明确权限越权。
- 构建/必要测试失败。
- 必要迁移缺失。
- 违反强制安全规则。

通用模型评论保持非阻断。

### 高风险 PR

- 必须完成 Agent Review。
- 相关专项测试通过。
- 至少一名人工 Reviewer。
- 关键问题显式确认。
- 合并权限仍由人保留。

## 9. 权限与安全

权限按“工具 + 操作”细分：

```text
git.read
git.diff
git.branch_create
github.pr_read
github.comment_write
ci.read
ticket.read
wiki.read
```

默认禁止：

```text
github.merge
git.push_force
production.deploy
secret.read
cloud_admin
database_write
```

要求：

- 凭证只存在主机侧。
- 不把 Token 写入环境变量、文件、Prompt。
- 日志自动脱敏。
- 任务结束销毁临时凭证。
- 工具命令白名单。
- 沙箱默认断网。
- 禁止访问云元数据服务。
- 不读取仓库之外的路径。

## 10. Prompt 边界

Reviewer prompt 必须明确：

1. 先读 PR 描述和 diff。
2. 查看变更文件完整上下文。
3. 只在必要时读直接调用方/被调用方和相关测试。
4. 只有高风险信号才查询历史。
5. 不评论风格问题。
6. 不对无法验证的猜测给高严重度。
7. 每个问题必须包含文件、行号、触发条件、影响、证据、建议、置信度。
8. 没有可验证问题时明确输出“未发现需要阻断合并的问题”。

Reviewer 禁止：

- `git log` / `git show` / `git blame`
- 任意全仓库搜索
- 访问互联网
- 修改代码
- 提交修复
- 改变 PR 状态
- 合并 / force push

## 11. 指标体系

### 质量

- 有效问题率
- 误报率 / 漏报率
- 被人工确认/修复比例
- 合并后回归数
- 严重度分布
- 同类问题重复率

### 效率

- 触发到评论的 P50/P95
- Agent 执行时间
- CI 等待时间
- Token 数 / 单 PR 成本
- 重试率、超时率、队列等待

### 使用

- 覆盖率
- 主动触发比例
- 评论采纳率
- 人工重跑率
- 规则命中率
- L2 占比

### 安全

- 越权调用
- 被拒绝的工具调用
- 凭证泄露检测
- 沙箱逃逸告警
- 网络违规
- 审计完整率

不要只统计“发现了多少问题”，应优先看：

```text
有效问题数 × 严重度
```

## 12. 落地阶段

### 阶段 0：准备

明确目标、高风险目录、权限、输出格式、CI 能力、审计要求、基线指标。

### 阶段 1：只读试运行

- 单仓库。
- PR 事件接入。
- 一次性沙箱。
- L0 + L1。
- 非阻断评论。
- 全量指标。

### 阶段 2：专项 Review

先选一个领域：数据库迁移、Core Data、权限、依赖、API 兼容性。

### 阶段 3：接入 CI

Agent 读取构建、测试、静态检查、失败日志；测试只在临时工作区生成。

### 阶段 4：有限阻断

仅对 secret、权限、构建失败、迁移缺失、强制安全规则阻断。

### 阶段 5：工作流编排

规则筛选 → 专项 Agent → CI → 失败分析 → 修复建议 → 人工确认。

## 13. MarketUS-iOS 映射

### 13.1 目录风险

| 路径 | 层级 | 说明 |
| --- | --- | --- |
| `Tools/dispatcher/**` | L2 | 调度、权限、GitHub 写操作 |
| `.github/**` | L2 | CI / 权限 / 合并门禁 |
| `Packages/MarketUSKit/**` | L2 | 核心业务与并发 |
| `App/**` | L1 | UI 与生命周期 |
| `docs/**` | L0 | 文档 |
| `project.yml` / `Package.swift` | L2 | 构建与依赖 |

### 13.2 L0 命令

```bash
make lint
make test-dispatcher
make build
make test-package
make secret-scan
```

Headless 环境：

```bash
make build
make test-package
```

UI 测试需要 Simulator.app，未运行必须标注“UI 未跑”。

### 13.3 Review Bundle 路径

```text
.agent/reviews/<pr>-<head>/
```

### 13.4 Dispatcher 接入点

- `cmd_review`：构建 Bundle、运行 L0、调用 L1/L2、发布评论。
- `run_review_tests`：在独立 sandbox 执行，结果写 `tests.json`。
- Reviewer cwd：Bundle，而非完整仓库。
- 写回：只允许评论与标签，不允许 merge / force push。
- 轮次：P1 最多 4 轮；第 4 次后升级人类。

## 14. MVP 验收

- 95% 的 PR 在 5 分钟内得到结果。
- 评论必须包含文件和行号。
- 高严重度问题达到预设有效率。
- 凭证不进入沙箱。
- Agent 无合并、force push 权限。
- 所有任务可审计、可重放。
- CI 等待时间与 Agent 执行时间分开统计。
- Reviewer 输入只有 Bundle，不接触完整仓库。

## 15. 当前状态与差距

| 项 | 状态 |
| --- | --- |
| 无 Git 历史快照 | 已设计；PR #124 已关闭 |
| Reviewer 完整仓库快照 | 已实现但已关闭，不满足严格输入隔离 |
| Review Bundle | 待实现 |
| L0 规则 | 已有 lint / test / secret-scan |
| L1 结构化输出 | 部分（verdict/findings） |
| L2 专项 | 未实现 |
| 非阻断评论 | 已实现 |
| 4 轮上限 | 已实现 |
| diff 2000 行上限 | 已实现 |
| 指标 | 部分（Loop Trace） |

下一步优先级：

1. 实现 Review Bundle。
2. `cmd_review` 改为只把 Bundle 交给 Reviewer。
3. 测试在独立 sandbox，结果注入 `tests.json`。
4. 增加 L0 规则筛选与风险评分。
5. 再评估 L2 专项。
