"""Phase 1b-i reducer tests: idempotency, secret scan and push safety."""

import json
import subprocess
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
import reducer  # noqa: E402

BRANCH = "ai/106-reducer-core"


class JobIdTests(unittest.TestCase):
    def test_derive_job_id_is_deterministic_and_context_sensitive(self):
        first = reducer.derive_job_id("implementer", 106, "base", BRANCH)
        self.assertEqual(first, reducer.derive_job_id("implementer", 106, "base", BRANCH))
        self.assertRegex(first, r"^implementer-106-[0-9a-f]{16}$")
        self.assertNotEqual(first, reducer.derive_job_id("implementer", 106, "base", "other"))
        self.assertNotEqual(first, reducer.derive_job_id("implementer", 106, "other", BRANCH))
        self.assertNotEqual(first, reducer.derive_job_id("repair", 106, "base", BRANCH))


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
        options = {
            "jobs_root": self.jobs,
            "scanner": lambda _path: [],
            "range_scanner": lambda _path, _range: [],
        }
        options.update(kwargs)
        return reducer.Reducer(**options)

    def _logs(self, job_id="job-1"):
        path = self.jobs / job_id / "operations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_commit_and_push_is_idempotent_and_logged(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        range_scans: list[str] = []

        def range_scan(_path, revision_range):
            range_scans.append(revision_range)
            return []

        result = self._reducer(range_scanner=range_scan).commit_and_push(
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
        staged = [e for e in entries if e["step"] == "scan" and e.get("scope") == "staged"]
        ranged = [e for e in entries if e["step"] == "scan" and e.get("scope") == "range"]
        commits = [e for e in entries if e["step"] == "commit"]
        pushes = [e for e in entries if e["step"] == "push"]
        self.assertEqual([e["status"] for e in staged], ["ok"])
        self.assertEqual([e["status"] for e in ranged], ["ok"])
        self.assertEqual([e["status"] for e in commits], ["ok"])
        self.assertEqual([e["status"] for e in pushes], ["ok"])
        self.assertEqual({e["branch"] for e in pushes}, {BRANCH})
        self.assertEqual(len(range_scans), 1)

        again = self._reducer(range_scanner=range_scan).commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-1"
        )
        self.assertTrue(again.ok, again.reason)
        self.assertEqual(again.commit_sha, head)
        self.assertEqual(self._head(), head)
        self.assertEqual(self._count(), self.base_count + 1)
        entries = self._logs()
        self.assertEqual(len([e for e in entries if e["step"] == "commit"]), 1)
        self.assertEqual(len([e for e in entries if e["step"] == "push"]), 1)
        self.assertEqual(len(range_scans), 2)
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

    def test_commit_disables_signing_and_hooks(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self._git("config", "commit.gpgsign", "true")
        self._git("config", "gpg.program", "false")
        result = self._reducer().commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-8"
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(self._count(), self.base_count + 1)

    def test_deleted_secret_in_history_is_blocked_before_push(self):
        token = "ghp_" + "d" * 36
        (self.repo / "Leak.swift").write_text(
            f'let token = "{token}"\n', encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-m", "add leak")
        (self.repo / "Leak.swift").unlink()
        self._git("add", "-A")
        self._git("commit", "-m", "remove leak")
        head = self._head()
        commands: list[list[str]] = []

        def recording(command):
            commands.append(list(command))
            return subprocess.run(command, capture_output=True, text=True, check=False)

        blocking = reducer.Reducer(
            jobs_root=self.jobs,
            scanner=lambda _path: [],
            range_scanner=dispatcher.scan_range_secrets,
            runner=recording,
        )
        with mock.patch.object(dispatcher, "gitleaks_range_hits", return_value=[]):
            result = blocking.commit_and_push(self.repo, BRANCH, {"number": 106}, "job-6")
        self.assertFalse(result.ok)
        self.assertIn("密钥扫描命中", result.reason)
        self.assertEqual(self._head(), head)
        self.assertEqual(self._count(), self.base_count + 2)
        self.assertEqual(self._remote(), "")
        self.assertFalse(any("push" in command for command in commands))
        entries = self._logs("job-6")
        failed_ranges = [
            entry
            for entry in entries
            if entry["step"] == "scan"
            and entry.get("scope") == "range"
            and entry["status"] == "failed"
        ]
        self.assertEqual(len(failed_ranges), 1)
        self.assertIn("Leak.swift: github-token", failed_ranges[0]["reason"])

    def test_secret_in_commit_message_is_blocked_before_push(self):
        token = "ghp_" + "e" * 36
        (self.repo / "note.txt").write_text("clean\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", f"add clean file {token}")
        head = self._head()
        commands: list[list[str]] = []

        def recording(command):
            commands.append(list(command))
            return subprocess.run(command, capture_output=True, text=True, check=False)

        blocking = reducer.Reducer(
            jobs_root=self.jobs,
            scanner=lambda _path: [],
            range_scanner=dispatcher.scan_range_secrets,
            runner=recording,
        )
        with mock.patch.object(dispatcher, "gitleaks_range_hits", return_value=[]):
            result = blocking.commit_and_push(self.repo, BRANCH, {"number": 106}, "job-9")
        self.assertFalse(result.ok)
        self.assertIn("密钥扫描命中", result.reason)
        self.assertIn("message: github-token", result.reason)
        self.assertEqual(self._head(), head)
        self.assertEqual(self._remote(), "")
        self.assertFalse(any("push" in command for command in commands))

    def test_logged_push_requires_matching_remote(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        first = self._reducer().commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-7"
        )
        self.assertTrue(first.ok, first.reason)
        head = self._head()
        self.assertEqual(self._remote(), head)
        self._run(["git", "update-ref", "-d", f"refs/heads/{BRANCH}"], cwd=self.origin)
        self.assertEqual(self._remote(), "")

        commands: list[list[str]] = []

        def recording(command):
            commands.append(list(command))
            return subprocess.run(command, capture_output=True, text=True, check=False)

        second = self._reducer(runner=recording).commit_and_push(
            self.repo, BRANCH, {"number": 106}, "job-7"
        )
        self.assertTrue(second.ok, second.reason)
        self.assertEqual(self._remote(), head)
        self.assertEqual(len([command for command in commands if "push" in command]), 1)

    def test_ensure_agent_pr_reuses_job_dir_after_push_failure(self):
        (self.repo / "change.txt").write_text("one\n", encoding="utf-8")
        pushes = {"failures": 1, "count": 0}
        commands: list[list[str]] = []

        def scripted(command):
            commands.append(list(command))
            if command[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(command, 0, "[]", "")
            if command[:3] == ["gh", "pr", "create"]:
                return subprocess.CompletedProcess(
                    command, 0, "https://example/pull/77\n", ""
                )
            if command[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(command, 0, '{"comments": []}', "")
            if command[:2] == ["gh", "pr"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if "push" in command:
                pushes["count"] += 1
                if pushes["failures"]:
                    pushes["failures"] -= 1
                    return subprocess.CompletedProcess(
                        command, 1, "", "simulated push failure"
                    )
            return subprocess.run(command, capture_output=True, text=True, check=False)

        issue = {"number": 106, "title": "test"}
        with (
            mock.patch.object(dispatcher, "run", side_effect=scripted),
            mock.patch.object(dispatcher, "gitleaks_hits", return_value=[]),
            mock.patch.object(dispatcher, "gitleaks_range_hits", return_value=[]),
            mock.patch.object(dispatcher, "JOB_ROOT", self.jobs),
        ):
            first, first_reason = dispatcher.ensure_agent_pr(
                self.repo, BRANCH, issue, run_id="run-1"
            )
            second, second_reason = dispatcher.ensure_agent_pr(
                self.repo, BRANCH, issue, run_id="run-1"
            )
        self.assertIsNone(first)
        self.assertIn("git push 失败", first_reason)
        self.assertEqual(second_reason, "")
        self.assertEqual(second["number"], 77)
        self.assertEqual(self._count(), self.base_count + 1)
        self.assertEqual(self._remote(), self._head())
        job_dirs = [path for path in self.jobs.iterdir() if path.is_dir()]
        self.assertEqual(len(job_dirs), 1)
        self.assertTrue(job_dirs[0].name.startswith("implementer-106-"))
        entries = [
            json.loads(line)
            for line in (job_dirs[0] / "operations.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        commits = [e for e in entries if e["step"] == "commit" and e["status"] == "ok"]
        self.assertEqual(len(commits), 1)
        self.assertEqual(len([e for e in entries if e["step"] == "push" and e["status"] == "ok"]), 1)
        self.assertEqual(len([e for e in entries if e["step"] == "push" and e["status"] == "failed"]), 1)
        self.assertEqual(pushes["count"], 2)
        creates = [command for command in commands if command[:3] == ["gh", "pr", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn(
            "<!-- job:implementer-106-",
            creates[0][creates[0].index("--body") + 1],
        )
        comments = [command for command in commands if command[:3] == ["gh", "pr", "comment"]]
        self.assertEqual(len(comments), 1)
        self.assertIn("<!-- job:implementer-106-", comments[0][-1])

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


class ReducerGitHubTests(unittest.TestCase):
    """Idempotent PR, comment and label writes against a fake gh."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.calls = []
        self.prs = []
        self.comments = {}
        self.fail_once = {}

    def _runner(self, command):
        self.calls.append(list(command))
        key = " ".join(command[:3])
        if self.fail_once.get(key, 0):
            self.fail_once[key] -= 1
            return subprocess.CompletedProcess(command, 1, "", f"simulated {key} failure")
        if command[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.prs), "")
        if command[:3] == ["gh", "pr", "create"]:
            number = 77 + len(self.prs)
            url = f"https://example/pull/{number}"
            self.prs.append({"number": number, "url": url})
            return subprocess.CompletedProcess(command, 0, f"{url}\n", "")
        if command[:3] == ["gh", "pr", "view"]:
            bodies = [{"body": b} for b in self.comments.get(int(command[3]), [])]
            return subprocess.CompletedProcess(command, 0, json.dumps({"comments": bodies}), "")
        if command[:3] == ["gh", "pr", "comment"]:
            self.comments.setdefault(int(command[3]), []).append(command[command.index("--body") + 1])
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] in (["gh", "pr", "edit"], ["gh", "issue", "edit"]):
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", f"unexpected: {command}")

    def _reducer(self):
        return reducer.Reducer(runner=self._runner, jobs_root=self.jobs)

    def test_ensure_pr_creates_once_then_reuses_open_pr(self):
        issue = {"number": 108}
        labels = ["risk:medium", "ai:generated"]
        self.fail_once["gh pr create"] = 1
        failed = self._reducer().ensure_pr(
            self.worktree, BRANCH, issue, "job-1", "title", "body", labels
        )
        self.assertFalse(failed.ok)
        self.assertIn("gh pr create 失败", failed.reason)
        first = self._reducer().ensure_pr(
            self.worktree, BRANCH, issue, "job-1", "title", "body", labels
        )
        self.assertTrue(first.ok, first.reason)
        self.assertEqual(first.pr["number"], 77)
        creates = [c for c in self.calls if c[:3] == ["gh", "pr", "create"]]
        self.assertEqual(len(creates), 2)
        self.assertIn("<!-- job:job-1 -->", creates[-1][creates[-1].index("--body") + 1])
        log_text = (self.jobs / "job-1" / "operations.jsonl").read_text(encoding="utf-8")
        self.assertIn('"step": "pr"', log_text)
        self.assertIn('"step": "labels"', log_text)
        self.assertIn('"status": "failed"', log_text)

        second = self._reducer().ensure_pr(
            self.worktree, BRANCH, issue, "job-1", "title", "body", labels
        )
        self.assertTrue(second.ok, second.reason)
        self.assertTrue(second.skipped)

    def test_comment_pr_posts_once_and_marker_blocks_duplicate(self):
        first = self._reducer().comment_pr(77, "job-1", "salvage note")
        self.assertTrue(first.ok, first.reason)
        second = self._reducer().comment_pr(77, "job-1", "salvage note")
        self.assertTrue(second.skipped)
        self.assertEqual(len(self.comments[77]), 1)
        self.assertIn("<!-- job:job-1 -->", self.comments[77][0])
        (self.jobs / "job-1" / "operations.jsonl").unlink()
        third = self._reducer().comment_pr(77, "job-1", "again")
        self.assertTrue(third.skipped)
        self.assertEqual(len(self.comments[77]), 1)

    def test_set_labels_is_idempotent_and_supports_issues(self):
        self.fail_once["gh pr edit"] = 1
        first = self._reducer().set_labels(
            "pr", 77, ["risk:medium", "ai:generated"], [], "job-1"
        )
        self.assertFalse(first.ok)
        second = self._reducer().set_labels(
            "pr", 77, ["ai:generated", "risk:medium"], [], "job-1"
        )
        self.assertTrue(second.ok, second.reason)
        again = self._reducer().set_labels(
            "pr", 77, ["ai:generated", "risk:medium"], [], "job-1"
        )
        self.assertTrue(again.skipped)
        issue = self._reducer().set_labels(
            "issue", 108, ["ai:generated"], ["status:ready"], "job-2"
        )
        self.assertTrue(issue.ok, issue.reason)
        issue_edit = [c for c in self.calls if c[:3] == ["gh", "issue", "edit"]][0]
        self.assertIn("--remove-label", issue_edit)
