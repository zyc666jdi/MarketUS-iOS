"""``dsh --profile headless --json`` adapter (NDJSON with a final event)."""

from __future__ import annotations

import json

from .base import RunnerResult, RunnerSpec, build_result


class DshRunner:
    """Parse dsh NDJSON, falling back to raw stdout when no final event exists."""

    def __init__(self, spec: RunnerSpec) -> None:
        self.spec = spec

    def command(
        self, sandbox: str | None = None, output_path: str | None = None
    ) -> list[str]:
        """dsh enforces the sandbox through ``DSH_PERMISSION_MODE``, not argv."""

        return list(self.spec.command)

    def parse(
        self,
        stdout: str,
        *,
        returncode: int = 0,
        duration_ms: int = 0,
        final_message: str | None = None,
    ) -> RunnerResult:
        final_events = {self.spec.final_event, "final", "dsh_final"}
        texts: list[str] = []
        structured: dict | None = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") not in final_events:
                continue
            payload = event.get("result")
            if isinstance(payload, dict) and structured is None:
                structured = payload
            text = event.get("text") or event.get("message")
            if not text and isinstance(payload, str):
                text = payload
            if text:
                texts.append(str(text))
        text = "\n".join(texts) if texts else stdout
        return build_result(
            text,
            returncode=returncode,
            duration_ms=duration_ms,
            result=structured,
        )
