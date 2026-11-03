# Runbook: CI 失败排查

> 原则：**先判断是代码问题还是基础设施问题。** 基础设施可以重跑，代码问题必须修代码。
> 任何情况下都不得通过跳过检查、放宽阈值、删除测试来「修好」CI。

## 1. 快速判断

```bash
gh pr checks <n>                      # 哪些 job 红了
gh run view <run-id> --log-failed     # 只看失败日志
gh run rerun <run-id> --failed        # 仅基础设施问题可重跑（最多 1 次）
```

| 现象 | 判定 | 动作 |
| --- | --- | --- |
| 同一 commit 重跑即过 | 基础设施/flaky | 重跑 1 次；记录 flaky |
| 本地同样失败 | 代码问题 | 修代码 |
| 本地通过、CI 失败 | 环境差异 | 对齐版本/依赖 |
| 多个 PR 同时红同一 job | 基础设施 | 通知维护者，标记 `status:blocked` |
| 只有本 PR 红 | 代码问题 | 修代码 |

## 2. 常见失败与处置

### `xcodegen: command not found`

```bash
brew install xcodegen
make bootstrap
```

CI 中：确认 setup 步骤在 build 之前，且缓存键未污染。

### 模拟器无法启动 / destination 不匹配

```bash
xcrun simctl list devices available
xcodebuild -scheme MarketUS -showdestinations
```

改用具体设备名（与 CI 镜像一致），或加 `-destination 'generic/platform=iOS Simulator'`。
**不要**通过删测试来规避。

### 代码签名失败

```bash
xcodebuild ... CODE_SIGNING_ALLOWED=NO
```

PR/CI 构建一律禁用签名；需要签名的 job 只在发布流水线，且由人类触发。

### SPM 解析失败 / 版本冲突

```bash
rm -rf .build ~/Library/Caches/org.swift.swiftpm
xcodebuild -resolvePackageDependencies -scheme MarketUS
```

若因新增依赖导致 → 检查是否有人未经批准改了 `Package.swift`（受保护路径）。

### 覆盖率门禁失败

```bash
make coverage          # 看具体未覆盖行
```

补测试，**不要**调阈值。确属无法测试的胶水代码 → 在 PR 中显式说明并请人类豁免。

### gitleaks 告警

```bash
gitleaks detect --no-git -v
```

- 真实密钥 → 立即轮换 + 从历史移除（BFG/filter-repo 由人类操作）。
- 误报（测试 fixture 假字符串）→ 加入 `.gitleaks.toml` allowlist，**需人类审批**。
- Agent 不得自行修改 allowlist。

### 快照测试失败

```bash
# 查看差异产物，不要直接覆盖
open snapshot-failures/
```

- 预期变更 → 说明原因并在 PR 中附新旧对比图。
- 非预期 → 修实现，**禁止** `record: .all` 重新录制了事。

### flaky（时好时坏）

按 `testing-strategy.md` §7：记录 → quarantine → 48h 内修复 → 否则升级 P1。
**禁止**在 CI 配置里加 `retry` 掩盖。

### 权限错误（403 / Resource not accessible）

- Bot token 可能过期或权限不足 → 人类重新签发，**不要把 token 贴进 issue**。
- `pull_request_target` 相关权限问题 → 不要为了「能跑」而放宽权限。

## 3. 重跑规则

| 情况 | 允许重跑 |
| --- | --- |
| 基础设施故障（runner 掉线、网络、配额） | ✅ 1 次 |
| 明确的 flaky 测试 | ✅ 1 次，并记录 |
| 代码/测试失败 | ❌ 重跑无意义 |
| 覆盖率/密钥扫描失败 | ❌ |

连续 2 次基础设施故障 → `status:blocked` + 通知维护者，不要无限重跑。

## 4. 上报模板

```markdown
## CI 持续失败

- PR: #124
- Job: `unit-tests`
- Run: <link>
- 失败信息：
  ```
  <原始错误，不要转述>
  ```
- 本地复现：是 / 否
- 已重跑：1 次（结果：失败）
- 判断：代码问题 / 基础设施 / 环境差异
- 需要的支持：@owner
```

## 5. 禁止清单

- ❌ 修改 `.github/workflows/**` 来绕过失败（受保护路径，需人类）
- ❌ 加 `|| true`、`continue-on-error`、`if: always()`
- ❌ 删除/跳过失败的测试
- ❌ 降低覆盖率阈值
- ❌ 把密钥加入 allowlist
- ❌ 使用 `--admin` 强制合并
