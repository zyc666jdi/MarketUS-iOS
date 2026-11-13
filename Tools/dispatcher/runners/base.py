"""Shared runner contract for the dispatcher (Phase 1a).

Adapters parse agent stdout into :class:`RunnerResult`; the dispatcher serialises
that object with :meth:`RunnerResult.to_payload` so every runner produces the
same ``output.schema.json`` document.  No third-party dependency is required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

SCHEMA_VERSION = 1
STATUSES = ("succeeded", "failed", "timeout")
VERDICTS = ("APPROVE", "REQUEST_CHANGES")

VERDICT_RE = re.compile(r"##\s*Verdict:\s*(APPROVE|REQUEST_CHANGES)", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{2,4}\s+(.+)$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+)$")
P1_RE = re.compile(r"\bP1\b|\bblockers?\b", re.IGNORECASE)
P2_P3_RE = re.compile(r"\bP[23]\b", re.IGNORECASE)
INLINE_P1_RE = re.compile(r"^(?:\[P1\]|P1)\b", re.IGNORECASE)
EMPTY_FINDINGS = {"无", "none", "n/a", "na", "—", "-", "暂无", "nothing"}
SUMMARY_LIMIT = 500


@dataclass(frozen=True)
class RunnerSpec:
    """A runner declared in ``config/agents.yaml``."""

    name: str
    command: tuple[str, ...]
    output_format: str
    final_event: str


@dataclass(frozen=True)
class RoleSpec:
    """A role declared in ``config/agents.yaml``."""

    name: str
    runner: str
    prompt: str
    capabilities: tuple[str, ...]
    runner_env: str | None = None
    sandbox: str | None = None


@dataclass
class RunnerResult:
    """Normalised agent outcome.

    ``text`` keeps the extracted reviewer body so existing markdown parsing in
    ``dispatcher.py`` keeps working.  The object is iterable as ``(text, code)``
    for backwards compatibility with the old ``run_review_agent`` return value.
    """

    status: str
    text: str
    returncode: int = 0
    verdict: str | None = None
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown runner status: {self.status}")
        if self.verdict not in (*VERDICTS, None):
            raise ValueError(f"unknown verdict: {self.verdict}")

    def __iter__(self):
        yield self.text
        yield self.returncode

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "result": {
                "verdict": self.verdict,
                "summary": self.summary,
                "findings": list(self.findings),
            },
            "usage": {"duration_ms": max(0, int(self.duration_ms))},
            "error": {"code": self.error_code, "retryable": self.retryable},
        }


@runtime_checkable
class Runner(Protocol):
    """Minimal adapter interface; concrete runners live in this package."""

    spec: RunnerSpec

    def command(self) -> list[str]:
        ...

    def parse(self, stdout: str, *, returncode: int = 0, duration_ms: int = 0) -> RunnerResult:
        ...


@dataclass(frozen=True)
class ResolvedRole:
    """The concrete runner selected for a role after env overrides."""

    role: RoleSpec
    runner: Runner
    runner_name: str


def build_job_input(
    *,
    job_id: str,
    role: str,
    prompt: str,
    ticket: int | None = None,
    pr: int | None = None,
    worktree: str | None = None,
    timeout_seconds: int = 1800,
    capabilities: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build the ``input.schema.json`` document passed to a runner."""

    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "role": role,
        "prompt": prompt,
        "ticket": ticket,
        "pr": pr,
        "worktree": str(worktree) if worktree is not None else None,
        "limits": {"timeout_seconds": max(1, int(timeout_seconds))},
        "capabilities": [str(item) for item in capabilities],
    }


def extract_verdict(text: str) -> str | None:
    match = VERDICT_RE.search(text)
    return match.group(1).upper() if match else None


def extract_findings(text: str) -> list[str]:
    """Collect P1/blocker bullets from a reviewer markdown body."""

    findings: list[str] = []
    in_blockers = False
    in_advisory = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            title = heading.group(1)
            in_advisory = bool(P2_P3_RE.search(title))
            in_blockers = not in_advisory and bool(P1_RE.search(title))
            continue
        bullet = BULLET_RE.match(line)
        if bullet is None:
            continue
        item = bullet.group(1).strip()
        if not item or item.lower().strip("。.!！:：") in EMPTY_FINDINGS:
            continue
        if in_blockers or (not in_advisory and INLINE_P1_RE.match(item)):
            findings.append(item)
    return findings


def summarize(text: str, limit: int = SUMMARY_LIMIT) -> str:
    return " ".join(text.split())[:limit]


def build_result(
    text: str,
    *,
    returncode: int = 0,
    duration_ms: int = 0,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> RunnerResult:
    """Merge a runner payload with markdown fallbacks into one result."""

    structured = result if isinstance(result, dict) else {}
    verdict = str(structured.get("verdict") or "").strip().upper() or extract_verdict(text)
    verdict = verdict if verdict in VERDICTS else None
    raw_findings = structured.get("findings")
    findings = (
        [str(item).strip() for item in raw_findings if item is not None and str(item).strip()]
        if isinstance(raw_findings, list)
        else extract_findings(text)
    )
    summary = str(structured.get("summary") or "").strip() or summarize(text)
    if returncode != 0 and error_code is None:
        error_code = f"agent_exit_{returncode}"
    return RunnerResult(
        status="succeeded" if returncode == 0 else "failed",
        text=text,
        returncode=returncode,
        verdict=verdict,
        summary=summary,
        findings=findings,
        duration_ms=duration_ms,
        error_code=error_code,
        retryable=retryable,
    )
