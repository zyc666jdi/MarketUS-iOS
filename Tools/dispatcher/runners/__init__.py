"""Runner adapters for the dispatcher."""

from .base import (
    SANDBOXES,
    ResolvedRole,
    RoleSpec,
    Runner,
    RunnerResult,
    RunnerSpec,
    build_job_input,
    build_result,
    build_runner_env,
    filter_blocking_findings,
    render_job_prompt,
)
from .codex import CodexRunner
from .dsh import DshRunner

__all__ = [
    "CodexRunner",
    "DshRunner",
    "ResolvedRole",
    "RoleSpec",
    "Runner",
    "RunnerResult",
    "RunnerSpec",
    "SANDBOXES",
    "build_job_input",
    "build_result",
    "build_runner_env",
    "filter_blocking_findings",
    "render_job_prompt",
]
