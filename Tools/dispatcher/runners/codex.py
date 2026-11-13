"""``codex exec`` adapter (final-message review sentinel only).

Codex writes tool output and diffs to stderr, and only the final assistant
message to stdout.  The dispatcher runs it with ``-o <run_dir>/last-message.txt``
and this adapter parses only that final message.

A verdict is accepted solely from a JSON object carrying
``"marketus_review": true``.  Fenced JSON quoted from a diff or from tool
output is ignored, even when it contains a ``verdict`` key.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import SANDBOXES, VERDICTS, RunnerResult, RunnerSpec, build_result

SENTINEL_KEY = "marketus_review"
FINAL_MESSAGE_FILE = "last-message.txt"
FENCE_RE = re.compile(r"```(?:json)?[ \t]*(?:\r?\n)?(.*?)```", re.DOTALL | re.IGNORECASE)
VERDICT_MARKER_RE = re.compile(
    r"##\s*Verdict\s*:\s*(?:APPROVE|REQUEST_CHANGES)", re.IGNORECASE
)


def _json_object(candidate: str) -> dict[str, Any] | None:
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _review_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a validated sentinel payload, or ``None`` for any other JSON."""

    if payload is None or payload.get(SENTINEL_KEY) is not True:
        return None
    verdict = str(payload.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        return None
    clean: dict[str, Any] = {"verdict": verdict, "findings": []}
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        clean["summary"] = summary.strip()
    raw_findings = payload.get("findings")
    if isinstance(raw_findings, list):
        clean["findings"] = [
            str(item).strip()
            for item in raw_findings
            if item is not None and str(item).strip()
        ]
    return clean


def _raw_sentinel_suffix(text: str) -> tuple[dict[str, Any] | None, str]:
    """Parse a raw sentinel object that is the final text content."""

    starts = [0] if text.startswith("{") else []
    index = text.rfind("\n{")
    if index != -1:
        starts.append(index + 1)
    for start in starts:
        payload = _review_payload(_json_object(text[start:].strip()))
        if payload is not None:
            return payload, text[:start].rstrip()
    return None, text


def extract_sentinel(final_message: str) -> tuple[dict[str, Any] | None, str]:
    """Return ``(sentinel, body)`` parsed strictly from the final message.

    The sentinel must be the final content: either the whole message is the
    sentinel object, or the last fenced JSON block is at the end of the message.
    Any other JSON is ignored.
    """

    text = final_message.strip()
    if not text:
        return None, ""

    whole = _review_payload(_json_object(text))
    if whole is not None:
        return whole, ""

    blocks = list(FENCE_RE.finditer(text))
    if blocks:
        last = blocks[-1]
        trailing = text[last.end() :]
        if not trailing.strip():
            payload = _review_payload(_json_object(last.group(1).strip()))
            if payload is not None:
                return payload, text[: last.start()].rstrip()
            return None, text
        # A fenced example may precede a raw sentinel object at the very end.
        payload, raw_body = _raw_sentinel_suffix(trailing)
        if payload is not None:
            return payload, (text[: last.end()] + raw_body).rstrip()
        return None, text

    return _raw_sentinel_suffix(text)


def _strip_verdict_headings(text: str) -> str:
    """Remove markdown verdict markers so the sentinel is authoritative.

    The pattern intentionally matches markers quoted from a diff too (inline or
    with leading whitespace), otherwise ``dispatcher.parse_verdict`` could pick
    up the quoted text after the sentinel was accepted.
    """

    return VERDICT_MARKER_RE.sub("", text).strip()


def _set_option(
    args: list[str], flag: str, value: str, aliases: tuple[str, ...] = ()
) -> list[str]:
    names = (flag, *aliases)
    for index, arg in enumerate(args):
        for name in names:
            if arg == name:
                if index + 1 < len(args):
                    args[index + 1] = value
                else:
                    args.append(value)
                return args
            if arg.startswith(f"{name}="):
                args[index] = f"{name}={value}"
                return args
    if "exec" in args:
        position = args.index("exec") + 1
    else:
        position = len(args) - 1 if args and args[-1] == "-" else len(args)
    args[position:position] = [flag, value]
    return args


class CodexRunner:
    """Parse codex's final assistant message and require a review sentinel."""

    def __init__(self, spec: RunnerSpec) -> None:
        self.spec = spec

    def command(
        self, sandbox: str | None = None, output_path: str | None = None
    ) -> list[str]:
        """Render argv for the resolved role sandbox and final-message file.

        The checked-in command omits ``--sandbox`` and ``-o`` so role identity
        and ``run_dir`` are the single source of truth: reviewer ->
        ``read-only``, implementer / repair -> ``workspace-write``.
        """

        if sandbox is not None and sandbox not in SANDBOXES:
            raise ValueError(f"unknown codex sandbox: {sandbox!r}")
        args = list(self.spec.command)
        if sandbox is not None:
            args = _set_option(args, "--sandbox", sandbox, ("-s",))
        if output_path is not None:
            args = _set_option(
                args, "-o", str(output_path), ("--output-last-message",)
            )
        return args

    def parse(
        self,
        stdout: str,
        *,
        returncode: int = 0,
        duration_ms: int = 0,
        final_message: str | None = None,
    ) -> RunnerResult:
        """Parse only the final assistant message; never stdout/tool output.

        ``stdout`` is intentionally unused: in the dispatcher it is the merged
        agent log and can contain tool output, diffs, and quoted JSON.
        """

        if final_message is None:
            return build_result(
                "",
                returncode=returncode,
                duration_ms=duration_ms,
                error_code="missing_final_message" if returncode == 0 else None,
                markdown_fallback=False,
            )

        payload, body = extract_sentinel(final_message)
        text = _strip_verdict_headings(
            body if payload is not None else final_message
        )
        return build_result(
            text,
            returncode=returncode,
            duration_ms=duration_ms,
            result=payload,
            error_code=(
                "missing_review_sentinel"
                if payload is None and returncode == 0
                else None
            ),
            markdown_fallback=False,
        )
