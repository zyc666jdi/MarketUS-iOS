"""Runner adapters for the dispatcher."""

from .base import (
    ResolvedRole,
    RoleSpec,
    Runner,
    RunnerResult,
    RunnerSpec,
    build_job_input,
    build_result,
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
    "build_job_input",
    "build_result",
]
