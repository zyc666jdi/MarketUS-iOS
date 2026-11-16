"""Phase 1b-i reducer tests: idempotency, secret scan and push safety."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DISPATCHER_ROOT = ROOT / "Tools/dispatcher"
if str(DISPATCHER_ROOT) not in sys.path:
    sys.path.insert(0, str(DISPATCHER_ROOT))

import reducer  # noqa: E402

BRANCH = "ai/106-reducer-core"


class JobIdTests(unittest.TestCase):
    def test_derive_job_id_is_deterministic_and_context_sensitive(self):
        first = reducer.derive_job_id("implementer", 106, "base", "head")
        self.assertEqual(first, reducer.derive_job_id("implementer", 106, "base", "head"))
        self.assertRegex(first, r"^implementer-106-[0-9a-f]{16}$")
        self.assertNotEqual(first, reducer.derive_job_id("implementer", 106, "base", "next"))
        self.assertNotEqual(first, reducer.derive_job_id("repair", 106, "base", "head"))


class ReducerGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin.git"
        self.repo = self.root / "work"
        self.jobs = self.root / "jobs"
        self._run(["git", "init", "--bare", str(self.origin)], cwd=self.root)
        self._run(["git", "init", "-b", "main", str(self.repo)], cwd=self.root)
        self._git("config", "user.name", "test")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "commit.gpgsign", "false")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "base")
        self._git("remote", "add", "origin", str(self.origin))
        self._git("push", "-u", "origin", "main")
        self._run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.origin)
        self._git("checkout", "-b", BRANCH)
        self.base_count = self._count()

    def _run(self, args, cwd=None):
        return subprocess.run(
            args, cwd=str(cwd or self.repo), capture_output=True, text=True, check=True
        )

    def _git(self, *args):
        return self._run(["git", *args])

    def _head(self):
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _count(self):
        return int(self._git("rev-list", "--count", "HEAD").stdout.strip())

    def _remote(self):
        result = self._run(
            ["git", "ls-remote", "--heads", str(self.origin), f"refs/heads/{BRANCH}"]
        )
        return result.stdout.split()[0] if result.stdout.strip() else ""

    def _reducer(self, **kwargs):
        return reducer.Reducer(jobs_root=self.jobs, scanner=lambda _path: [], **kwargs)

    def _logs(self, job_id="job-1"):
        path = self.jobs / job_id / "operations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_commit_and_push_is_idempotent_and_logged(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        result = self._reducer().commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-1", run_id="run-1"
        )
        self.assertTrue(result.ok, result.reason)
        head = self._head()
        self.assertEqual(result.commit_sha, head)
        self.assertEqual(self._remote(), head)
        self.assertEqual(self._count(), self.base_count + 1)
        message = self._git("log", "-1", "--format=%B").stdout
        self.assertIn("chore(agent): capture work for #106", message)
        self.assertIn("Refs: #106", message)
        self.assertIn("Job-Id: job-1", message)
        self.assertIn("Agent-Run: run-1", message)
        entries = self._logs()
        self.assertEqual([entry["step"] for entry in entries], ["scan", "commit", "push"])
        self.assertEqual([entry["status"] for entry in entries], ["ok", "ok", "ok"])

        again = self._reducer().commit_and_push(self.repo, BRANCH, {"number": 106}, "job-1")
        self.assertTrue(again.ok, again.reason)
        self.assertEqual(again.commit_sha, head)
        self.assertEqual(self._head(), head)
        self.assertEqual(self._count(), self.base_count + 1)
        self.assertEqual(len(self._logs()), 3)
        self.assertEqual(set(again.skipped), {"scan", "commit", "push"})

    def test_clean_unpushed_commit_is_pushed_without_new_commit(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "work")
        head = self._head()
        result = self._reducer().commit_and_push(self.repo, BRANCH, 106, "job-2")
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(self._head(), head)
        self.assertEqual(self._remote(), head)
        self.assertEqual(self._count(), self.base_count + 1)

    def test_secret_scan_blocks_commit_and_push(self):
        (self.repo / "Leak.swift").write_text('let token = "x"\n', encoding="utf-8")
        before = self._head()
        blocking = reducer.Reducer(
            jobs_root=self.jobs,
            scanner=lambda _path: ["Leak.swift: github-token"],
        )
        result = blocking.commit_and_push(self.repo, BRANCH, {"number": 106}, "job-3")
        self.assertFalse(result.ok)
        self.assertIn("密钥扫描命中", result.reason)
        self.assertIn("Leak.swift: github-token", result.reason)
        self.assertEqual(self._head(), before)
        self.assertEqual(self._count(), self.base_count)
        self.assertEqual(self._remote(), "")
        entries = self._logs("job-3")
        self.assertEqual(entries[0]["step"], "scan")
        self.assertEqual(entries[0]["status"], "failed")

    def test_failed_push_is_recoverable_without_duplicate_commit(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        state = {"failures": 1}

        def flaky(command):
            if "push" in command and state["failures"]:
                state["failures"] -= 1
                return subprocess.CompletedProcess(command, 1, "", "simulated push failure")
            return subprocess.run(command, capture_output=True, text=True, check=False)

        first = self._reducer(runner=flaky).commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-4"
        )
        self.assertFalse(first.ok)
        self.assertIn("git push 失败", first.reason)
        head = self._head()
        self.assertEqual(self._count(), self.base_count + 1)
        self.assertEqual(self._remote(), "")

        second = self._reducer(runner=flaky).commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-4"
        )
        self.assertTrue(second.ok, second.reason)
        self.assertEqual(second.commit_sha, head)
        self.assertEqual(self._head(), head)
        self.assertEqual(self._count(), self.base_count + 1)
        self.assertEqual(self._remote(), head)
        steps = [(entry["step"], entry["status"]) for entry in self._logs("job-4")]
        self.assertEqual(steps.count(("commit", "ok")), 1)
        self.assertIn(("push", "failed"), steps)
        self.assertIn(("push", "ok"), steps)

    def test_diverged_remote_push_fails_without_force(self):
        other = self.root / "other"
        self._run(["git", "clone", str(self.origin), str(other)], cwd=self.root)
        self._run(["git", "config", "user.name", "other"], cwd=other)
        self._run(["git", "config", "user.email", "other@example.com"], cwd=other)
        self._run(["git", "checkout", "-b", BRANCH], cwd=other)
        (other / "remote.txt").write_text("remote\n", encoding="utf-8")
        self._run(["git", "add", "."], cwd=other)
        self._run(["git", "commit", "-m", "remote work"], cwd=other)
        self._run(["git", "push", "origin", BRANCH], cwd=other)
        remote_before = self._remote()
        self.assertTrue(remote_before)

        (self.repo / "local.txt").write_text("local\n", encoding="utf-8")
        commands: list[list[str]] = []

        def recording(command):
            commands.append(list(command))
            return subprocess.run(command, capture_output=True, text=True, check=False)

        result = self._reducer(runner=recording).commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-5"
        )
        self.assertFalse(result.ok)
        self.assertIn("git push 失败", result.reason)
        pushes = [command for command in commands if "push" in command]
        self.assertTrue(pushes)
        for command in pushes:
            self.assertFalse(
                {"--force", "-f", "--force-with-lease"} & set(command), command
            )
        self.assertEqual(self._remote(), remote_before)
