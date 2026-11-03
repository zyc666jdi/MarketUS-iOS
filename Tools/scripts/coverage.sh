#!/usr/bin/env bash
# 输出覆盖率报告。
#
# Phase 1：仅生成报告，不设阈值（避免在 App 尚未成型时产生噪声）。
# Phase 2：接入「变更行 ≥ 80%」门禁（见 docs/testing-strategy.md）。
set -euo pipefail

RESULT="${1:-TestResults/MarketUS.xcresult}"

if [[ ! -d "$RESULT" ]]; then
    echo "缺少测试结果：$RESULT"
    echo "请先运行：make test"
    exit 1
fi

xcrun xccov view --report "$RESULT" | tee coverage-report.txt
echo
echo "覆盖率报告已写入 coverage-report.txt（Phase 1 仅报告，不阻断）"
