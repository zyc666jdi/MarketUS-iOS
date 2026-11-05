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
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
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

TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
WORKTREE_ROOT = Path(".worktrees")
RUN_ROOT = Path(".agent/runs")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(issue: int) -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{issue}"


def slugify(title: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (slug[:limit].strip("-") or "ticket")


def gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = run(["gh", *args])
    if check and result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def cmd(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed: {result.stderr.strip()}")


def issue_detail(number: int) -> dict[str, Any]:
    return gh_json(["api", f"repos/{repo_slug()}/issues/{number}"])


def comment(number: int, body: str) -> None:
    gh(["issue", "comment", str(number), "--body", body])


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

    if "affected_paths" not in body.lower() and not section(body, "预期受影响路径"):
        failures.append("缺少 affected_paths / 预期受影响路径")

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



def build_prompt(issue: dict[str, Any], worktree: Path, branch: str) -> str:
    implementer = Path(".agent/prompts/implementer.md").read_text(encoding="utf-8")
    contract = Path("AGENTS.md").read_text(encoding="utf-8")
    body = issue.get("body") or ""
    return f"""{implementer}

---

{contract}

---

# Ticket #{issue['number']}: {issue['title']}

{body}

---

# Run constraints

- 工作目录：`{worktree}`（不得在主工作区修改）
- 分支：`{branch}`
- 超时：见 dispatcher；超过即会被终止
- 禁止：合并 PR、push `main`、修改受保护路径、跳过测试
- 完成后：按 PR 模板填写证据，并确保 `make verify` 通过
"""


def cmd_once(args: argparse.Namespace) -> int:
    number = args.issue
    issue = issue_detail(number)
    labels = labels_of(issue)
    failures = validate(issue)

    if issue.get("state") != "open":
        failures.append("issue 不是 open 状态")
    if "pull_request" in issue:
        failures.append("这是 PR，不是 issue")
    if issue.get("author_association") not in TRUSTED_ASSOCIATIONS:
        failures.append(f"作者不可信：{issue.get('author_association')}")
    if "status:ready" not in labels:
        failures.append("缺少 status:ready（可能已被领取或未就绪）")

    if failures:
        print(f"⛔ #{number} 未通过 DoR/准入：")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    slug = slugify(issue["title"])
    branch = f"ai/{number}-{slug}"
    worktree = WORKTREE_ROOT / f"{number}-{slug}"
    prompt = build_prompt(issue, worktree, branch)

    print(f"ticket   #{number} {issue['title']}")
    print(f"branch   {branch}")
    print(f"worktree {worktree}")
    print(f"prompt   {len(prompt)} chars")
    print("agent    dsh --profile headless --json")

    if not args.execute:
        print("\n(dry-run) 未写入 GitHub、未创建 worktree、未调用 Agent；加 --execute 执行。")
        return 0

    if worktree.exists():
        print(f"⛔ worktree 已存在：{worktree}")
        return 1

    run_id = make_run_id(number)
    gh(["issue", "edit", str(number), "--remove-label", "status:ready", "--add-label", "status:claimed"])
    comment(
        number,
        f"🤖 claimed\n\n- run: `{run_id}`\n- at: `{now_iso()}`\n- branch: `{branch}`",
    )
    after = issue_detail(number)
    if "status:claimed" not in labels_of(after):
        print("⛔ 领取回读失败（可能被其他实例抢占）")
        return 1

    try:
        cmd(["git", "fetch", "origin", "main"])
        cmd(["git", "worktree", "add", str(worktree), "-b", branch, "origin/main"])
    except SystemExit as error:
        gh(["issue", "edit", str(number), "--remove-label", "status:claimed"])
        comment(number, f"⛔ 创建 worktree 失败：{error}")
        return 1

    gh(["issue", "edit", str(number), "--add-label", "status:in-progress"])

    run_dir = RUN_ROOT / str(number)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    env = os.environ.copy()
    env["AGENT_RUN_ID"] = run_id
    env["TICKET_NUMBER"] = str(number)

    timeout_seconds = args.timeout * 60
    try:
        result = subprocess.run(
            ["dsh", "--profile", "headless", "--json"],
            input=prompt,
            text=True,
            cwd=str(worktree),
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
        )
        (run_dir / "agent.jsonl").write_text(result.stdout, encoding="utf-8")
        (run_dir / "agent.stderr.log").write_text(result.stderr, encoding="utf-8")
        agent_failed = result.returncode != 0
    except subprocess.TimeoutExpired:
        gh(["issue", "edit", str(number), "--remove-label", "status:in-progress", "--add-label", "status:blocked"])
        comment(number, f"⛔ Agent 超时（{args.timeout} 分钟）。日志：`.agent/runs/{number}/`")
        return 1
    except FileNotFoundError:
        raise SystemExit("找不到 dsh，请确认 PATH 中有 dsh")

    prs = json.loads(
        run(["gh", "pr", "list", "--head", branch, "--json", "number,url,title", "--limit", "1"]).stdout or "[]"
    )
    gh(["issue", "edit", str(number), "--remove-label", "status:in-progress"])

    if prs:
        pr = prs[0]
        gh(["issue", "edit", str(number), "--add-label", "status:in-review"])
        comment(number, f"✅ PR 已创建：{pr['url']}\n\n日志：`.agent/runs/{number}/`")
        print(f"PR: {pr['url']}")
        return 0

    gh(["issue", "edit", str(number), "--add-label", "status:blocked"])
    reason = "Agent 退出码非 0" if agent_failed else "未检测到 PR"
    comment(number, f"⛔ {reason}。worktree: `{worktree}`，日志：`.agent/runs/{number}/`")
    print(f"⛔ {reason}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="MarketUS Dispatcher (Phase 3a, read-only)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="各状态数量与活跃 ticket")

    plan_parser = subparsers.add_parser("plan", help="列出 ready ticket 并校验 DoR")
    plan_parser.add_argument("--json", action="store_true", help="输出 JSON")

    once_parser = subparsers.add_parser("once", help="手动派发一张 ticket（默认 dry-run）")
    once_parser.add_argument("--issue", type=int, required=True, help="issue number")
    once_parser.add_argument("--execute", action="store_true", help="真正领取并调用 Agent")
    once_parser.add_argument("--timeout", type=int, default=90, help="Agent 超时（分钟）")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status(args)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "once":
        return cmd_once(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
