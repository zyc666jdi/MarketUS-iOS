SHELL := /bin/bash

SCHEME  := MarketUS
DEST    ?= platform=iOS Simulator,name=iPhone 16
BUILD_DEST ?= generic/platform=iOS Simulator
PACKAGE := Packages/MarketUSKit
RESULT  := TestResults/MarketUS.xcresult

.PHONY: bootstrap lint format build test test-package coverage secret-scan verify simulator-stop clean

bootstrap:
	@command -v xcodegen >/dev/null 2>&1 || { echo "缺少 xcodegen：请先运行 brew bundle"; exit 1; }
	xcodegen generate

lint:
	@command -v swiftlint >/dev/null 2>&1 || { echo "缺少 swiftlint：请先运行 brew bundle"; exit 1; }
	swiftlint --strict
	@command -v swiftformat >/dev/null 2>&1 && swiftformat --lint . || echo "警告：未安装 swiftformat，跳过格式检查"

format:
	@command -v swiftformat >/dev/null 2>&1 || { echo "缺少 swiftformat：请先运行 brew bundle"; exit 1; }
	swiftformat .

test-package:
	swift test --package-path $(PACKAGE)

build: bootstrap
	xcodebuild -scheme $(SCHEME) -destination '$(BUILD_DEST)' build CODE_SIGNING_ALLOWED=NO

test: bootstrap
	rm -rf $(RESULT)
	xcodebuild test -scheme $(SCHEME) -destination '$(DEST)' \
		-enableCodeCoverage YES -resultBundlePath $(RESULT) CODE_SIGNING_ALLOWED=NO

coverage:
	@./Tools/scripts/coverage.sh $(RESULT)

secret-scan:
	@command -v gitleaks >/dev/null 2>&1 || { echo "缺少 gitleaks：请先运行 brew bundle"; exit 1; }
	gitleaks detect --no-git --redact -v

verify: lint test-package build test coverage
	@echo "✅ verify 通过（lint + package tests + build + UI test + coverage 报告）"

simulator-stop:
	@xcrun simctl shutdown all 2>/dev/null; \
	if pgrep -x Simulator >/dev/null 2>&1; then \
		osascript -e 'tell application "Simulator" to quit' >/dev/null 2>&1; \
	fi; \
	echo "✅ 模拟器已关闭（避免模拟器内 mediaanalysisd 空转占用 CPU）"

clean:
	rm -rf build DerivedData TestResults coverage-report.txt
	rm -rf MarketUS.xcodeproj
