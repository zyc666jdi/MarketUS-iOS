"""Phase 1a tests: runner registry, parser adapters, and JSON contracts."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
DISPATCHER_ROOT = ROOT / "Tools/dispatcher"
if str(DISPATCHER_ROOT) not in sys.path:
    sys.path.insert(0, str(DISPATCHER_ROOT))

import dispatcher  # noqa: E402
from runners import base, registry  # noqa: E402

INPUT_SCHEMA = DISPATCHER_ROOT / "schemas/input.schema.json"
OUTPUT_SCHEMA = DISPATCHER_ROOT / "schemas/output.schema.json"


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """Small JSON-schema subset validator used to keep schemas honest."""

    errors: list[str] = []
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if not any(checks[item](value) for item in types if item in checks):
            return [f"{path}: expected {types}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value not in enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in sorted(set(value) - set(properties)):
                errors.append(f"{path}: unexpected {key}")
        for key, subschema in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], subschema, f"{path}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], f"{path}[{index}]"))
    return errors


class ConfigLoadTests(unittest.TestCase):
    def test_checked_in_registry_declares_runners_and_roles(self):
        reg = registry.load_registry()
        self.assertEqual(reg.version, 1)
        self.assertEqual(reg.defaults["timeout_seconds"], 1800)
        self.assertIn("DEEPSEEK_API_KEY", reg.defaults["env_allowlist"])
        self.assertEqual(reg.defaults["sandbox"], "workspace-write")
        self.assertEqual(
            reg.runners["dsh"].spec.command,
            ("dsh", "--profile", "headless", "--json"),
        )
        self.assertEqual(reg.runners["dsh"].spec.output_format, "ndjson")
        self.assertEqual(reg.runners["dsh"].spec.final_event, "dsh_final")
        self.assertEqual(
            reg.runners["codex"].spec.command,
            ("codex", "exec", "--sandbox", "workspace-write", "-"),
        )
        self.assertEqual(reg.runners["codex"].spec.output_format, "text")
        self.assertEqual(reg.runners["codex"].spec.final_event, "json_result")
        self.assertEqual(
            set(reg.roles), {"implementer", "reviewer", "repair", "planner"}
        )

    def test_roles_map_prompt_and_capabilities(self):
        reg = registry.load_registry()
        self.assertEqual(reg.roles["implementer"].prompt, ".agent/prompts/implementer.md")
        self.assertEqual(reg.roles["reviewer"].prompt, ".agent/prompts/reviewer.md")
        self.assertEqual(reg.roles["reviewer"].runner_env, "REVIEWER_RUNNER")
        self.assertEqual(reg.roles["reviewer"].sandbox, "read-only")
        self.assertIn("create_pull_request", reg.roles["implementer"].capabilities)
        self.assertIn("verdict", reg.roles["reviewer"].capabilities)
        self.assertIn("commit", reg.roles["repair"].capabilities)
        self.assertIn("create_issue", reg.roles["planner"].capabilities)

    def test_stdlib_fallback_parses_same_config(self):
        text = registry.DEFAULT_CONFIG.read_text(encoding="utf-8")
        with mock.patch.object(registry, "_yaml", None):
            self.assertEqual(registry.load_config(), registry.parse_simple_yaml(text))
            fallback = registry.load_registry()
        self.assertEqual(
            fallback.runners["dsh"].spec.command,
            ("dsh", "--profile", "headless", "--json"),
        )
        self.assertEqual(
            fallback.resolve("reviewer", {"REVIEWER_RUNNER": "codex"}).runner_name,
            "codex",
        )

    def test_malformed_registry_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text("version: 1\n", encoding="utf-8")
            with self.assertRaises(registry.RegistryError):
                registry.load_registry(path)


class RoleResolutionTests(unittest.TestCase):
    def setUp(self):
        self.reg = registry.load_registry()

    def test_all_roles_resolve_to_dsh_by_default(self):
        for role in ("implementer", "reviewer", "repair", "planner"):
            with self.subTest(role=role):
                self.assertEqual(self.reg.resolve(role, {}).runner_name, "dsh")

    def test_reviewer_runner_override_selects_codex(self):
        resolved = self.reg.resolve("reviewer", {"REVIEWER_RUNNER": "codex"})
        self.assertEqual(resolved.runner_name, "codex")
        self.assertEqual(
            resolved.runner.command(),
            ["codex", "exec", "--sandbox", "workspace-write", "-"],
        )

    def test_reviewer_runner_override_back_to_dsh(self):
        self.assertEqual(
            self.reg.resolve("reviewer", {"REVIEWER_RUNNER": "dsh"}).runner_name, "dsh"
        )

    def test_unknown_override_is_a_clear_error(self):
        with self.assertRaisesRegex(registry.RegistryError, "allowed"):
            self.reg.resolve("reviewer", {"REVIEWER_RUNNER": "claude"})

    def test_dispatcher_resolves_every_role_and_honours_override(self):
        for role in ("implementer", "reviewer", "repair", "planner"):
            with self.subTest(role=role):
                self.assertEqual(dispatcher.resolve_agent_role(role).runner_name, "dsh")
        with mock.patch.dict(os.environ, {"REVIEWER_RUNNER": "codex"}):
            self.assertEqual(
                dispatcher.role_command("reviewer"),
                ["codex", "exec", "--sandbox", "workspace-write", "-"],
            )
        with mock.patch.dict(os.environ, {"REVIEWER_RUNNER": ""}):
            with mock.patch.object(dispatcher, "agent_command", return_value=["dsh-x"]):
                self.assertEqual(dispatcher.role_command("implementer"), ["dsh-x"])


class DshParserTests(unittest.TestCase):
    def setUp(self):
        self.runner = registry.load_registry().runners["dsh"]

    def test_final_event_is_converted_to_unified_result(self):
        text = "## Verdict: APPROVE\n\n## Blockers (P1)\n\n- 无\n"
        stdout = '{"type":"status"}\n' + json.dumps({"type": "final", "text": text})
        result = self.runner.parse(stdout, duration_ms=12)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.text, text)
        self.assertEqual(result.verdict, "APPROVE")
        self.assertEqual(result.findings, [])
        self.assertEqual(result.duration_ms, 12)

    def test_dsh_final_event_and_p1_findings(self):
        text = (
            "## Verdict: REQUEST_CHANGES\n\n"
            "## Blockers（P1）\n\n"
            "- [ ] `A.swift:10` — real blocker\n"
            "- 无\n"
        )
        stdout = json.dumps({"type": "dsh_final", "text": text})
        result = self.runner.parse(stdout)
        self.assertEqual(result.verdict, "REQUEST_CHANGES")
        self.assertEqual(result.findings, ["`A.swift:10` — real blocker"])

    def test_raw_output_fallback_and_nonzero_exit(self):
        result = self.runner.parse("agent log without events", returncode=2)
        self.assertEqual(result.text, "agent log without events")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "agent_exit_2")
        self.assertFalse(result.retryable)

    def test_structured_final_payload(self):
        stdout = json.dumps(
            {
                "type": "dsh_final",
                "result": {"verdict": "APPROVE", "summary": "LGTM", "findings": []},
            }
        )
        result = self.runner.parse(stdout)
        self.assertEqual(result.verdict, "APPROVE")
        self.assertEqual(result.summary, "LGTM")


class CodexParserTests(unittest.TestCase):
    def setUp(self):
        self.runner = registry.load_registry().runners["codex"]

    def test_plain_text_review(self):
        text = "## Verdict: REQUEST_CHANGES\n\n## Blockers (P1)\n\n- `B.swift:2` bug\n"
        result = self.runner.parse(text)
        self.assertEqual(result.text, text)
        self.assertEqual(result.verdict, "REQUEST_CHANGES")
        self.assertEqual(result.findings, ["`B.swift:2` bug"])

    def test_fenced_json_result(self):
        stdout = (
            "Review complete.\n```json\n"
            '{"verdict": "APPROVE", "summary": "ok", "findings": []}\n```\n'
        )
        result = self.runner.parse(stdout)
        self.assertEqual(result.verdict, "APPROVE")
        self.assertEqual(result.summary, "ok")
        self.assertEqual(result.findings, [])

    def test_json_envelope_with_result_object(self):
        stdout = json.dumps(
            {
                "type": "json_result",
                "result": {
                    "verdict": "REQUEST_CHANGES",
                    "summary": "issue",
                    "findings": ["P1: race"],
                },
            }
        )
        result = self.runner.parse(stdout)
        self.assertEqual(result.verdict, "REQUEST_CHANGES")
        self.assertEqual(result.summary, "issue")
        self.assertEqual(result.findings, ["P1: race"])

    def test_nonzero_exit_marks_failure(self):
        result = self.runner.parse("boom", returncode=1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "agent_exit_1")


class SchemaContractTests(unittest.TestCase):
    def setUp(self):
        self.input_schema = load_schema(INPUT_SCHEMA)
        self.output_schema = load_schema(OUTPUT_SCHEMA)

    def test_expected_top_level_keys(self):
        self.assertEqual(
            set(self.input_schema["required"]),
            {
                "schema_version",
                "job_id",
                "role",
                "prompt",
                "ticket",
                "pr",
                "worktree",
                "limits",
                "capabilities",
            },
        )
        self.assertEqual(
            set(self.output_schema["required"]),
            {"schema_version", "status", "result", "usage", "error"},
        )
        self.assertEqual(
            set(self.output_schema["properties"]["result"]["required"]),
            {"verdict", "summary", "findings"},
        )
        self.assertEqual(
            set(self.output_schema["properties"]["error"]["required"]),
            {"code", "retryable"},
        )

    def test_built_input_matches_schema(self):
        payload = base.build_job_input(
            job_id="review-104",
            role="reviewer",
            prompt="review this",
            ticket=104,
            pr=105,
            worktree="/tmp/wt",
            timeout_seconds=60,
            capabilities=("read", "comment"),
        )
        self.assertEqual(schema_errors(payload, self.input_schema), [])
        self.assertEqual(payload["limits"], {"timeout_seconds": 60})

    def test_runner_output_matches_schema(self):
        runner = registry.load_registry().runners["codex"]
        result = runner.parse("## Verdict: APPROVE\n")
        self.assertEqual(schema_errors(result.to_payload(), self.output_schema), [])
        self.assertIsNone(result.to_payload()["error"]["code"])

    def test_validator_rejects_missing_keys(self):
        self.assertTrue(schema_errors({"schema_version": 1}, self.input_schema))
        self.assertTrue(schema_errors({"schema_version": 1}, self.output_schema))


class DispatcherIntegrationTests(unittest.TestCase):
    def test_codex_reviewer_uses_registry_and_writes_unified_result(self):
        stdout = (
            "## Verdict: REQUEST_CHANGES\n\n"
            "## Blockers (P1)\n\n- `C.swift:3` bug\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "review-104"
            with (
                mock.patch.dict(os.environ, {"REVIEWER_RUNNER": "codex"}),
                mock.patch.object(dispatcher, "load_agent_secrets"),
                mock.patch.object(
                    dispatcher, "run_agent_streaming", return_value=(0, stdout)
                ) as stream,
            ):
                result = dispatcher.run_review_agent(
                    "prompt", run_dir, 30, ticket=104, pr=105, worktree=tmp
                )
                text, code = result
            self.assertEqual((text, code), (stdout, 0))
            self.assertEqual(result.verdict, "REQUEST_CHANGES")
            self.assertEqual(stream.call_args.args[0], [
                "codex", "exec", "--sandbox", "workspace-write", "-",
            ])
            self.assertNotIn("DSH_PERMISSION_MODE", stream.call_args.args[3])
            input_payload = json.loads((run_dir / "input.json").read_text())
            output_payload = json.loads((run_dir / "result.json").read_text())
            self.assertEqual(input_payload["ticket"], 104)
            self.assertEqual(input_payload["pr"], 105)
            self.assertEqual(
                schema_errors(input_payload, load_schema(INPUT_SCHEMA)), []
            )
            self.assertEqual(
                schema_errors(output_payload, load_schema(OUTPUT_SCHEMA)), []
            )

    def test_dsh_reviewer_preserves_agent_cmd(self):
        stdout = json.dumps({"type": "final", "text": "## Verdict: APPROVE\n"})
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "review-104"
            with (
                mock.patch.dict(
                    os.environ,
                    {"REVIEWER_RUNNER": "", "AGENT_CMD": "custom-agent --flag"},
                ),
                mock.patch.object(dispatcher, "load_agent_secrets"),
                mock.patch.object(
                    dispatcher, "run_agent_streaming", return_value=(0, stdout)
                ) as stream,
            ):
                result = dispatcher.run_review_agent("prompt", run_dir, 30)
            self.assertEqual(stream.call_args.args[0], ["custom-agent", "--flag"])
            self.assertNotIn("DSH_PERMISSION_MODE", stream.call_args.args[3])
            self.assertEqual(result.text, "## Verdict: APPROVE\n")
            self.assertEqual(result.verdict, "APPROVE")
            self.assertEqual(
                schema_errors(
                    json.loads((run_dir / "result.json").read_text()),
                    load_schema(OUTPUT_SCHEMA),
                ),
                [],
            )

    def test_default_dsh_reviewer_keeps_read_only_permission_mode(self):
        stdout = json.dumps({"type": "final", "text": "## Verdict: APPROVE\n"})
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(dispatcher, "load_agent_secrets"),
                mock.patch.object(
                    dispatcher, "run_agent_streaming", return_value=(0, stdout)
                ) as stream,
            ):
                dispatcher.run_review_agent("prompt", Path(tmp) / "run", 30)
            self.assertEqual(
                stream.call_args.args[0], ["dsh", "--profile", "headless", "--json"]
            )
            self.assertEqual(stream.call_args.args[3]["DSH_PERMISSION_MODE"], "read-only")

    def test_configured_runner_error_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"REVIEWER_RUNNER": "claude"}):
                result = dispatcher.run_review_agent("prompt", Path(tmp), 1)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "runner_config")
            self.assertIn("allowed", result.text)


if __name__ == "__main__":
    unittest.main()
