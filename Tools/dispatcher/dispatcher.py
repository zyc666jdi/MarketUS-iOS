#!/usr/bin/env python3
"""MarketUS Dispatcher —— Phase 3a（只读）。

当前只实现两条只读命令：
    dispatcher.py status    查看各状态数量与活跃 ticket
    dispatcher.py plan      列出 status:ready 的 ticket 并做 DoR 校验

不会写 GitHub、不会领取 ticket、不会调用 Agent。
设计见 docs/dispatcher.md。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

STATUS_LABELS = [
    "status:ready",
    "status:claimed",
    "status:in-progress",
    "status:in-review",
    "status:awaiting-human",
    "status:blocked",
    "status:done",
]

ACTIVE_STATUS = {
    "status:claimed",
    "status:in-progress",
    "status:in-review",
    "status:awaiting-human",
}

PRIORITY_ORDER = {
    "priority:p0": 0,
    "priority:p1": 1,
    "priority:p2": 2,
    "priority:p3": 3,
}

# 提示注入 / 危险指令的粗筛；命中即拒绝自动领取，交人类判断。
SUSPICIOUS_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"忽略(之前|以上|上述|前面)的?(所有)?指令",
    r"\.ssh",
    r"\.env",
    r"curl\s+[^|]+\|\s*(ba)?sh",
    r"(跳过|禁用|关闭)(测试|检查)",
    r"(skip|disable)\s+(the\s+)?tests?",
    r"gh\s+pr\s+merge",
    r"(直接|自动)\s*(合并|merge)",
    r"rm\s+-rf\s+/",
]

ISSUE_FIELDS = "number,title,body,labels,author,createdAt,url"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def gh_json(args: list[str]) -> Any:
    result = run(["gh", *args])
    if result.returncode != 0:
        raise SystemExit(f"gh failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def repo_slug() -> str:
    result = run(["git", "remote", "get-url", "origin"])
    match = re.search(r"github\.com[:/]([^/]+)/([^/.\n]+)", result.stdout.strip())
    if not match:
        raise SystemExit("无法从 origin remote 解析 owner/repo")
    return f"{match.group(1)}/{match.group(2)}"


def labels_of(issue: dict[str, Any]) -> set[str]:
    return {label["name"] for label in issue.get("labels", [])}


def section(body: str, title: str) -> str:
    pattern = re.compile(rf"^#{{2,4}}\s*{re.escape(title)}\s*$", re.IGNORECASE)
    lines = body.splitlines()
    start = None
    for index, line in enumerate(lines):
        if pattern.match(line.strip()):
            start = index + 1
            break
    if start is None:
        return ""
    collected: list[str] = []
    for line in lines[start:]:
        if re.match(r"^#{2,4}\s+", line):
            break
        collected.append(line)
    return "\n".join(collected)


def acceptance_lines(body: str) -> list[str]:
    block = section(body, "验收标准")
    return [line for line in block.splitlines() if re.match(r"^\s*[-*]\s+\S", line)]


def suspicious_hits(body: str) -> list[str]:
    return [pattern for pattern in SUSPICIOUS_PATTERNS if re.search(pattern, body, re.IGNORECASE)]


def validate(issue: dict[str, Any]) -> list[str]:
    """返回 DoR 失败项；空列表表示通过。"""
    failures: list[str] = []
    names = labels_of(issue)
    body = issue.get("body") or ""

    for prefix in ("type:", "area:", "priority:", "risk:"):
        if not any(name.startswith(prefix) for name in names):
            failures.append(f"缺少 {prefix}* 标签")

    if not section(body, "验收标准"):
        failures.append("缺少「验收标准」段落")
    elif len(acceptance_lines(body)) < 2:
        failures.append("验收标准少于 2 条")

    if not section(body, "范围外"):
        failures.append("缺少「范围外」段落")

    if "affected_paths" not in body.lower():
        failures.append("缺少 affected_paths")

    if "security:suspicious" in names:
        failures.append("标记为 security:suspicious")

    if len(body) > 6000:
        failures.append("正文过长（>6000 字符）")

    hits = suspicious_hits(body)
    if hits:
        failures.append("命中可疑内容：" + ", ".join(hits))

    return failures


def priority_rank(issue: dict[str, Any]) -> int:
    for name in labels_of(issue):
        if name in PRIORITY_ORDER:
            return PRIORITY_ORDER[name]
    return 99


def list_issues(label: str | None = None) -> list[dict[str, Any]]:
    args = ["issue", "list", "--state", "open", "--limit", "50", "--json", ISSUE_FIELDS]
    if label:
        args.extend(["--label", label])
    issues: list[dict[str, Any]] = gh_json(args)
    return issues


def cmd_status(_args: argparse.Namespace) -> int:
    issues = list_issues()
    counts = {status: 0 for status in STATUS_LABELS}
    for issue in issues:
        for name in labels_of(issue) & set(STATUS_LABELS):
            counts[name] += 1

    # status:done 的 ticket 已关闭，单独统计。
    closed_done = gh_json(
        ["issue", "list", "--state", "closed", "--label", "status:done", "--limit", "100", "--json", "number"]
    )
    counts["status:done"] = len(closed_done)

    print(f"repo: {repo_slug()}")
    for status in STATUS_LABELS:
        print(f"{status:24} {counts[status]}")

    active = [issue for issue in issues if labels_of(issue) & ACTIVE_STATUS]
    if active:
        print("\nactive:")
        for issue in sorted(active, key=lambda item: item["number"]):
            tags = ", ".join(sorted(labels_of(issue) & ACTIVE_STATUS))
            print(f"  #{issue['number']:<4} {issue['title']}  [{tags}]")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    ready = sorted(list_issues("status:ready"), key=lambda issue: (priority_rank(issue), issue["number"]))
    rows = []
    for issue in ready:
        failures = validate(issue)
        rows.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "priority": next(
                    (name for name in labels_of(issue) if name in PRIORITY_ORDER),
                    "priority:?",
                ),
                "url": issue["url"],
                "ready": not failures,
                "failures": failures,
            }
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("没有 status:ready 的 ticket。")
        return 0

    for row in rows:
        state = "✅" if row["ready"] else "⛔"
        print(f"{state} #{row['number']} [{row['priority']}] {row['title']}")
        for failure in row["failures"]:
            print(f"     - {failure}")

    blocked = sum(1 for row in rows if not row["ready"])
    print(f"\n共 {len(rows)} 张 ready，{blocked} 张未通过 DoR。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MarketUS Dispatcher (Phase 3a, read-only)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="各状态数量与活跃 ticket")

    plan_parser = subparsers.add_parser("plan", help="列出 ready ticket 并校验 DoR")
    plan_parser.add_argument("--json", action="store_true", help="输出 JSON")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status(args)
    if args.command == "plan":
        return cmd_plan(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
