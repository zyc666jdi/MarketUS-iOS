SHELL := /bin/bash

SCHEME  := MarketUS
DEST    ?= platform=iOS Simulator,name=iPhone 16
PACKAGE := Packages/MarketUSKit
RESULT  := TestResults/MarketUS.xcresult

.PHONY: bootstrap lint format build test test-package coverage secret-scan verify clean

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
	xcodebuild -scheme $(SCHEME) -destination '$(DEST)' build CODE_SIGNING_ALLOWED=NO

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

clean:
	rm -rf build DerivedData TestResults coverage-report.txt
	rm -rf MarketUS.xcodeproj
