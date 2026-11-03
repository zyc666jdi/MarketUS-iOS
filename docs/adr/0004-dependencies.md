# ADR-0004: 依赖基线——运行时零第三方依赖

- **状态**：Proposed
- **日期**：2026-11-03
- **决策者**：@ios-leads（待确认）
- **相关**：`docs/dependencies.md`、`docs/security-and-compliance.md`

## 背景

- 仓库公开，任何依赖都是供应链攻击面。
- 代码主要由 AI Agent 编写/修改，第三方框架的隐式约定会显著增加出错概率。
- 团队规模小，没有精力跟进多个框架的 breaking change。
- 最低 iOS 16，原生能力（async/await、URLSession、WebSocket、Swift Charts、Combine）已覆盖 MVP。

## 选项

| 选项 | 优点 | 缺点 |
| --- | --- | --- |
| A. 零运行时依赖 + 少量测试/工具依赖 | 攻击面最小、升级成本最低、Agent 友好 | 部分轮子要自己写（缓存、重连） |
| B. 常规组合（Alamofire + Kingfisher + Realm…） | 开发快 | 依赖链长、版本冲突、审计困难 |
| C. 全自研（含网络栈） | 完全可控 | 重复造轮子、Bug 风险高 |

## 决策

选择 **A**：

1. **运行时依赖 0 个**；网络、WebSocket、持久化、图表、日志全部用原生 API。
2. **测试依赖**：默认 XCTest + 自写 fake；快照库（`swift-snapshot-testing`）作为唯一候选，需人类批准后引入。
3. **工具链**：XcodeGen / SwiftLint / SwiftFormat / gitleaks / xcbeautify 通过 `Brewfile` 锁定，不进入产物。
4. **白名单制**：`docs/dependencies.md` 是唯一白名单，清单外一律未批准。
5. **准入标准**：许可证、维护活跃度、传递依赖、原生替代方案、移除成本，全部通过才可引入。
6. **精确版本 + 提交 `Package.resolved`**；Action pin 到 commit SHA。

## 后果

**正面**

- 供应链风险最低，公开仓库审计简单。
- 升级只需跟 Xcode/Swift 版本，不被第三方节奏绑架。
- Agent 写出的代码依赖少、上下文小，评审更容易。

**负面 / 代价**

- 需要自己实现：HTTP 重试/缓存、WebSocket 重连、图片缓存（如需要）、数据库（如需要）。
- 某些能力（崩溃分析、复杂动画、图片流水线）后期仍需引入第三方，届时成本更高。

**需要跟进**

- [ ] Phase 2 网络层与重连自研代码的测试覆盖（≥ 80%）
- [ ] 每季度评估一次「是否到了该引入 X 的规模」
- [ ] 快照库的引入决策（Phase 2 末）

## 重新评估的触发条件

- 自研网络/存储代码缺陷率显著高于预期
- 需要图片流水线或离线数据库（届时按 ADR 引入 Nuke/GRDB）
- 团队规模扩大到需要统一 UI 组件库
