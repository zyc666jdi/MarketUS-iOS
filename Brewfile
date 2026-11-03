# 开发工具链（不进入 App 产物）
# 用法：brew bundle          安装
#       brew bundle dump   重新生成
# 版本锁定：brew bundle 会生成 Brewfile.lock.json，请一并提交。

brew "xcodegen"     # 从 project.yml 生成 Xcode 工程（避免 .xcodeproj 冲突）
brew "swiftlint"    # 静态分析（--strict，0 warning 门禁）
brew "swiftformat"  # 格式化（--lint 门禁）
brew "gitleaks"     # 密钥扫描
brew "xcbeautify"   # CI 日志美化（可选）
brew "yq"           # 批量创建 GitHub 标签 / 解析 YAML

# Docker Desktop 请手动安装（体积大，且需要 GUI 授权）：
#   https://docs.docker.com/desktop/install/mac-install/
# 用于 Tools/mock-market 本地 Mock 行情服务；CI 使用 docker compose 启动。
