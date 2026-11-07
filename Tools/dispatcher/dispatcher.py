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
import fcntl
import json
import os
import re
import shlex
import shutil
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


def agent_command() -> list[str]:
    """Agent 入口；可用 AGENT_CMD 覆盖（例如 codex exec / claude -p）。"""
    return shlex.split(os.environ.get("AGENT_CMD", "dsh --profile headless --json"))


def secret_env_file() -> Path:
    return Path(os.environ.get("AGENT_ENV_FILE", Path.home() / ".config/marketUS/deepseek.env"))


def load_agent_secrets() -> None:
    """按 env → 600 权限 env 文件 → macOS Keychain 的顺序解析 DEEPSEEK_API_KEY。

    只写入当前进程环境，不打印、不落盘。
    """
    if os.environ.get("DEEPSEEK_API_KEY"):
        return

    path = secret_env_file()
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)

    if os.environ.get("DEEPSEEK_API_KEY"):
        return

    if sys.platform == "darwin":
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "DEEPSEEK_API_KEY", "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            os.environ["DEEPSEEK_API_KEY"] = result.stdout.strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(issue: int) -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{issue}"


def slugify(title: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (slug[:limit].strip("-") or "ticket")


def is_merge_command(command: list[str]) -> bool:
    """Return True for a ``gh pr merge`` invocation; agents never merge."""
    return len(command) >= 3 and command[0] == "gh" and command[1] == "pr" and command[2] == "merge"


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


def notify(title: str, message: str) -> None:
    """macOS 通知；MARKETUS_NOTIFY=0 关闭，失败静默。"""
    if os.environ.get("MARKETUS_NOTIFY") == "0" or sys.platform != "darwin":
        return
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def comment(number: int, body: str) -> None:
    gh(["issue", "comment", str(number), "--body", body])


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    if is_merge_command(cmd):
        raise SystemExit("⛔ Agent 永不合并：拒绝执行 gh pr merge，请由人类合并。")
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


def set_status(issue: int | dict[str, Any], target: str) -> None:
    """Move an issue to ``target`` and clear every other status label.

    ``issue`` accepts an issue number or a GitHub issue payload. ``status:done``
    is terminal and is never removed here, even when moving to another status.
    The target is always added, so this is idempotent.
    """
    if target not in STATUS_LABELS:
        raise ValueError(f"unknown status label: {target}")

    number = issue["number"] if isinstance(issue, dict) else issue
    args = ["issue", "edit", str(number)]
    for label in STATUS_LABELS:
        if label in ("status:done", target):
            continue
        args.extend(["--remove-label", label])
    args.extend(["--add-label", target])
    gh(args)


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


NEGATION_PATTERN = re.compile(r"(不|禁止|不得|勿|严禁|never|do\s+not|don't|no)", re.IGNORECASE)


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 24):start]
    return bool(NEGATION_PATTERN.search(prefix))


def suspicious_hits(body: str) -> list[str]:
    scope = section(body, "范围外")
    scanned = body.replace(scope, "") if scope else body
    hits: list[str] = []
    for pattern in SUSPICIOUS_PATTERNS:
        for match in re.finditer(pattern, scanned, re.IGNORECASE):
            if not _is_negated(scanned, match.start()):
                hits.append(pattern)
                break
    return hits


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
    command = agent_command()
    load_agent_secrets()
    credential = "DEEPSEEK_API_KEY" if os.environ.get("DEEPSEEK_API_KEY") else "missing"
    print(f"agent    {' '.join(command)}")
    print(f"cred     {credential}")

    if not args.execute:
        print("\n(dry-run) 未写入 GitHub、未创建 worktree、未调用 Agent；加 --execute 执行。")
        return 0

    if worktree.exists():
        print(f"⛔ worktree 已存在：{worktree}")
        return 1

    if command and command[0] == "dsh" and not os.environ.get("DEEPSEEK_API_KEY"):
        print("⛔ 缺少 DEEPSEEK_API_KEY：headless 不使用交互式凭据。")
        print("   配置方式：export DEEPSEEK_API_KEY、~/.config/marketUS/deepseek.env，或 Keychain。")
        return 1

    if not command or shutil.which(command[0]) is None:
        print(f"⛔ 找不到 Agent 命令：{command[0] if command else '<empty>'}")
        return 1

    run_id = make_run_id(number)
    set_status(number, "status:claimed")
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
        set_status(number, "status:blocked")
        comment(number, f"⛔ 创建 worktree 失败：{error}")
        return 1

    set_status(number, "status:in-progress")

    run_dir = RUN_ROOT / str(number)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    env = os.environ.copy()
    env["AGENT_RUN_ID"] = run_id
    env["TICKET_NUMBER"] = str(number)

    timeout_seconds = args.timeout * 60
    command = agent_command()
    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            cwd=str(worktree),
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
        )
        (run_dir / "agent.log").write_text(result.stdout, encoding="utf-8")
        (run_dir / "agent.stderr.log").write_text(result.stderr, encoding="utf-8")
        agent_failed = result.returncode != 0
    except subprocess.TimeoutExpired:
        set_status(number, "status:blocked")
        comment(number, f"⛔ Agent 超时（{args.timeout} 分钟）。日志：`.agent/runs/{number}/`")
        return 1
    except FileNotFoundError:
        raise SystemExit(f"找不到 Agent 命令：{command[0]}")

    prs = json.loads(
        run(["gh", "pr", "list", "--head", branch, "--json", "number,url,title", "--limit", "1"]).stdout or "[]"
    )

    if prs:
        pr = prs[0]
        set_status(number, "status:in-review")
        comment(number, f"✅ PR 已创建：{pr['url']}\n\n日志：`.agent/runs/{number}/`")
        notify("MarketUS PR 已创建", f"#{number} {pr['url']}")
        print(f"PR: {pr['url']}")
        return 0

    set_status(number, "status:blocked")
    reason = "Agent 退出码非 0" if agent_failed else "未检测到 PR"
    comment(number, f"⛔ {reason}。worktree: `{worktree}`，日志：`.agent/runs/{number}/`")
    print(f"⛔ {reason}")
    return 1



ISSUE_PATTERN = re.compile(r"(?:Fixes|Closes|Refs)\s*#(\d+)", re.IGNORECASE)
VERDICT_PATTERN = re.compile(r"##\s*Verdict:\s*(APPROVE|REQUEST_CHANGES)", re.IGNORECASE)


def extract_issue_number(pr: dict[str, Any]) -> int | None:
    branch = pr.get("headRefName", "")
    match = re.match(r"(?:ai|human)/(\d+)-", branch)
    if match:
        return int(match.group(1))
    match = ISSUE_PATTERN.search(pr.get("body") or "")
    if match:
        return int(match.group(1))
    return None


def parse_verdict(text: str) -> str | None:
    match = VERDICT_PATTERN.search(text)
    return match.group(1).upper() if match else None


def agent_text(output: str) -> str:
    """dsh --json 输出 NDJSON；取 final 事件，否则原样返回。"""
    final: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "final" and event.get("text"):
            final.append(str(event["text"]))
    return "\n".join(final) if final else output


def run_review_agent(prompt: str, run_dir: Path, timeout_minutes: int) -> tuple[str, int]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    load_agent_secrets()

    command = agent_command()
    env = os.environ.copy()
    env["AGENT_RUN_ID"] = run_dir.name
    if command and command[0] == "dsh":
        env["DSH_PERMISSION_MODE"] = "read-only"

    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_minutes * 60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "reviewer timeout", 1
    except FileNotFoundError:
        return f"agent not found: {command[0] if command else '<empty>'}", 1

    (run_dir / "agent.log").write_text(result.stdout, encoding="utf-8")
    (run_dir / "agent.stderr.log").write_text(result.stderr, encoding="utf-8")
    return agent_text(result.stdout), result.returncode


def comment_pr(number: int, body: str) -> None:
    gh(["pr", "comment", str(number), "--body", body])


def cmd_review(args: argparse.Namespace) -> int:
    pr = gh_json(
        [
            "pr",
            "view",
            str(args.pr),
            "--json",
            "number,title,body,headRefName,headRefOid,state,url,comments",
        ]
    )
    if pr.get("state") != "OPEN":
        print(f"⛔ PR #{args.pr} 不是 OPEN")
        return 1

    issue_number = extract_issue_number(pr)
    if issue_number is None:
        print("⛔ 无法从分支名/PR body 解析 ticket 编号")
        return 1

    sha = pr["headRefOid"]
    marker = f"dispatcher-review pr={args.pr} sha={sha}"
    for existing in pr.get("comments", []):
        if marker in (existing.get("body") or ""):
            print(f"PR #{args.pr} 在 {sha[:8]} 上已评审过，跳过")
            return 0

    issue = issue_detail(issue_number)
    diff = gh(["pr", "diff", str(args.pr)]).stdout
    if len(diff) > 60_000:
        diff = diff[:60_000] + "\n\n[diff truncated]\n"

    prompt = (
        Path(".agent/prompts/reviewer.md").read_text(encoding="utf-8")
        + "\n\n---\n\n"
        + Path("docs/code-review.md").read_text(encoding="utf-8")
        + f"\n\n---\n\n# PR #{args.pr}: {pr['title']}\n\n{pr.get('body') or ''}"
        + f"\n\n---\n\n# Ticket #{issue_number}\n\n{issue.get('body') or ''}"
        + f"\n\n---\n\n# Diff\n\n```diff\n{diff}\n```\n"
    )

    print(f"PR       #{args.pr} {pr['title']}")
    print(f"ticket   #{issue_number}")
    print(f"sha      {sha[:8]}")
    print(f"diff     {len(diff)} chars")
    print(f"agent    {' '.join(agent_command())}")

    if not args.execute:
        print("\n(dry-run) 未调用 Reviewer；加 --execute 执行。")
        return 0

    labels = labels_of(issue)
    if "status:in-progress" in labels:
        set_status(issue_number, "status:in-review")

    run_dir = RUN_ROOT / f"review-{args.pr}"
    text, code = run_review_agent(prompt, run_dir, args.timeout)
    verdict = parse_verdict(text)
    print(f"verdict  {verdict or 'MISSING'} (exit={code})")

    if verdict is None:
        comment_pr(
            args.pr,
            f"## ⛔ Dispatcher Review 未能给出 verdict\n\n日志：`.agent/runs/review-{args.pr}/`\n\n"
            f"<!-- {marker} verdict=MISSING -->",
        )
        set_status(issue_number, "status:blocked")
        return 1

    body = (
        f"{text}\n\n---\n\n"
        f"<!-- {marker} verdict={verdict} -->\n"
        f"评审日志：`.agent/runs/review-{args.pr}/`\n"
    )
    comment_pr(args.pr, body)

    if verdict == "APPROVE":
        gh(["pr", "edit", str(args.pr), "--add-label", "merge:requested"])
        set_status(issue_number, "status:awaiting-human")
        notify("MarketUS PR 可合并", f"PR #{args.pr} 评审通过，等待你合并")
        print("✅ APPROVE — 已请求人工合并")
        return 0

    set_status(issue_number, "status:in-progress")
    print("⛔ REQUEST_CHANGES — 已退回实现")
    return 1


def cmd_postmerge(args: argparse.Namespace) -> int:
    prs = gh_json(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            "50",
            "--json",
            "number,title,body,headRefName,url",
        ]
    )
    changed = 0
    for pr in prs:
        issue_number = extract_issue_number(pr)
        if issue_number is None:
            continue
        try:
            issue = issue_detail(issue_number)
        except SystemExit:
            continue
        labels = labels_of(issue)
        if "status:done" in labels:
            continue
        print(f"merged PR #{pr['number']} → issue #{issue_number}")
        if args.dry_run:
            continue
        set_status(issue_number, "status:done")
        gh(["issue", "comment", str(issue_number), "--body", f"✅ 已合并：{pr['url']}"])
        branch = pr.get("headRefName", "")
        if "/" in branch:
            worktree = WORKTREE_ROOT / branch.split("/", 1)[1]
            if worktree.exists():
                run(["git", "worktree", "remove", str(worktree), "--force"])
        changed += 1
    print(f"postmerge: {changed} issue(s) updated")
    return 0



REVIEW_MARKER = re.compile(r"dispatcher-review pr=\d+ sha=([0-9a-f]+) verdict=(\w+)")
REPAIR_MARKER = "dispatcher-repair pr="


def pr_detail(number: int) -> dict[str, Any]:
    return gh_json(
        [
            "pr",
            "view",
            str(number),
            "--json",
            "number,title,body,headRefName,headRefOid,state,url,comments",
        ]
    )


def last_review(pr: dict[str, Any]) -> tuple[str | None, str | None, str]:
    for comment in reversed(pr.get("comments", [])):
        body = comment.get("body") or ""
        match = REVIEW_MARKER.search(body)
        if match:
            return match.group(1), match.group(2).upper(), body
    return None, None, ""


def repair_rounds(pr: dict[str, Any]) -> int:
    return sum(1 for comment in pr.get("comments", []) if REPAIR_MARKER in (comment.get("body") or ""))


def cmd_repair(args: argparse.Namespace) -> int:
    pr = pr_detail(args.pr)
    sha, verdict, feedback = last_review(pr)
    if verdict != "REQUEST_CHANGES":
        print(f"PR #{args.pr} 最近 verdict={verdict or 'none'}，无需修复")
        return 0

    rounds = repair_rounds(pr)
    issue_number = extract_issue_number(pr)
    if issue_number is None:
        print("⛔ 无法解析 ticket 编号")
        return 1
    issue = issue_detail(issue_number)

    print(f"PR       #{args.pr} {pr['title']}")
    print(f"ticket   #{issue_number}")
    print(f"sha      {sha[:8] if sha else '?'}")
    print(f"round    {rounds + 1}/2")
    if not args.execute:
        print("\n(dry-run) 未调用修复 Agent；加 --execute 执行。")
        return 0

    if rounds >= 2:
        set_status(issue_number, "status:blocked")
        comment_pr(
            args.pr,
            f"⛔ 自动修复已达 2 轮上限，升级人类处理。\n\n<!-- {REPAIR_MARKER}{args.pr} sha={sha} round=blocked -->",
        )
        notify("MarketUS 自动修复失败", f"PR #{args.pr} 两轮修复仍被退回")
        return 1

    branch = pr["headRefName"]
    worktree = WORKTREE_ROOT / f"repair-{args.pr}"
    if worktree.exists():
        run(["git", "worktree", "remove", str(worktree), "--force"])

    run(["git", "fetch", "origin", branch])
    cmd(["git", "worktree", "add", "-b", f"repair/{args.pr}", str(worktree), f"origin/{branch}"])
    base = run(["git", "-C", str(worktree), "rev-parse", "HEAD"]).stdout.strip()

    prompt = (
        Path(".agent/prompts/implementer.md").read_text(encoding="utf-8")
        + "\n\n---\n\n"
        + Path("AGENTS.md").read_text(encoding="utf-8")
        + f"\n\n---\n\n# Ticket #{issue_number}\n\n{issue.get('body') or ''}"
        + f"\n\n---\n\n# Reviewer feedback（必须修复）\n\n{feedback[:20000]}"
        + f"\n\n---\n\n# Run constraints\n\n- 只修复 Reviewer 指出的问题，不扩大范围\n- 只跑 `make build` + `make test-package`；UI 测试跳过\n- 提交并 push 到 `{branch}`，**不要合并**\n- 完成后立即退出\n"
    )
    run_dir = RUN_ROOT / f"repair-{args.pr}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    load_agent_secrets()
    command = agent_command()
    if not command or shutil.which(command[0]) is None:
        print(f"⛔ 找不到 Agent 命令：{command[0] if command else '<empty>'}")
        return 1
    env = os.environ.copy()
    env["AGENT_RUN_ID"] = f"repair-{args.pr}"
    if command[0] == "dsh":
        env["DSH_PERMISSION_MODE"] = os.environ.get("AGENT_PERMISSION_MODE", "danger-full-access")

    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            cwd=str(worktree),
            capture_output=True,
            timeout=args.timeout * 60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        set_status(issue_number, "status:blocked")
        comment_pr(args.pr, f"⛔ 修复 Agent 超时。日志：`.agent/runs/repair-{args.pr}/`")
        return 1

    (run_dir / "agent.log").write_text(result.stdout, encoding="utf-8")
    (run_dir / "agent.stderr.log").write_text(result.stderr, encoding="utf-8")
    head = run(["git", "-C", str(worktree), "rev-parse", "HEAD"]).stdout.strip()
    if head == base:
        set_status(issue_number, "status:blocked")
        comment_pr(args.pr, f"⛔ 修复未产生 commit。日志：`.agent/runs/repair-{args.pr}/`")
        return 1

    run(["git", "-C", str(worktree), "push", "origin", f"HEAD:{branch}"])
    set_status(issue_number, "status:in-review")
    comment_pr(
        args.pr,
        f"🤖 自动修复已提交 `{head[:8]}`，回到评审。\n\n<!-- {REPAIR_MARKER}{args.pr} sha={sha} round={rounds + 1} -->",
    )
    run(["git", "worktree", "remove", str(worktree), "--force"])
    print(f"repair pushed: {head[:8]} → {branch}")
    return 0


FALLBACK_REVIEW_MINUTES = 10


def age_minutes(iso_timestamp: str) -> float:
    created = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 60


def should_auto_review(labels: set[str], has_review: bool, age: float) -> bool:
    """Return True only for stale in-progress PRs without a current-head review.

    Callers must filter to non-draft ai/* PRs and pass has_review based on the
    PR's current head SHA; this predicate only applies the label and age rules.
    """
    return not has_review and "status:in-progress" in labels and age > FALLBACK_REVIEW_MINUTES


def cmd_watch(_args: argparse.Namespace) -> int:
    """持久化跟踪 ai/* PR 的创建/合并/关闭/冲突/CI 失败，去重通知。"""
    state_path = Path(".agent/state/pr-watch.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    known: dict[str, str] = state.get("known", {})
    notified: set[str] = set(state.get("notified", []))
    initialized = bool(known)

    prs = gh_json(
        [
            "pr", "list", "--state", "open", "--limit", "50",
            "--json", "number,title,headRefName,headRefOid,mergeable,statusCheckRollup",
        ]
    )
    events: list[tuple[str, str, str]] = []
    current: dict[str, str] = {}
    for pr in prs:
        if not pr.get("headRefName", "").startswith("ai/"):
            continue
        n = str(pr["number"]); current[n] = "OPEN"
        sha = pr.get("headRefOid", "")
        if initialized and n not in known:
            events.append(("MarketUS PR 已创建", f"#{n} {pr['title']}", f"created:{n}"))
        if pr.get("mergeable") == "CONFLICTING":
            events.append(("MarketUS PR 有冲突", f"#{n} {pr['title']}", f"conflict:{n}:{sha}"))
        failed = [c for c in (pr.get("statusCheckRollup") or []) if c.get("conclusion") in ("FAILURE", "CANCELLED")]
        if failed:
            events.append(("MarketUS PR CI 失败", f"#{n} {len(failed)} 个 check 失败", f"ci:{n}:{sha}"))

    for n in set(known) - set(current):
        try:
            detail = gh_json(["pr", "view", n, "--json", "state,title,url"])
        except SystemExit:
            continue
        state_value = detail.get("state")
        if state_value == "MERGED":
            print(f"merged PR #{n}: running postmerge")
            cmd_postmerge(argparse.Namespace(dry_run=False))

    for title, message, key in events:
        if key in notified:
            continue
        notify(title, message)
        print(f"watch: {title} — {message}")
        notified.add(key)

    state_path.write_text(
        json.dumps({"known": current, "notified": sorted(notified)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


def flag_dor(issue: dict[str, Any], failures: list[str]) -> None:
    number = issue["number"]
    gh(["issue", "edit", str(number), "--add-label", "ai:needs-human-decision"])
    detail = gh_json(["issue", "view", str(number), "--json", "comments"])
    if any("<!-- dispatcher-dor -->" in (c.get("body") or "") for c in detail.get("comments", [])):
        return
    body = "⛔ **DoR 未通过，暂不派发**\n\n" + "\n".join(f"- {f}" for f in failures) + "\n\n<!-- dispatcher-dor -->"
    comment(number, body)
    notify("MarketUS DoR 未通过", f"#{number} 需要补充字段")


def cmd_run(args: argparse.Namespace) -> int:
    state_dir = Path(".agent/state")
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dispatcher.lock"
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("另一个 Dispatcher 正在运行，跳过本轮")
        return 0

    try:
        cmd_postmerge(argparse.Namespace(dry_run=args.dry_run))
        cmd_watch(argparse.Namespace())

        prs = gh_json(
            [
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                "20",
                "--json",
                "number,body,headRefName,isDraft",
            ]
        )
        for pr in prs:
            if not pr.get("headRefName", "").startswith("ai/"):
                continue
            issue_number = extract_issue_number(pr)
            if issue_number is None:
                continue
            try:
                linked = issue_detail(issue_number)
            except SystemExit:
                continue
            if "status:in-progress" not in labels_of(linked):
                continue
            detail = pr_detail(pr["number"])
            _, verdict, _ = last_review(detail)
            if verdict == "REQUEST_CHANGES":
                print(f"repair PR #{pr['number']} (issue #{issue_number})")
                cmd_repair(
                    argparse.Namespace(
                        pr=pr["number"],
                        execute=not args.dry_run,
                        timeout=args.timeout,
                    )
                )
                return 0

        ready = sorted(list_issues("status:ready"), key=lambda issue: (priority_rank(issue), issue["number"]))
        for issue in ready:
            failures = validate(issue)
            if failures:
                print(f"DoR failed #{issue['number']}: {'; '.join(failures)}")
                if not args.dry_run:
                    flag_dor(issue, failures)
                continue
            if "ai:needs-human-decision" in labels_of(issue) and not args.dry_run:
                gh(["issue", "edit", str(issue["number"]), "--remove-label", "ai:needs-human-decision"])
            print(f"dispatch #{issue['number']} {issue['title']}")
            cmd_once(
                argparse.Namespace(
                    issue=issue["number"],
                    execute=not args.dry_run,
                    timeout=args.timeout,
                )
            )
            break

        prs = gh_json(
            [
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                "20",
                "--json",
                "number,body,headRefName,isDraft,createdAt",
            ]
        )
        for pr in prs:
            if pr.get("isDraft") or not pr.get("headRefName", "").startswith("ai/"):
                continue
            issue_number = extract_issue_number(pr)
            if issue_number is None:
                continue
            try:
                issue = issue_detail(issue_number)
            except SystemExit:
                continue
            labels = labels_of(issue)
            if "status:in-review" not in labels:
                if "status:in-progress" not in labels:
                    continue
                created_at = pr.get("createdAt")
                if not isinstance(created_at, str) or not created_at:
                    continue
                try:
                    age = age_minutes(created_at)
                except ValueError:
                    continue
                try:
                    detail = pr_detail(pr["number"])
                except SystemExit:
                    continue
                sha, verdict, _ = last_review(detail)
                has_review = verdict is not None and sha == detail.get("headRefOid")
                if not should_auto_review(labels, has_review, age):
                    continue
                print(f"fallback review PR #{pr['number']} (in-progress, no current-head review)")
            print(f"review PR #{pr['number']} (issue #{issue_number})")
            cmd_review(
                argparse.Namespace(
                    pr=pr["number"],
                    execute=not args.dry_run,
                    timeout=args.timeout,
                )
            )
            break
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    return 0


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

    repair_parser = subparsers.add_parser("repair", help="对 REQUEST_CHANGES 的 PR 运行修复 Agent（默认 dry-run）")
    repair_parser.add_argument("--pr", type=int, required=True)
    repair_parser.add_argument("--execute", action="store_true")
    repair_parser.add_argument("--timeout", type=int, default=90)

    subparsers.add_parser("watch", help="检查 open PR 冲突/CI 失败并通知")

    review_parser = subparsers.add_parser("review", help="对 PR 运行只读 Reviewer（默认 dry-run）")
    review_parser.add_argument("--pr", type=int, required=True)
    review_parser.add_argument("--execute", action="store_true")
    review_parser.add_argument("--timeout", type=int, default=30)

    postmerge_parser = subparsers.add_parser("postmerge", help="收尾已合并 PR 对应的 issue")
    postmerge_parser.add_argument("--dry-run", action="store_true")

    run_parser = subparsers.add_parser("run", help="单次巡检：postmerge → dispatch → review")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--timeout", type=int, default=90)

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status(args)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "once":
        return cmd_once(args)
    if args.command == "watch":
        return cmd_watch(args)
    if args.command == "repair":
        return cmd_repair(args)
    if args.command == "review":
        return cmd_review(args)
    if args.command == "postmerge":
        return cmd_postmerge(args)
    if args.command == "run":
        return cmd_run(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
