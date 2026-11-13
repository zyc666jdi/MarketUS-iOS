"""``codex exec`` adapter (text, optional JSON result payload)."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import SANDBOXES, RunnerResult, RunnerSpec, build_result

FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
TAG_RE = re.compile(r"<json_result>\s*(\{.*?\})\s*</json_result>", re.DOTALL)
TEXT_KEYS = ("text", "message", "output")


def _find_payload(stdout: str) -> dict[str, Any] | None:
    """Return the last JSON object in stdout, if codex emitted one."""

    stripped = stdout.strip()
    candidates = [stripped]
    candidates.extend(reversed(FENCE_RE.findall(stdout)))
    candidates.extend(reversed(TAG_RE.findall(stdout)))
    candidates.extend(
        line.strip()
        for line in reversed(stdout.splitlines())
        if line.strip().startswith("{")
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _payload_text(payload: dict[str, Any], stdout: str) -> str:
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result
    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    structured = result if isinstance(result, dict) else payload
    for key in ("summary", "text"):
        value = structured.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return stdout


class CodexRunner:
    """Parse free-form codex stdout, optionally embedding a JSON result."""

    def __init__(self, spec: RunnerSpec) -> None:
        self.spec = spec

    def command(self, sandbox: str | None = None) -> list[str]:
        """Render argv for the resolved role sandbox.

        The checked-in command omits ``--sandbox`` so the role declaration is
        the only source of truth: reviewer -> ``read-only``, implementer /
        repair -> ``workspace-write`` (the defaults).  An explicit flag in a
        custom command is replaced rather than duplicated.
        """

        args = list(self.spec.command)
        if sandbox is None:
            return args
        if sandbox not in SANDBOXES:
            raise ValueError(f"unknown codex sandbox: {sandbox!r}")
        for index, arg in enumerate(args):
            if arg == "--sandbox":
                if index + 1 < len(args):
                    args[index + 1] = sandbox
                else:
                    args.append(sandbox)
                return args
            if arg.startswith("--sandbox="):
                args[index] = f"--sandbox={sandbox}"
                return args
        if "exec" in args:
            position = args.index("exec") + 1
        else:
            position = len(args) - 1 if args and args[-1] == "-" else len(args)
        args[position:position] = ["--sandbox", sandbox]
        return args

    def parse(self, stdout: str, *, returncode: int = 0, duration_ms: int = 0) -> RunnerResult:
        payload = _find_payload(stdout)
        if payload is None:
            return build_result(stdout, returncode=returncode, duration_ms=duration_ms)
        result = payload.get("result")
        structured = result if isinstance(result, dict) else payload
        return build_result(
            _payload_text(payload, stdout),
            returncode=returncode,
            duration_ms=duration_ms,
            result=structured,
        )
