"""Phase 1c-ii/iii: scheduler shadow planning and opt-in execution.

The scheduler turns the current issue labels plus open PRs into the jobs the
dispatcher *would* want, then enqueues them into the Phase 1c-i job store
(:func:`shadow_tick`).  Shadow planning never claims a job, never runs an agent
and never writes to GitHub.

Phase 1c-iii adds opt-in execution on top of that queue (:func:`run_once`):
one CAS claim per tick, a heartbeat thread that renews the lease, and a
delegated ``dispatcher.py`` subprocess selected by role.  Execution is off by
default; ``dispatcher.py run`` only uses it when ``MARKETUS_SCHEDULER=1`` is
set, and ``dispatcher.py scheduler run`` must be invoked explicitly.

Job ids come from :func:`jobs.derive_job_id` and are stable by construction:

* ``status:ready`` yields an ``implementer`` job with ``pr=None`` and an empty
  ``base_sha``.  The base commit is chosen when the job is executed; binding
  the moving ``origin/main`` SHA at plan time would mint a new job on every
  upstream commit and break "repeated ticks do not duplicate work".
* ``status:in-review`` yields a ``reviewer`` job and ``status:in-progress``
  yields a ``repair`` job.  Both bind the linked PR number and ``headRefOid``:
  a new head is new work, while a base retarget alone is not.  ``baseRefOid``
  is stored as row metadata; the store's fourth identity slot receives the head.
* The prompt hash binds the role prompt file content.  Editing a prompt
  deliberately creates a new job identity so an old row cannot silently run
  with a stale prompt.

Inputs may be passed in (tests, callers) or read through read-only ``gh``
list commands.  Shadow planning has no write path to GitHub; execution only
writes GitHub through the delegated ``once`` / ``review`` / ``repair`` command
and never merges.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobs import JOB_STATES, JobStore, derive_job_id

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCHER_PATH = Path(__file__).resolve().with_name("dispatcher.py")

# repair reuses the implementer prompt, matching config/agents.yaml.
PROMPT_PATHS: dict[str, Path] = {
    "implementer": REPO_ROOT / ".agent/prompts/implementer.md",
    "reviewer": REPO_ROOT / ".agent/prompts/reviewer.md",
    "repair": REPO_ROOT / ".agent/prompts/implementer.md",
}

# Order is precedence: labels are normally exclusive, but a transient overlap
# must resolve deterministically.  ready wins because it is the dispatcher's
# explicit "start work" signal; review wins over a stale in-progress label.
STATUS_ROLES: tuple[tuple[str, str], ...] = (
    ("status:ready", "implementer"),
    ("status:in-review", "reviewer"),
    ("status:in-progress", "repair"),
)
STATUS_ROLE = dict(STATUS_ROLES)
ROLE_ORDER = {role: index for index, role in enumerate(("implementer", "reviewer", "repair"))}

ACTIVE_JOB_STATES = frozenset({"queued", "leased", "running", "reducing", "stale"})
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

GH_ISSUE_FIELDS = "number,title,body,labels,state,url"
GH_ISSUE_LIMIT = 50
# gh issue list exposes no authorAssociation; status labels are maintainer
# applied, so the untrusted code path is the PR association below.
# `gh pr list --json` does not expose authorAssociation, so PRs are read from
# the REST pulls endpoint (which returns author_association plus head/base repo).
GH_PR_PAGE_SIZE = 100

PR_BRANCH_PATTERN = re.compile(r"(?:ai|human)/(\d+)-", re.IGNORECASE)
PR_ISSUE_PATTERN = re.compile(r"(?:Fixes|Closes|Refs)\s*#(\d+)", re.IGNORECASE)

# Execution (Phase 1c-iii): one claim per tick, lease renewed while a command runs.
HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 7200.0
MAX_ERROR_CHARS = 2000
# dispatcher.py uses 2 for "another process already holds the per-PR lock".
EXIT_LOCK_BUSY = 2
# GitHub infrastructure failures are retryable: back off 60s per attempt and
# cap the wait at 15 minutes.  A busy per-PR lock only needs a short pause.
GITHUB_INFRA_RETRY_BASE_SECONDS = 60.0
GITHUB_INFRA_RETRY_MAX_SECONDS = 900.0
LOCK_BUSY_RETRY_SECONDS = 5.0
GITHUB_INFRA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btimeout\b|\btimed?\s+out\b", re.IGNORECASE),
    re.compile(r"\bconnection\b", re.IGNORECASE),
    re.compile(
        r"\brate[\s_-]?limit(?:ed|ing)?\b|\btoo many requests\b", re.IGNORECASE
    ),
    re.compile(r"\bHTTP(?:/\d(?:\.\d)?)?\s*[: ]?\s*5\d{2}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:status|response)(?:\s+code)?\s*[:=]?\s*5\d{2}\b", re.IGNORECASE
    ),
    re.compile(
        r"\b5\d{2}\s+(?:internal server error|bad gateway|service unavailable|"
        r"gateway timeout)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bgraphql\b[^\n]*\b(?:network|timeout|timed out|connection)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bnetwork (?:error|is unreachable)\b", re.IGNORECASE),
)


class SchedulerError(RuntimeError):
    """Raised when the read-only inputs needed for a shadow tick fail."""


@dataclass(frozen=True)
class DesiredJob:
    """One job implied by the current labels/PRs, before DB comparison."""

    job_id: str
    role: str
    ticket: int
    pr: int | None
    base_sha: str
    head_sha: str
    prompt_hash: str
    source_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "role": self.role,
            "ticket": self.ticket,
            "pr": self.pr,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "prompt_hash": self.prompt_hash,
            "source_status": self.source_status,
        }


def _label_names(issue: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for label in issue.get("labels") or []:
        if isinstance(label, Mapping):
            name = label.get("name")
        else:
            name = label
        if name:
            names.add(str(name))
    return names


def _issue_number(issue: Mapping[str, Any]) -> int | None:
    try:
        number = int(issue.get("number"))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _pr_number(pr: Mapping[str, Any]) -> int | None:
    return _issue_number(pr)


def _is_open(item: Mapping[str, Any]) -> bool:
    return str(item.get("state") or "open").lower() == "open"


def _is_open_pr(pr: Mapping[str, Any]) -> bool:
    """Return True only for open, non-draft, trusted same-repo PRs.

    Missing provenance fails closed: a PR without an explicit same-repo marker
    and a trusted author association is ignored rather than planned.  This
    gate runs before ``_prs_by_ticket`` picks the highest PR number, so an
    external fork PR can never win by number.
    """
    return (
        _is_open(pr)
        and not bool(pr.get("isDraft"))
        and pr.get("isCrossRepository") is False
        and str(pr.get("authorAssociation") or "").strip().upper()
        in TRUSTED_ASSOCIATIONS
    )


def _planned_status(issue: Mapping[str, Any]) -> str | None:
    labels = _label_names(issue)
    for status, _role in STATUS_ROLES:
        if status in labels:
            return status
    return None


def _pr_ticket(pr: Mapping[str, Any]) -> int | None:
    branch = str(pr.get("headRefName") or "")
    match = PR_BRANCH_PATTERN.match(branch)
    if match:
        return int(match.group(1))
    match = PR_ISSUE_PATTERN.search(str(pr.get("body") or ""))
    if match:
        return int(match.group(1))
    return None


def _pr_sha(pr: Mapping[str, Any] | None, key: str, fallback: str) -> str:
    if not pr:
        return ""
    return str(pr.get(key) or pr.get(fallback) or "").strip()


def _pr_base_sha(pr: Mapping[str, Any] | None) -> str:
    return _pr_sha(pr, "baseRefOid", "base_sha")


def _pr_head_sha(pr: Mapping[str, Any] | None) -> str:
    return _pr_sha(pr, "headRefOid", "head_sha")


def _identity_sha(role: str, head_sha: str) -> str:
    """Commit bound into the job id: head for reviewer/repair, empty otherwise."""
    return "" if role == "implementer" else head_sha


def _prs_by_ticket(prs: Iterable[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    """Pick the newest trusted open PR per ticket (highest PR number wins).

    Untrusted / cross-repository PRs are removed by ``_is_open_pr`` before the
    number comparison, so they cannot shadow a legitimate linked PR.
    """
    selected: dict[int, Mapping[str, Any]] = {}
    for pr in prs or []:
        if not _is_open_pr(pr):
            continue
        ticket = _pr_ticket(pr)
        number = _pr_number(pr)
        if ticket is None or number is None:
            continue
        current = selected.get(ticket)
        if current is None or number > int(_pr_number(current) or 0):
            selected[ticket] = pr
    return selected


def _file_hash(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        # A missing prompt is an execution problem, not a reason to abort the
        # read-only shadow tick.  The empty hash is visible in the job payload.
        return ""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _resolve_prompt_hashes(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for role, path in PROMPT_PATHS.items():
        override = (overrides or {}).get(role)
        hashes[role] = str(override) if override is not None else _file_hash(path)
    return hashes


def _planned(
    issues: Iterable[Mapping[str, Any]],
    prs: Iterable[Mapping[str, Any]],
    prompt_hashes: Mapping[str, str] | None = None,
) -> tuple[list[DesiredJob], list[dict[str, Any]]]:
    """Return ``(desired jobs, skipped candidates)`` for one tick."""
    hashes = _resolve_prompt_hashes(prompt_hashes)
    prs_by_ticket = _prs_by_ticket(prs)
    planned: list[DesiredJob] = []
    missing_pr: list[dict[str, Any]] = []
    seen: set[int] = set()

    for issue in issues or []:
        ticket = _issue_number(issue)
        if ticket is None or ticket in seen or not _is_open(issue):
            continue
        status = _planned_status(issue)
        if status is None:
            continue
        seen.add(ticket)
        role = STATUS_ROLE[status]
        pr: Mapping[str, Any] | None = None
        pr_number: int | None = None
        if role != "implementer":
            pr = prs_by_ticket.get(ticket)
            pr_number = _pr_number(pr) if pr is not None else None
            if pr is None or pr_number is None:
                missing_pr.append({"ticket": ticket, "status": status})
                continue
        base_sha = "" if role == "implementer" else _pr_base_sha(pr)
        head_sha = "" if role == "implementer" else _pr_head_sha(pr)
        prompt_hash = hashes.get(role, "")
        identity = _identity_sha(role, head_sha)
        planned.append(
            DesiredJob(
                job_id=derive_job_id(role, ticket, pr_number, identity, prompt_hash),
                role=role, ticket=ticket, pr=pr_number,
                base_sha=base_sha, head_sha=head_sha,
                prompt_hash=prompt_hash, source_status=status,
            )
        )

    planned.sort(key=lambda job: (job.ticket, ROLE_ORDER[job.role]))
    missing_pr.sort(key=lambda item: item["ticket"])
    return planned, missing_pr


def plan_jobs(
    issues: Iterable[Mapping[str, Any]],
    prs: Iterable[Mapping[str, Any]],
    *,
    prompt_hashes: Mapping[str, str] | None = None,
) -> list[DesiredJob]:
    """Map ready/in-review/in-progress issues to desired jobs.

    The function is pure: it reads no DB and performs no I/O beyond hashing
    the prompt files (unless ``prompt_hashes`` overrides them).  Issues whose
    status needs a PR but have no open linked PR are omitted; use
    :func:`shadow_tick` to see them in the ``missing_pr`` diff.
    """
    planned, _missing = _planned(issues, prs, prompt_hashes=prompt_hashes)
    return planned


def _job_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "job_id": row.get("job_id"),
        "role": row.get("role"),
        "ticket": int(row.get("ticket") or 0),
        "pr": row.get("pr"),
        "state": row.get("state"),
    }


def shadow_tick(
    store: JobStore,
    issues: Iterable[Mapping[str, Any]] | None = None,
    prs: Iterable[Mapping[str, Any]] | None = None,
    *,
    prompt_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Enqueue the desired jobs once and report what changed.

    Existing rows are never claimed, updated or deleted.  A desired job that
    already exists in any state is counted as ``skipped``; a non-queued state
    is additionally reported under ``diff.state_mismatch`` so an operator can
    see that shadow enqueue did not reset a finished/running row.  Active DB
    rows that no longer correspond to any desired job are reported as
    ``diff.orphaned`` (the shadow does not cancel them).
    """
    if issues is None:
        issues = read_issues()
    if prs is None:
        prs = read_prs()

    desired, missing_pr = _planned(issues, prs, prompt_hashes=prompt_hashes)
    existing_rows = store.list_jobs()
    existing = {str(row["job_id"]): row for row in existing_rows}

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    state_mismatch: list[dict[str, Any]] = []
    for job in desired:
        row = existing.get(job.job_id)
        if row is None:
            row = store.enqueue(
                job.role,
                job.ticket,
                job.pr,
                job.base_sha,
                head_sha=job.head_sha,
                prompt_hash=job.prompt_hash,
                job_id=job.job_id,
            )
            existing[job.job_id] = row
            created.append(row)
            continue
        skipped.append(row)
        if row.get("state") != "queued":
            state_mismatch.append(
                {**_job_summary(row), "desired_state": "queued"}
            )

    desired_ids = {job.job_id for job in desired}
    db_counts = {state: 0 for state in JOB_STATES}
    for row in existing.values():
        state = str(row.get("state") or "")
        db_counts[state] = db_counts.get(state, 0) + 1

    orphaned = sorted(
        (
            _job_summary(row)
            for job_id, row in existing.items()
            if job_id not in desired_ids and row.get("state") in ACTIVE_JOB_STATES
        ),
        key=lambda item: str(item["job_id"]),
    )

    diff = {
        "created": [_job_summary(row) for row in created],
        "unchanged": [_job_summary(row) for row in skipped],
        "state_mismatch": state_mismatch,
        "orphaned": orphaned,
        "missing_pr": missing_pr,
    }
    return {
        "db_path": str(getattr(store, "db_path", "")),
        "desired": [job.to_dict() for job in desired],
        "created": len(created),
        "skipped": len(skipped),
        "missing_pr": len(missing_pr),
        "diff": diff,
        "db_counts": db_counts,
    }


# -- execution (Phase 1c-iii) ------------------------------------------------


def default_owner() -> str:
    """Return the lease owner used when the caller does not name one."""
    return f"scheduler-{os.getpid()}"


def _truncate(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Keep at most ``limit`` characters, preferring the actionable tail."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return "…" + value[-(limit - 1):]


def _combined_output(stdout: str, stderr: str) -> str:
    """Label and join captured stdout/stderr; empty streams are omitted."""
    sections = []
    for label, value in (("stdout", stdout), ("stderr", stderr)):
        text = str(value or "").strip()
        if text:
            sections.append(f"{label}:\n{text}")
    return "\n".join(sections)


def is_github_infrastructure_error(text: str) -> bool:
    """Return True when the delegated command failed on GitHub infrastructure.

    Covered failures are timeouts, connection errors, rate limits, HTTP 5xx
    responses and GraphQL network errors.  The caller retries these with
    backoff instead of treating them as a failed implementation attempt.
    """
    value = str(text or "")
    return any(pattern.search(value) for pattern in GITHUB_INFRA_PATTERNS)


def github_retry_delay(attempt: int) -> float:
    """Return ``60s * attempt`` capped at 15 minutes (attempt is 1-based)."""
    number = max(1, int(attempt or 0))
    return min(
        GITHUB_INFRA_RETRY_BASE_SECONDS * number,
        GITHUB_INFRA_RETRY_MAX_SECONDS,
    )


def role_command(job: Mapping[str, Any]) -> list[str]:
    """Build the delegated dispatcher argv for one job, never through a shell."""
    role = str(job.get("role") or "").strip().lower()
    ticket = int(job.get("ticket") or 0)
    if ticket <= 0:
        raise ValueError("job ticket 必须是正整数")
    if role == "implementer":
        return [
            sys.executable,
            str(DISPATCHER_PATH),
            "once",
            "--issue",
            str(ticket),
            "--execute",
        ]
    if role in ("reviewer", "repair"):
        pr_value = job.get("pr")
        if pr_value in (None, ""):
            raise ValueError(f"{role} job 缺少 pr")
        pr = int(pr_value)
        if pr <= 0:
            raise ValueError(f"{role} job 的 pr 必须是正整数")
        verb = "review" if role == "reviewer" else "repair"
        return [
            sys.executable,
            str(DISPATCHER_PATH),
            verb,
            "--pr",
            str(pr),
            "--execute",
        ]
    raise ValueError(f"未知 role：{role or '-'}")


def _execution_job(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "job_id": row.get("job_id"),
        "role": row.get("role"),
        "ticket": int(row.get("ticket") or 0),
        "pr": row.get("pr"),
        "state": row.get("state"),
        "attempt": int(row.get("attempt") or 0),
        "retry_at": row.get("retry_at"),
    }


def _heartbeat_placeholder(interval: float) -> dict[str, Any]:
    return {
        "started": False,
        "stopped": False,
        "alive": False,
        "beats": 0,
        "rejected": 0,
        "errors": 0,
        "interval_seconds": interval,
    }


def _oldest_queued(store: JobStore) -> dict[str, Any] | None:
    """Read-only preview of the oldest ``queued`` row (dry-run only)."""
    rows = store.list_jobs(state="queued")
    if not rows:
        return None
    return min(
        rows,
        key=lambda row: (float(row.get("created_at") or 0), str(row.get("job_id") or "")),
    )


class _LeaseHeartbeat:
    """Renew one job's lease from a daemon thread until :meth:`stop` is called."""

    def __init__(
        self,
        store: JobStore,
        job_id: str,
        owner: str,
        lease_seconds: float,
        interval: float,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self.owner = owner
        self.lease_seconds = lease_seconds
        self.interval = interval
        self.beats = 0
        self.rejected = 0
        self.errors = 0
        self.started = False
        self.stopped = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"scheduler-heartbeat-{job_id}",
            daemon=True,
        )

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()
        self.started = True

    def stop(self) -> None:
        """Set the stop flag and join; safe to call from the tick's finally."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.interval * 2))
        self.stopped = not self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                renewed = self.store.heartbeat(
                    self.job_id, self.owner, self.lease_seconds
                )
            except (OSError, sqlite3.Error, ValueError):
                self.errors += 1
                continue
            if renewed:
                self.beats += 1
            else:
                # Lease reclaimed or job already finished: no point renewing.
                self.rejected += 1
                return

    def result(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "stopped": self.stopped,
            "alive": self.alive,
            "beats": self.beats,
            "rejected": self.rejected,
            "errors": self.errors,
            "interval_seconds": self.interval,
        }


def _refresh_execution_state(
    store: JobStore, result: dict[str, Any], job_id: str
) -> None:
    current = store.get_job(job_id)
    if current is None:
        return
    result["job"] = _execution_job(current)
    result["state"] = current.get("state")
    result["retry_at"] = current.get("retry_at")


def _apply_retry_recovery(
    recovery: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    job: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the optional pre-requeue recovery hook, normalizing failures."""
    if recovery is None:
        # Requeuing without restoring the issue would make the next claim fail
        # admission; fail closed instead of silently breaking the retry.
        return {
            "ok": False,
            "retryable": False,
            "skipped": False,
            "reason": "未配置 retry recovery，拒绝在状态未恢复时 requeue",
        }
    try:
        outcome = recovery(job)
    except (Exception, SystemExit) as error:
        return {
            "ok": False,
            "retryable": False,
            "skipped": False,
            "reason": f"{type(error).__name__}: {error}",
        }
    if not isinstance(outcome, Mapping):
        return {
            "ok": False,
            "retryable": False,
            "skipped": False,
            "reason": f"recovery 返回值无效：{type(outcome).__name__}",
        }
    result = dict(outcome)
    result.setdefault("ok", False)
    result.setdefault("retryable", True)
    result.setdefault("skipped", False)
    result["reason"] = str(result.get("reason") or "")
    return result


def run_once(
    store: JobStore,
    execute: bool = True,
    owner: str | None = None,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    *,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    recovery: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Claim at most one queued job, run its role command, and record the outcome.

    ``execute=False`` is a read-only preview: it inspects the oldest queued
    job without claiming it or running a command.  The real path uses the
    store's CAS claim, keeps the lease alive from a heartbeat thread while the
    delegated ``dispatcher.py`` subprocess runs, then finalizes the row:

    * exit 0 -> ``done``
    * exit 2 (per-PR lock busy) -> back to ``queued`` with a short backoff via
      :meth:`JobStore.requeue`, never ``failed``
    * a GitHub infrastructure failure (timeout, connection, rate limit,
      HTTP 5xx, GraphQL network) -> run the ``recovery`` hook first
      (restore issue status, clean safe worktree), then ``queued`` with
      exponential-ish backoff (60s per attempt, capped at 15 minutes); if
      recovery fails or is not configured, the job becomes ``failed`` with
      ``retryable=False`` and the reason is recorded instead of silently
      requeueing a job whose issue cannot be claimed
    * any other exit or start failure -> ``failed`` with the combined
      stdout/stderr tail

    The scheduler never writes GitHub itself (only the delegated command may)
    and never merges.  The heartbeat is always stopped before the row is
    finalized.
    """
    interval = float(heartbeat_interval)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("heartbeat_interval 必须大于 0")
    lease = float(lease_seconds)
    if not math.isfinite(lease) or lease <= 0:
        raise ValueError("lease_seconds 必须大于 0")
    owner_value = str(owner or "").strip() or default_owner()

    if not execute:
        job = _oldest_queued(store)
        if job is None:
            return {
                "action": "idle",
                "job": None,
                "state": None,
                "owner": owner_value,
                "lease_seconds": lease,
                "command": [],
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "retry_at": None,
                "retry_delay": None,
                "error": "",
                "finished": False,
                "executed": False,
                "heartbeat": _heartbeat_placeholder(interval),
            }
        result: dict[str, Any] = {
            "action": "dry_run",
            "job": _execution_job(job),
            "state": job.get("state"),
            "owner": owner_value,
            "lease_seconds": lease,
            "command": [],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "retry_at": None,
            "retry_delay": None,
            "error": "",
            "finished": False,
            "executed": False,
            "heartbeat": _heartbeat_placeholder(interval),
        }
        try:
            result["command"] = role_command(job)
        except (TypeError, ValueError) as error:
            result["error"] = _truncate(f"invalid job: {error}")
        return result

    claimed = store.claim(owner_value, lease)
    if claimed is None:
        return {
            "action": "idle",
            "job": None,
            "state": None,
            "owner": owner_value,
            "lease_seconds": lease,
            "command": [],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "retry_at": None,
            "retry_delay": None,
            "error": "",
            "finished": False,
            "executed": False,
            "heartbeat": _heartbeat_placeholder(interval),
        }

    job_id = str(claimed["job_id"])
    attempt = int(claimed.get("attempt") or 0)
    result = {
        "action": "executed",
        "job": _execution_job(claimed),
        "state": claimed.get("state"),
        "owner": owner_value,
        "lease_seconds": lease,
        "command": [],
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "retry_at": None,
        "retry_delay": None,
        "error": "",
        "finished": False,
        "executed": False,
        "heartbeat": _heartbeat_placeholder(interval),
    }
    try:
        command = role_command(claimed)
    except (TypeError, ValueError) as error:
        message = _truncate(f"invalid job: {error}")
        result["action"] = "failed"
        result["error"] = message
        result["finished"] = store.finish(
            job_id, "failed", message, owner=owner_value, attempt=attempt
        )
        _refresh_execution_state(store, result, job_id)
        return result

    result["command"] = command
    heartbeat = _LeaseHeartbeat(store, job_id, owner_value, lease, interval)
    exit_code: int | None = None
    stderr = ""
    stdout = ""
    heartbeat.start()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, check=False
        )
        exit_code = int(completed.returncode)
        stderr = completed.stderr or ""
        stdout = completed.stdout or ""
    except OSError as error:
        stderr = str(error)
    finally:
        heartbeat.stop()
    combined = _combined_output(stdout, stderr)
    result["heartbeat"] = heartbeat.result()
    result["exit_code"] = exit_code
    result["stdout"] = _truncate(stdout)
    result["stderr"] = _truncate(stderr)
    result["executed"] = True

    if exit_code == 0:
        result["finished"] = store.finish(
            job_id, "done", owner=owner_value, attempt=attempt
        )
    elif exit_code == EXIT_LOCK_BUSY:
        # Another dispatcher already owns this PR; leave the work claimable.
        result["action"] = "requeued"
        result["retry_delay"] = LOCK_BUSY_RETRY_SECONDS
        result["finished"] = store.requeue(
            job_id,
            owner_value,
            attempt=attempt,
            error="",
            delay=LOCK_BUSY_RETRY_SECONDS,
        )
    elif exit_code is not None and is_github_infrastructure_error(combined):
        # GitHub had a transient outage.  Before retrying, put the issue back
        # in the state its role expects and clean a safe stale worktree;
        # otherwise the next claim would fail admission and burn the job.
        recovery_result = _apply_retry_recovery(recovery, claimed)
        result["recovery"] = recovery_result
        result["retryable"] = bool(recovery_result.get("retryable", True))
        if not recovery_result.get("ok") or not recovery_result.get("retryable", True):
            reason = str(recovery_result.get("reason") or "unknown recovery failure")
            message = _truncate(f"retry recovery failed: {reason}")
            result["action"] = "failed"
            result["retryable"] = False
            result["error"] = message
            result["finished"] = store.finish(
                job_id, "failed", message, owner=owner_value, attempt=attempt
            )
        else:
            message = _truncate(combined)
            delay = github_retry_delay(attempt)
            result["action"] = "requeued"
            result["retryable"] = True
            result["retry_delay"] = delay
            result["finished"] = store.requeue(
                job_id,
                owner_value,
                attempt=attempt,
                error=message,
                delay=delay,
            )
    else:
        message = _truncate(
            combined or f"delegated command exited {exit_code}"
        )
        result["action"] = "failed"
        result["error"] = message
        result["finished"] = store.finish(
            job_id, "failed", message, owner=owner_value, attempt=attempt
        )

    _refresh_execution_state(store, result, job_id)
    if not result["finished"] and not result["error"]:
        result["error"] = "lease lost before finish"
    return result


def _gh_json(args: list[str]) -> Any:
    """Run a read-only ``gh`` command and parse its JSON output."""
    command = ["gh", *args]
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise SchedulerError(f"无法执行 gh：{error}") from error
    if result.returncode != 0:
        raise SchedulerError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as error:
        raise SchedulerError(f"gh {' '.join(args)} 返回无效 JSON") from error


def _read_list(resource: str, fields: str, limit: int) -> list[dict[str, Any]]:
    data = _gh_json(
        [resource, "list", "--state", "open", "--limit", str(limit), "--json", fields]
    )
    return data if isinstance(data, list) else []


def read_issues() -> list[dict[str, Any]]:
    """Read open issues through ``gh issue list`` (read-only)."""
    return _read_list("issue", GH_ISSUE_FIELDS, GH_ISSUE_LIMIT)


def _repo_name(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    name = value.get("full_name")
    return str(name) if isinstance(name, str) else ""


def _normalize_pr(item: Mapping[str, Any]) -> dict[str, Any]:
    """Map a REST PR onto the same shape used by injected test inputs."""
    head = item.get("head")
    base = item.get("base")
    head = head if isinstance(head, Mapping) else {}
    base = base if isinstance(base, Mapping) else {}
    head_repo = _repo_name(head.get("repo"))
    base_repo = _repo_name(base.get("repo"))
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "body": item.get("body"),
        "headRefName": head.get("ref"),
        "headRefOid": head.get("sha"),
        "baseRefOid": base.get("sha"),
        "state": item.get("state"),
        "isDraft": item.get("draft"),
        "url": item.get("html_url"),
        "authorAssociation": item.get("author_association"),
        "isCrossRepository": (
            head_repo != base_repo if head_repo and base_repo else None
        ),
    }


def read_prs() -> list[dict[str, Any]]:
    """Read open PRs with provenance through the read-only REST endpoint."""
    pages = _gh_json(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{{owner}}/{{repo}}/pulls?state=open&per_page={GH_PR_PAGE_SIZE}",
        ]
    )
    return [
        _normalize_pr(item)
        for page in (pages if isinstance(pages, list) else [])
        if isinstance(page, list)
        for item in page
        if isinstance(item, Mapping)
    ]
