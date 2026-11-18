"""Phase 1c-ii: scheduler shadow planning for the dispatcher.

The scheduler turns the current issue labels plus open PRs into the jobs the
dispatcher *would* want, then enqueues them into the Phase 1c-i job store.  It
stops there: this phase never claims a job, never runs an agent, and never
writes to GitHub (labels, comments, branches or PRs).  ``dispatcher.py
scheduler shadow`` is the only entry point and is not wired into ``run``.

Job ids come from :func:`jobs.derive_job_id` and are stable by construction:

* ``status:ready`` yields an ``implementer`` job with ``pr=None`` and an empty
  ``base_sha``.  The base commit is chosen when the job is executed; binding
  the moving ``origin/main`` SHA at plan time would mint a new job on every
  upstream commit and break "repeated ticks do not duplicate work".
* ``status:in-review`` yields a ``reviewer`` job and ``status:in-progress``
  yields a ``repair`` job.  Both bind the linked PR number and the PR's
  ``baseRefOid`` (the base SHA is immutable for a given review target).
* The prompt hash binds the role prompt file content.  Editing a prompt
  deliberately creates a new job identity so an old row cannot silently run
  with a stale prompt.

Inputs may be passed in (tests, callers) or read through read-only ``gh``
list commands.  The module has no write path to GitHub.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobs import JOB_STATES, JobStore, derive_job_id

REPO_ROOT = Path(__file__).resolve().parents[2]

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

GH_ISSUE_FIELDS = "number,title,body,labels,state,url"
GH_PR_FIELDS = "number,title,body,headRefName,headRefOid,baseRefOid,state,isDraft,url"
GH_ISSUE_LIMIT = 50
GH_PR_LIMIT = 200

PR_BRANCH_PATTERN = re.compile(r"(?:ai|human)/(\d+)-", re.IGNORECASE)
PR_ISSUE_PATTERN = re.compile(r"(?:Fixes|Closes|Refs)\s*#(\d+)", re.IGNORECASE)


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
    prompt_hash: str
    source_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "role": self.role,
            "ticket": self.ticket,
            "pr": self.pr,
            "base_sha": self.base_sha,
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
    return _is_open(pr) and not bool(pr.get("isDraft"))


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


def _pr_base_sha(pr: Mapping[str, Any] | None) -> str:
    if not pr:
        return ""
    value = pr.get("baseRefOid") or pr.get("base_sha") or ""
    return str(value).strip()


def _prs_by_ticket(prs: Iterable[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    """Pick the newest open PR per ticket (highest PR number wins)."""
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
        prompt_hash = hashes.get(role, "")
        planned.append(
            DesiredJob(
                job_id=derive_job_id(role, ticket, pr_number, base_sha, prompt_hash),
                role=role,
                ticket=ticket,
                pr=pr_number,
                base_sha=base_sha,
                prompt_hash=prompt_hash,
                source_status=status,
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
        raise SchedulerError(
            f"gh {' '.join(args)} failed: {result.stderr.strip()}"
        )
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


def read_prs() -> list[dict[str, Any]]:
    """Read open PRs through ``gh pr list`` (read-only)."""
    return _read_list("pr", GH_PR_FIELDS, GH_PR_LIMIT)


__all__ = [
    "ACTIVE_JOB_STATES",
    "DesiredJob",
    "PROMPT_PATHS",
    "ROLE_ORDER",
    "STATUS_ROLES",
    "SchedulerError",
    "plan_jobs",
    "read_issues",
    "read_prs",
    "shadow_tick",
]
