# 安全策略

## 报告漏洞

**不要通过公开 issue 报告安全问题。**

请使用 GitHub 的 [Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)：
仓库 **Security → Report a vulnerability**。

请在报告中包含：

- 受影响的版本 / commit
- 复现步骤或 PoC（不要包含真实用户数据）
- 影响评估（读取数据？远程执行？绕过门禁？）
- 你希望如何署名

我们会在 **3 个工作日内**确认收到，**7 天内**给出初步评估。

## 支持范围

| 版本 | 状态 |
| --- | --- |
| `main` | ✅ 接收修复 |
| 最新 TestFlight | ✅ 接收修复 |
| 更早版本 | ❌ 仅记录 |

## 重点关注

- 认证/会话、密钥与凭据处理
- 行情数据的越权访问与再分发
- 隐私数据（持仓、自选股、设备标识）的泄漏
- 供应链（依赖、GitHub Actions、构建脚本）
- 针对 AI Agent 的提示注入导致越权（改 CI、读密钥、绕过评审）

## 明确的非问题

- 使用 Mock/公开演示数据本身
- 需要物理接触设备或已越狱环境的问题（除非可规模化）
- 缺少「保险式」加固且无可利用路径的报告（会记录但不加急）

## 安全研究的安全港

在遵守以下前提下，我们不会对善意的安全研究采取法律行动：

- 只测试你自己创建的账号与数据
- 不进行拒绝服务、不访问他人数据、不修改他人内容
- 给合理的修复时间（≥ 7 天）后再公开

## 致谢

经确认的有效报告会在修复后致谢（除非你要求匿名）。
