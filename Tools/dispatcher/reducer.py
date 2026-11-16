"""Phase 1b-i: idempotent reducer for the implementer commit/push path.

A job's ``scan -> commit -> push`` steps are recorded in
``.agent/jobs/<job_id>/operations.jsonl``; a re-run skips recorded steps and
resumes at the first failure.  Git state is re-checked too, so repeated calls
never duplicate a commit or force a push.  PR creation, comments, labels and
merges are out of scope, and this module never force-pushes.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

JOBS_ROOT = Path(".agent/jobs")
COMMITTER_NAME = "marketus-dispatcher"
COMMITTER_EMAIL = "dispatcher@users.noreply.github.com"
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

RunCallable = Callable[[list[str]], subprocess.CompletedProcess[str]]
ScanCallable = Callable[[Path], list[str]]
RangeScanCallable = Callable[[Path, str], list[str]]


@dataclass(frozen=True)
class ReduceResult:
    """Structured reducer outcome; ``reason`` is empty on success."""

    ok: bool
    commit_sha: str = ""
    reason: str = ""
    job_id: str = ""
    skipped: tuple[str, ...] = ()


def derive_job_id(role: str, issue: int, base_sha: str = "", branch: str = "") -> str:
    """Derive a deterministic job id from role, ticket, base and branch.

    HEAD/tree are deliberately excluded: a retry after a commit must reuse the
    same job directory and operation log.
    """
    digest = hashlib.sha256(
        f"{role}|{issue}|{base_sha}|{branch}".encode("utf-8")
    ).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-") or "job"
    return f"{slug}-{issue}-{digest}"


def issue_number(issue: dict[str, Any] | int) -> int:
    """Accept a ticket dict (dispatcher shape) or a bare issue number."""
    if isinstance(issue, int):
        return issue
    if isinstance(issue, dict):
        value = issue.get("number")
        if isinstance(value, int):
            return value
    return 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _dispatcher_helper(name: str) -> Callable[..., list[str]]:
    """Resolve a dispatcher secret helper without a circular top-level import."""
    for module_name in ("dispatcher", "__main__"):
        helper = getattr(sys.modules.get(module_name), name, None)
        if callable(helper):
            return helper
    try:
        import dispatcher as module  # noqa: PLC0415 - import-safe fallback
    except ImportError as error:  # pragma: no cover - dispatcher is checked in
        raise RuntimeError(f"无法加载 dispatcher.{name}") from error
    return getattr(module, name)


def _default_scanner(worktree: Path) -> list[str]:
    return _dispatcher_helper("scan_staged_secrets")(worktree)


def _default_range_scanner(worktree: Path, revision_range: str) -> list[str]:
    return _dispatcher_helper("scan_range_secrets")(worktree, revision_range)


class _OperationLog:
    """Append-only JSONL log for one job; reads tolerate partial/corrupt lines."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
        return entries

    def last(self, step: str, **fields: Any) -> dict[str, Any] | None:
        for item in reversed(self.records()):
            if item.get("step") != step or item.get("status") not in ("ok", "skipped"):
                continue
            if all(item.get(key) == value for key, value in fields.items()):
                return item
        return None

    def has(self, step: str, **fields: Any) -> bool:
        for item in self.records():
            if item.get("step") != step or item.get("status") not in ("ok", "skipped"):
                continue
            if all(item.get(key) == value for key, value in fields.items()):
                return True
        return False

    def append(self, *, step: str, status: str, reason: str = "", **fields: Any) -> None:
        record = {"ts": _utc_now(), "step": step, "status": status, "reason": reason}
        record.update(fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            # The git-state checks below keep the reducer idempotent even when
            # the audit log cannot be written.
            return


class Reducer:
    """Idempotent scan/commit/push for one job."""

    def __init__(
        self,
        *,
        runner: RunCallable | None = None,
        scanner: ScanCallable | None = None,
        range_scanner: RangeScanCallable | None = None,
        jobs_root: Path | None = None,
    ) -> None:
        self.runner = runner or _default_runner
        self.scanner = scanner or _default_scanner
        self.range_scanner = range_scanner or _default_range_scanner
        self.jobs_root = Path(jobs_root) if jobs_root is not None else JOBS_ROOT

    def commit_and_push(
        self,
        worktree: Path,
        branch: str,
        issue: dict[str, Any] | int,
        job_id: str,
        *,
        run_id: str = "",
    ) -> ReduceResult:
        """Scan, commit and push the worktree, skipping steps already recorded."""
        path = Path(worktree)
        number = issue_number(issue)
        if number <= 0:
            return ReduceResult(False, reason="issue 缺少有效的 number", job_id=job_id)
        if not JOB_ID_PATTERN.match(job_id or ""):
            return ReduceResult(False, reason=f"job_id 非法：{job_id!r}", job_id=job_id)
        valid_branch = BRANCH_PATTERN.match(branch or "") and ".." not in branch
        if not valid_branch or branch.endswith(("/", ".lock")):
            return ReduceResult(False, reason=f"branch 非法：{branch!r}", job_id=job_id)
        if not path.is_dir():
            return ReduceResult(False, reason=f"worktree 不存在：{path}", job_id=job_id)

        log = _OperationLog(self.jobs_root / job_id / "operations.jsonl")
        skipped: list[str] = []

        staged = self._git(path, "add", "-A")
        if staged.returncode != 0:
            reason = f"git add 失败：{staged.stderr.strip() or 'unknown'}"
            log.append(
                step="scan", status="failed", reason=reason,
                scope="staged", head=self._head(path),
            )
            return ReduceResult(False, self._head(path), reason, job_id)

        head = self._head(path)
        tree = self._tree(path)
        if not head or not tree:
            reason = "无法读取 git HEAD/tree"
            log.append(step="scan", status="failed", reason=reason, scope="staged")
            return ReduceResult(False, head, reason, job_id)

        scanned = log.last("scan", scope="staged")
        if scanned is not None and scanned.get("tree") == tree:
            skipped.append("scan")
        else:
            findings = self.scanner(path)
            if findings:
                reason = "密钥扫描命中：" + "; ".join(findings)
                log.append(
                    step="scan", status="failed", reason=reason,
                    scope="staged", head=head, tree=tree,
                )
                return ReduceResult(False, head, reason, job_id)
            log.append(step="scan", status="ok", scope="staged", head=head, tree=tree)

        staged_diff = self._git(path, "diff", "--cached", "--quiet")
        if staged_diff.returncode == 1:
            subject, body = self._commit_message(number, job_id, run_id)
            committed = self._git(
                path,
                "-c", f"user.name={COMMITTER_NAME}",
                "-c", f"user.email={COMMITTER_EMAIL}",
                "-c", "commit.gpgsign=false",
                "commit", "--no-verify", "-m", subject, "-m", body,
            )
            if committed.returncode != 0:
                reason = f"git commit 失败：{committed.stderr.strip() or 'unknown'}"
                log.append(step="commit", status="failed", reason=reason, head=head)
                return ReduceResult(False, head, reason, job_id, tuple(skipped))
            head = self._head(path)
            if not head:
                reason = "git commit 后无法读取 HEAD"
                log.append(step="commit", status="failed", reason=reason)
                return ReduceResult(False, "", reason, job_id, tuple(skipped))
            log.append(step="commit", status="ok", head=head, tree=tree, commit_sha=head)
        elif staged_diff.returncode != 0:
            reason = f"git diff 失败：{staged_diff.stderr.strip() or 'unknown'}"
            log.append(step="commit", status="failed", reason=reason, head=head)
            return ReduceResult(False, head, reason, job_id, tuple(skipped))
        else:
            skipped.append("commit")
            if not log.has("commit", head=head):
                log.append(step="commit", status="skipped", head=head, reason="没有待提交改动")

        remote = self._remote_head(path, branch)
        range_spec = self._range_spec(path, remote)
        if not range_spec:
            reason = "无法确定提交扫描范围"
            log.append(
                step="scan", status="failed", reason=reason,
                scope="range", head=head,
            )
            return ReduceResult(False, head, reason, job_id, tuple(skipped))
        findings = self.range_scanner(path, range_spec)
        if findings:
            reason = "密钥扫描命中：" + "; ".join(findings)
            log.append(
                step="scan", status="failed", reason=reason,
                scope="range", range=range_spec, head=head,
            )
            return ReduceResult(False, head, reason, job_id, tuple(skipped))
        log.append(step="scan", status="ok", scope="range", range=range_spec, head=head)

        recorded_push = log.last("push")
        if (
            recorded_push is not None
            and recorded_push.get("head") == head
            and remote == head
        ):
            skipped.append("push")
            return ReduceResult(True, head, "", job_id, tuple(skipped))

        if remote == head:
            skipped.append("push")
            if not log.has("push", head=head, branch=branch):
                log.append(
                    step="push", status="skipped", reason="远端已包含本地 HEAD",
                    head=head, branch=branch,
                )
            return ReduceResult(True, head, "", job_id, tuple(skipped))

        push_result = self._git(path, "push", "-u", "origin", f"HEAD:refs/heads/{branch}")
        if push_result.returncode != 0:
            reason = f"git push 失败：{push_result.stderr.strip() or 'unknown'}"
            log.append(
                step="push", status="failed", reason=reason,
                head=head, branch=branch, remote=remote,
            )
            return ReduceResult(False, head, reason, job_id, tuple(skipped))
        log.append(
            step="push", status="ok", head=head, branch=branch, remote=remote,
        )
        return ReduceResult(True, head, "", job_id, tuple(skipped))

    @staticmethod
    def _commit_message(number: int, job_id: str, run_id: str) -> tuple[str, str]:
        subject = f"chore(agent): capture work for #{number}"
        trailers = [f"Refs: #{number}", f"Job-Id: {job_id}"]
        if run_id:
            trailers.append(f"Agent-Run: {run_id}")
        return subject, "\n".join(trailers)

    def _git(self, worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self.runner(["git", "-C", str(worktree), *args])

    def _head(self, worktree: Path) -> str:
        result = self._git(worktree, "rev-parse", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""

    def _tree(self, worktree: Path) -> str:
        result = self._git(worktree, "write-tree")
        return result.stdout.strip() if result.returncode == 0 else ""

    def _remote_head(self, worktree: Path, branch: str) -> str:
        ref = f"refs/heads/{branch}"
        result = self._git(worktree, "ls-remote", "--heads", "origin", ref)
        if result.returncode != 0:
            return ""
        parts = [line.split() for line in (result.stdout or "").splitlines()]
        return next((item[0] for item in parts if len(item) >= 2 and item[1] == ref), "")

    def _range_spec(self, worktree: Path, remote: str) -> str:
        """Scan everything not on the remote; fall back to the branch fork point."""
        if remote:
            return f"{remote}..HEAD"
        base = self._git(worktree, "merge-base", "origin/main", "HEAD")
        base_sha = base.stdout.strip() if base.returncode == 0 else ""
        return f"{base_sha}..HEAD" if base_sha else ""
