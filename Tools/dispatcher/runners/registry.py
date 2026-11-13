"""Load ``config/agents.yaml`` and resolve role -> runner.

PyYAML is optional: the checked-in config uses a small YAML subset that
:func:`parse_simple_yaml` understands, so the dispatcher keeps working on a
bare Python install.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

try:  # pragma: no cover - depends on the host environment
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None

from .base import ResolvedRole, RoleSpec, Runner, RunnerSpec
from .codex import CodexRunner
from .dsh import DshRunner

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "agents.yaml"
REQUIRED_ROLES = ("implementer", "reviewer", "repair", "planner")
OUTPUT_FORMATS = {"ndjson", "text"}
RUNNER_CLASSES = {"dsh": DshRunner, "codex": CodexRunner}


class RegistryError(RuntimeError):
    """Raised when the runner registry is missing, malformed, or ambiguous."""


def _strip_comment(line: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _split_flow(value: str) -> list[str]:
    items: list[str] = []
    quote = ""
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(value[start:index])
            start = index + 1
    items.append(value[start:])
    return items


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value[0] == "[" and value[-1] == "]":
        return [_scalar(item) for item in _split_flow(value[1:-1]) if item.strip()]
    if value[0] == "{" and value[-1] == "}":
        result: dict[str, Any] = {}
        for item in _split_flow(value[1:-1]):
            if ":" not in item:
                raise ValueError(f"bad inline mapping item: {item!r}")
            key, rest = item.split(":", 1)
            result[_scalar(key)] = _scalar(rest)
        return result
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return json.loads(value)
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _parse_block(lines: list[tuple[int, int, str]], index: int, indent: int) -> tuple[Any, int]:
    if lines[index][2].startswith("- "):
        items: list[Any] = []
        while index < len(lines):
            line_no, line_indent, content = lines[index]
            if line_indent < indent:
                break
            if line_indent > indent or not content.startswith("- "):
                raise ValueError(f"line {line_no}: bad list indentation")
            rest = content[2:].strip()
            index += 1
            if rest:
                items.append(_scalar(rest))
            elif index < len(lines) and lines[index][1] > indent:
                value, index = _parse_block(lines, index, lines[index][1])
                items.append(value)
            else:
                items.append(None)
        return items, index

    mapping: dict[str, Any] = {}
    while index < len(lines):
        line_no, line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"line {line_no}: unexpected indentation")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError(f"line {line_no}: expected 'key: value'")
        key, rest = content.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"line {line_no}: empty key")
        index += 1
        if rest.strip():
            mapping[key] = _scalar(rest)
        elif index < len(lines) and lines[index][1] > indent:
            mapping[key], index = _parse_block(lines, index, lines[index][1])
        else:
            mapping[key] = None
    return mapping, index


def parse_simple_yaml(text: str) -> Any:
    """Parse the mapping/list/scalar subset used by ``agents.yaml``."""

    lines: list[tuple[int, int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        content = stripped.lstrip(" ")
        lines.append((number, len(stripped) - len(content), content))
    if not lines:
        return None
    value, index = _parse_block(lines, 0, lines[0][1])
    if index != len(lines):
        raise ValueError(f"line {lines[index][0]}: could not parse")
    return value


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RegistryError(f"cannot read runner config {config_path}: {error}") from error
    if _yaml is not None:
        try:
            data = _yaml.safe_load(text)
        except Exception as error:  # PyYAML raises several parser subclasses
            raise RegistryError(f"invalid YAML in {config_path}: {error}") from error
    else:
        try:
            data = parse_simple_yaml(text)
        except (ValueError, json.JSONDecodeError) as error:
            raise RegistryError(f"invalid YAML subset in {config_path}: {error}") from error
    if not isinstance(data, dict):
        raise RegistryError(f"{config_path} must contain a mapping")
    return data


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RegistryError(f"{where} must be a non-empty string list")
    return tuple(value)


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{where} must be a mapping")
    return value


class Registry:
    """Validated runners, roles, and defaults from ``agents.yaml``."""

    def __init__(
        self,
        *,
        version: int,
        defaults: dict[str, Any],
        runners: dict[str, Runner],
        roles: dict[str, RoleSpec],
        path: Path,
    ) -> None:
        self.version = version
        self.defaults = defaults
        self.runners = runners
        self.roles = roles
        self.path = path

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path) -> "Registry":
        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise RegistryError(f"{path}: version must be an integer")
        defaults = _mapping(data.get("defaults"), f"{path}: defaults")
        timeout = defaults.get("timeout_seconds")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise RegistryError(f"{path}: defaults.timeout_seconds must be a positive integer")
        _string_list(defaults.get("env_allowlist"), f"{path}: defaults.env_allowlist")
        if not isinstance(defaults.get("sandbox"), str) or not defaults["sandbox"]:
            raise RegistryError(f"{path}: defaults.sandbox must be a string")

        runner_map = _mapping(data.get("runners"), f"{path}: runners")
        runners: dict[str, Runner] = {}
        for name, raw in runner_map.items():
            where = f"{path}: runners.{name}"
            entry = _mapping(raw, where)
            command = _string_list(entry.get("command"), f"{where}.command")
            output_format = entry.get("output_format")
            if output_format not in OUTPUT_FORMATS:
                raise RegistryError(f"{where}.output_format must be one of {sorted(OUTPUT_FORMATS)}")
            final_event = entry.get("final_event")
            if not isinstance(final_event, str) or not final_event:
                raise RegistryError(f"{where}.final_event must be a string")
            runner_class = RUNNER_CLASSES.get(name)
            if runner_class is None:
                raise RegistryError(f"{where}: unsupported runner {name!r}")
            spec = RunnerSpec(
                name=name,
                command=command,
                output_format=output_format,
                final_event=final_event,
            )
            runners[name] = runner_class(spec)
        if not runners:
            raise RegistryError(f"{path}: runners must not be empty")

        role_map = _mapping(data.get("roles"), f"{path}: roles")
        roles: dict[str, RoleSpec] = {}
        for name in REQUIRED_ROLES:
            where = f"{path}: roles.{name}"
            entry = _mapping(role_map.get(name), where)
            runner_name = entry.get("runner")
            if runner_name not in runners:
                raise RegistryError(f"{where}.runner must be one of {sorted(runners)}")
            prompt = entry.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                raise RegistryError(f"{where}.prompt must be a string")
            capabilities = _string_list(entry.get("capabilities"), f"{where}.capabilities")
            runner_env = entry.get("runner_env")
            if runner_env is not None and (not isinstance(runner_env, str) or not runner_env):
                raise RegistryError(f"{where}.runner_env must be a non-empty string")
            sandbox = entry.get("sandbox")
            if sandbox is not None and (not isinstance(sandbox, str) or not sandbox):
                raise RegistryError(f"{where}.sandbox must be a non-empty string")
            roles[name] = RoleSpec(
                name=name,
                runner=runner_name,
                prompt=prompt,
                capabilities=capabilities,
                runner_env=runner_env,
                sandbox=sandbox,
            )
        unknown = sorted(set(role_map) - set(REQUIRED_ROLES))
        if unknown:
            raise RegistryError(f"{path}: unknown roles {unknown}")
        return cls(version=version, defaults=defaults, runners=runners, roles=roles, path=path)

    def resolve(self, role: str, env: Mapping[str, str] | None = None) -> ResolvedRole:
        if role not in self.roles:
            raise RegistryError(f"unknown role {role!r}; expected one of {sorted(self.roles)}")
        spec = self.roles[role]
        runner_name = spec.runner
        if spec.runner_env:
            override = (env or {}).get(spec.runner_env, "").strip()
            if override:
                if override not in self.runners:
                    raise RegistryError(
                        f"{spec.runner_env}={override!r} is not a registered runner; "
                        f"allowed: {sorted(self.runners)}"
                    )
                runner_name = override
        return ResolvedRole(role=spec, runner=self.runners[runner_name], runner_name=runner_name)


def load_registry(path: Path | str | None = None) -> Registry:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    return Registry.from_dict(load_config(config_path), config_path)
