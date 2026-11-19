"""Phase 1c-ii tests: scheduler shadow planning, idempotent enqueue and CLI."""

import argparse
import contextlib
import hashlib
import inspect
import io
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
DISPATCHER_ROOT = ROOT / "Tools/dispatcher"
if str(DISPATCHER_ROOT) not in sys.path:
    sys.path.insert(0, str(DISPATCHER_ROOT))

import dispatcher  # noqa: E402
import jobs as job_store  # noqa: E402
import scheduler  # noqa: E402

PROMPT_HASHES = {
    "implementer": "sha256:implementer-prompt",
    "reviewer": "sha256:reviewer-prompt",
    "repair": "sha256:implementer-prompt",
}


def make_issue(number, status=None, *, extra_labels=(), state="OPEN"):
    labels = ([status] if status else []) + list(extra_labels)
    return {
        "number": number,
        "title": f"ticket {number}",
        "body": "",
        "state": state,
        "labels": [{"name": name} for name in labels],
    }


def make_pr(number, ticket=None, *, base_sha="base", head_sha=None, state="OPEN",
            draft=False, branch=None, body=None, author_association="OWNER",
            cross_repository=False):
    if branch is None:
        branch = f"ai/{ticket}-test" if ticket is not None else f"ai/{number}-test"
    if body is None:
        body = f"Fixes #{ticket}" if ticket is not None else ""
    return {
        "number": number,
        "title": f"pr {number}",
        "body": body,
        "headRefName": branch,
        "headRefOid": head_sha or f"head{number}",
        "baseRefOid": base_sha,
        "state": state,
        "isDraft": draft,
        "authorAssociation": author_association,
        "isCrossRepository": cross_repository,
    }


class PlanJobTests(unittest.TestCase):
    def test_ready_issue_maps_to_implementer_without_pr(self):
        planned = scheduler.plan_jobs(
            [make_issue(11, "status:ready")], [], prompt_hashes=PROMPT_HASHES
        )
        self.assertEqual(len(planned), 1)
        job = planned[0]
        self.assertEqual(job.role, "implementer")
        self.assertEqual(job.ticket, 11)
        self.assertIsNone(job.pr)
        self.assertEqual(job.base_sha, "")
        self.assertEqual(job.head_sha, "")
        self.assertEqual(job.prompt_hash, PROMPT_HASHES["implementer"])
        self.assertEqual(job.source_status, "status:ready")
        self.assertEqual(
            job.job_id,
            job_store.derive_job_id(
                "implementer", 11, None, "", PROMPT_HASHES["implementer"]
            ),
        )

    def test_in_review_issue_maps_to_reviewer_with_linked_pr(self):
        planned = scheduler.plan_jobs(
            [make_issue(12, "status:in-review")],
            [make_pr(120, 12, base_sha="base-12")],
            prompt_hashes=PROMPT_HASHES,
        )
        self.assertEqual(len(planned), 1)
        job = planned[0]
        self.assertEqual(job.role, "reviewer")
        self.assertEqual(job.pr, 120)
        self.assertEqual(job.base_sha, "base-12")
        self.assertEqual(job.head_sha, "head120")
        self.assertEqual(job.prompt_hash, PROMPT_HASHES["reviewer"])
        self.assertEqual(
            job.job_id,
            job_store.derive_job_id(
                "reviewer", 12, 120, "head120", PROMPT_HASHES["reviewer"]
            ),
        )

    def test_in_progress_issue_maps_to_repair_with_linked_pr(self):
        planned = scheduler.plan_jobs(
            [make_issue(13, "status:in-progress")],
            [make_pr(130, 13, base_sha="base-13")],
            prompt_hashes=PROMPT_HASHES,
        )
        self.assertEqual(len(planned), 1)
        job = planned[0]
        self.assertEqual(job.role, "repair")
        self.assertEqual(job.pr, 130)
        self.assertEqual(job.base_sha, "base-13")
        self.assertEqual(job.head_sha, "head130")
        self.assertEqual(job.prompt_hash, PROMPT_HASHES["implementer"])
        self.assertEqual(
            job.job_id,
            job_store.derive_job_id(
                "repair", 13, 130, "head130", PROMPT_HASHES["implementer"]
            ),
        )

    def test_status_precedence_is_deterministic(self):
        planned = scheduler.plan_jobs(
            [
                make_issue(14, "status:in-review", extra_labels=["status:ready"]),
                make_issue(15, "status:in-progress", extra_labels=["status:in-review"]),
            ],
            [make_pr(150, 15, base_sha="base-15")],
            prompt_hashes=PROMPT_HASHES,
        )
        self.assertEqual([job.role for job in planned], ["implementer", "reviewer"])
        self.assertEqual([job.ticket for job in planned], [14, 15])

    def test_non_actionable_or_closed_issues_produce_no_jobs(self):
        issues = [
            make_issue(16, "status:claimed"),
            make_issue(17, "status:awaiting-human"),
            make_issue(18, "status:blocked"),
            make_issue(19, "status:done"),
            make_issue(20),
            make_issue(21, "status:ready", state="CLOSED"),
        ]
        self.assertEqual(scheduler.plan_jobs(issues, [], prompt_hashes=PROMPT_HASHES), [])

    def test_review_and_repair_without_a_pr_are_not_planned(self):
        issues = [
            make_issue(22, "status:in-review"),
            make_issue(23, "status:in-progress"),
        ]
        self.assertEqual(scheduler.plan_jobs(issues, [], prompt_hashes=PROMPT_HASHES), [])

    def test_pr_link_falls_back_to_body_and_ignores_drafts(self):
        planned = scheduler.plan_jobs(
            [make_issue(24, "status:in-review")],
            [
                make_pr(240, 99, draft=True),
                make_pr(241, 24, branch="feature/review-fix", body="Closes #24"),
            ],
            prompt_hashes=PROMPT_HASHES,
        )
        self.assertEqual([job.pr for job in planned], [241])

        draft_only = scheduler.plan_jobs(
            [make_issue(25, "status:in-review")],
            [make_pr(250, 25, draft=True)],
            prompt_hashes=PROMPT_HASHES,
        )
        self.assertEqual(draft_only, [])

    def test_newest_open_pr_wins_for_the_same_ticket(self):
        planned = scheduler.plan_jobs(
            [make_issue(26, "status:in-review")],
            [make_pr(260, 26), make_pr(262, 26), make_pr(261, 26)],
            prompt_hashes=PROMPT_HASHES,
        )
        self.assertEqual([job.pr for job in planned], [262])

    def test_external_fork_pr_never_wins_by_higher_number(self):
        issue = make_issue(51, "status:in-review")
        trusted = make_pr(510, 51, head_sha="head-trusted")
        fork = make_pr(
            999,
            51,
            head_sha="head-fork",
            author_association="NONE",
            cross_repository=True,
        )
        owner_fork = make_pr(
            998,
            51,
            head_sha="head-owner-fork",
            author_association="OWNER",
            cross_repository=True,
        )
        planned = scheduler.plan_jobs(
            [issue], [fork, owner_fork, trusted], prompt_hashes=PROMPT_HASHES
        )
        self.assertEqual([job.pr for job in planned], [510])
        self.assertEqual([job.head_sha for job in planned], ["head-trusted"])
        self.assertEqual(
            scheduler.plan_jobs([issue], [fork], prompt_hashes=PROMPT_HASHES), []
        )

    def test_untrusted_same_repo_author_is_ignored(self):
        issue = make_issue(52, "status:in-review")
        untrusted = make_pr(520, 52, author_association="CONTRIBUTOR")
        trusted = make_pr(521, 52, author_association="MEMBER")
        planned = scheduler.plan_jobs(
            [issue], [untrusted, trusted], prompt_hashes=PROMPT_HASHES
        )
        self.assertEqual([job.pr for job in planned], [521])

    def test_missing_provenance_fields_fail_closed(self):
        issue = make_issue(53, "status:in-review")
        missing_cross = make_pr(530, 53)
        del missing_cross["isCrossRepository"]
        missing_author = make_pr(531, 53)
        del missing_author["authorAssociation"]
        for pr in (missing_cross, missing_author):
            with self.subTest(pr=pr["number"]):
                self.assertEqual(
                    scheduler.plan_jobs(
                        [issue], [pr], prompt_hashes=PROMPT_HASHES
                    ),
                    [],
                )

    def test_prompt_hash_defaults_to_the_role_prompt_content(self):
        planned = scheduler.plan_jobs([make_issue(27, "status:ready")], [])
        expected = "sha256:" + hashlib.sha256(
            (ROOT / ".agent/prompts/implementer.md").read_bytes()
        ).hexdigest()
        self.assertEqual(planned[0].prompt_hash, expected)

    def test_prompt_hash_participates_in_the_job_identity(self):
        issue = make_issue(28, "status:in-review")
        pr = make_pr(280, 28, base_sha="base-28")
        first = scheduler.plan_jobs(
            [issue], [pr], prompt_hashes={"reviewer": "sha256:one"}
        )[0]
        second = scheduler.plan_jobs(
            [issue], [pr], prompt_hashes={"reviewer": "sha256:two"}
        )[0]
        self.assertNotEqual(first.job_id, second.job_id)

    def test_head_ref_oid_is_consumed_even_without_base_ref_oid(self):
        pr = make_pr(290, 29, head_sha="head-only")
        del pr["baseRefOid"]
        job = scheduler.plan_jobs(
            [make_issue(29, "status:in-review")],
            [pr],
            prompt_hashes=PROMPT_HASHES,
        )[0]
        self.assertEqual(job.base_sha, "")
        self.assertEqual(job.head_sha, "head-only")
        self.assertEqual(
            job.job_id,
            job_store.derive_job_id(
                "reviewer", 29, 290, "head-only", PROMPT_HASHES["reviewer"]
            ),
        )


class ShadowTickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "jobs.db"
        self.store = job_store.JobStore(self.db)

    def issues_and_prs(self):
        issues = [
            make_issue(31, "status:ready"),
            make_issue(32, "status:in-review"),
            make_issue(33, "status:in-progress"),
        ]
        prs = [
            make_pr(320, 32, base_sha="base-32"),
            make_pr(330, 33, base_sha="base-33"),
        ]
        return issues, prs

    def test_shadow_tick_enqueues_three_roles_as_queued(self):
        issues, prs = self.issues_and_prs()
        result = scheduler.shadow_tick(
            self.store, issues, prs, prompt_hashes=PROMPT_HASHES
        )

        self.assertEqual(result["created"], 3)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["missing_pr"], 0)
        self.assertEqual(len(result["desired"]), 3)
        self.assertEqual(result["db_counts"]["queued"], 3)

        rows = {int(row["ticket"]): row for row in self.store.list_jobs()}
        self.assertEqual(set(rows), {31, 32, 33})
        self.assertEqual(rows[31]["role"], "implementer")
        self.assertIsNone(rows[31]["pr"])
        self.assertEqual(rows[31]["base_sha"], "")
        self.assertEqual(rows[31]["head_sha"], "")
        self.assertEqual(rows[32]["role"], "reviewer")
        self.assertEqual(rows[32]["pr"], 320)
        self.assertEqual(rows[32]["base_sha"], "base-32")
        self.assertEqual(rows[32]["head_sha"], "head320")
        self.assertEqual(rows[33]["role"], "repair")
        self.assertEqual(rows[33]["pr"], 330)
        self.assertEqual(rows[33]["base_sha"], "base-33")
        self.assertEqual(rows[33]["head_sha"], "head330")
        for row in rows.values():
            self.assertEqual(row["state"], "queued")
            self.assertEqual(row["attempt"], 0)
            self.assertIsNone(row["lease_owner"])
            self.assertIsNone(row["lease_until"])
            self.assertIsNone(row["heartbeat_at"])
        self.assertEqual(len(result["diff"]["created"]), 3)
        self.assertEqual(result["diff"]["unchanged"], [])

    def test_second_shadow_tick_is_idempotent(self):
        issues, prs = self.issues_and_prs()
        first = scheduler.shadow_tick(
            self.store, issues, prs, prompt_hashes=PROMPT_HASHES
        )
        rows_after_first = self.store.list_jobs()

        second = scheduler.shadow_tick(
            self.store, issues, prs, prompt_hashes=PROMPT_HASHES
        )
        rows_after_second = self.store.list_jobs()

        self.assertEqual(first["created"], 3)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped"], 3)
        self.assertEqual(second["missing_pr"], 0)
        self.assertEqual(second["db_counts"]["queued"], 3)
        self.assertEqual(second["diff"]["created"], [])
        self.assertEqual(len(second["diff"]["unchanged"]), 3)
        self.assertEqual(rows_after_first, rows_after_second)

    def test_fork_pr_cannot_hijack_shadow_tick(self):
        issue = make_issue(54, "status:in-review")
        trusted = make_pr(540, 54, head_sha="head-trusted")
        fork = make_pr(
            999,
            54,
            head_sha="head-fork",
            author_association="NONE",
            cross_repository=True,
        )
        result = scheduler.shadow_tick(
            self.store, [issue], [fork, trusted], prompt_hashes=PROMPT_HASHES
        )
        rows = self.store.list_jobs()
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["missing_pr"], 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pr"], 540)
        self.assertEqual(rows[0]["head_sha"], "head-trusted")

    def test_head_change_with_same_base_creates_a_second_job(self):
        issue = make_issue(43, "status:in-review")
        first_pr = make_pr(430, 43, base_sha="base-430", head_sha="head-a")
        second_pr = make_pr(430, 43, base_sha="base-430", head_sha="head-b")

        first_job = scheduler.plan_jobs(
            [issue], [first_pr], prompt_hashes=PROMPT_HASHES
        )[0]
        second_job = scheduler.plan_jobs(
            [issue], [second_pr], prompt_hashes=PROMPT_HASHES
        )[0]
        self.assertNotEqual(first_job.job_id, second_job.job_id)

        first_tick = scheduler.shadow_tick(
            self.store, [issue], [first_pr], prompt_hashes=PROMPT_HASHES
        )
        second_tick = scheduler.shadow_tick(
            self.store, [issue], [second_pr], prompt_hashes=PROMPT_HASHES
        )
        rows = self.store.list_jobs()

        self.assertEqual(first_tick["created"], 1)
        self.assertEqual(second_tick["created"], 1)
        self.assertEqual(second_tick["skipped"], 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["head_sha"] for row in rows}, {"head-a", "head-b"})
        self.assertEqual({row["base_sha"] for row in rows}, {"base-430"})

    def test_same_head_twice_keeps_one_job(self):
        issue = make_issue(44, "status:in-review")
        pr = make_pr(440, 44, base_sha="base-440", head_sha="head-same")
        first = scheduler.shadow_tick(
            self.store, [issue], [pr], prompt_hashes=PROMPT_HASHES
        )
        second = scheduler.shadow_tick(
            self.store, [issue], [pr], prompt_hashes=PROMPT_HASHES
        )
        rows = self.store.list_jobs()
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["head_sha"], "head-same")

    def test_base_change_with_same_head_creates_no_new_job(self):
        issue = make_issue(45, "status:in-review")
        first_pr = make_pr(450, 45, base_sha="base-old", head_sha="head-stable")
        second_pr = make_pr(450, 45, base_sha="base-new", head_sha="head-stable")

        first_job = scheduler.plan_jobs(
            [issue], [first_pr], prompt_hashes=PROMPT_HASHES
        )[0]
        second_job = scheduler.plan_jobs(
            [issue], [second_pr], prompt_hashes=PROMPT_HASHES
        )[0]
        self.assertEqual(first_job.job_id, second_job.job_id)

        first_tick = scheduler.shadow_tick(
            self.store, [issue], [first_pr], prompt_hashes=PROMPT_HASHES
        )
        second_tick = scheduler.shadow_tick(
            self.store, [issue], [second_pr], prompt_hashes=PROMPT_HASHES
        )
        rows = self.store.list_jobs()

        self.assertEqual(first_tick["created"], 1)
        self.assertEqual(second_tick["created"], 0)
        self.assertEqual(second_tick["skipped"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["head_sha"], "head-stable")
        self.assertEqual(rows[0]["base_sha"], "base-old")

    def test_missing_pr_is_reported_without_enqueueing(self):
        issues = [
            make_issue(34, "status:in-review"),
            make_issue(35, "status:in-progress"),
        ]
        result = scheduler.shadow_tick(
            self.store, issues, [], prompt_hashes=PROMPT_HASHES
        )

        self.assertEqual(result["desired"], [])
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["missing_pr"], 2)
        self.assertEqual(
            result["diff"]["missing_pr"],
            [
                {"ticket": 34, "status": "status:in-review"},
                {"ticket": 35, "status": "status:in-progress"},
            ],
        )
        self.assertEqual(self.store.list_jobs(), [])

    def test_ready_issue_still_enqueues_when_another_issue_has_no_pr(self):
        issues = [
            make_issue(36, "status:ready"),
            make_issue(37, "status:in-review"),
        ]
        result = scheduler.shadow_tick(
            self.store, issues, [], prompt_hashes=PROMPT_HASHES
        )
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["desired"][0]["ticket"], 36)
        self.assertEqual(result["missing_pr"], 1)

    def test_shadow_tick_does_not_claim_execute_or_write_github(self):
        issues, prs = self.issues_and_prs()
        issues_before = deepcopy(issues)
        prs_before = deepcopy(prs)
        with (
            mock.patch.object(job_store.JobStore, "claim") as claim,
            mock.patch.object(job_store.JobStore, "heartbeat") as heartbeat,
            mock.patch.object(job_store.JobStore, "finish") as finish,
            mock.patch.object(scheduler.subprocess, "run") as run,
        ):
            result = scheduler.shadow_tick(
                self.store, issues, prs, prompt_hashes=PROMPT_HASHES
            )

        self.assertEqual(result["created"], 3)
        claim.assert_not_called()
        heartbeat.assert_not_called()
        finish.assert_not_called()
        run.assert_not_called()
        self.assertEqual(issues, issues_before)
        self.assertEqual(prs, prs_before)
        for row in self.store.list_jobs():
            self.assertEqual(row["state"], "queued")
            self.assertEqual(row["attempt"], 0)
            self.assertIsNone(row["lease_owner"])

    def test_diff_reports_state_mismatch_and_orphaned_jobs(self):
        issue = make_issue(38, "status:in-review")
        pr = make_pr(380, 38, base_sha="base-38")
        desired = scheduler.plan_jobs(
            [issue], [pr], prompt_hashes=PROMPT_HASHES
        )[0]
        self.store.enqueue(
            desired.role,
            desired.ticket,
            desired.pr,
            desired.base_sha,
            head_sha=desired.head_sha,
            prompt_hash=desired.prompt_hash,
            job_id=desired.job_id,
        )
        self.store.claim("runner-a", 60)
        self.assertTrue(self.store.finish(desired.job_id, "done", owner="runner-a"))
        orphan = self.store.enqueue(
            "implementer", 99, None, "", prompt_hash=PROMPT_HASHES["implementer"]
        )

        result = scheduler.shadow_tick(
            self.store, [issue], [pr], prompt_hashes=PROMPT_HASHES
        )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(
            result["diff"]["state_mismatch"],
            [
                {
                    "job_id": desired.job_id,
                    "role": "reviewer",
                    "ticket": 38,
                    "pr": 380,
                    "state": "done",
                    "desired_state": "queued",
                }
            ],
        )
        self.assertEqual(
            [item["job_id"] for item in result["diff"]["orphaned"]],
            [orphan["job_id"]],
        )
        self.assertEqual(result["db_counts"]["done"], 1)
        self.assertEqual(result["db_counts"]["queued"], 1)

    def test_shadow_tick_reads_gh_when_inputs_are_omitted(self):
        issues = [make_issue(39, "status:ready")]
        with (
            mock.patch.object(scheduler, "read_issues", return_value=issues) as read_issues,
            mock.patch.object(scheduler, "read_prs", return_value=[]) as read_prs,
        ):
            result = scheduler.shadow_tick(self.store, prompt_hashes=PROMPT_HASHES)
        read_issues.assert_called_once_with()
        read_prs.assert_called_once_with()
        self.assertEqual(result["created"], 1)


class ReadOnlyGhTests(unittest.TestCase):
    def test_read_helpers_use_only_read_commands(self):
        completed = subprocess.CompletedProcess([], 0, "[]", "")
        with mock.patch.object(scheduler.subprocess, "run", return_value=completed) as run:
            self.assertEqual(scheduler.read_issues(), [])
            self.assertEqual(scheduler.read_prs(), [])
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0][:3], ["gh", "issue", "list"])
        self.assertEqual(calls[1][:3], ["gh", "api", "--paginate"])
        self.assertIn("--slurp", calls[1])
        self.assertIn("pulls?state=open", calls[1][-1])
        for command in calls:
            for write_command in ("edit", "create", "comment", "merge", "push"):
                self.assertNotIn(write_command, command)

    def test_read_prs_normalizes_rest_provenance_fields(self):
        payload = [[
            {
                "number": 550,
                "title": "trusted",
                "body": "Fixes #55",
                "head": {
                    "ref": "ai/55-x", "sha": "head-550",
                    "repo": {"full_name": "owner/repo"},
                },
                "base": {
                    "ref": "main", "sha": "base",
                    "repo": {"full_name": "owner/repo"},
                },
                "state": "open",
                "draft": False,
                "html_url": "https://example.test/550",
                "author_association": "MEMBER",
            },
            {
                "number": 551,
                "title": "fork",
                "body": "Fixes #55",
                "head": {
                    "ref": "ai/55-x", "sha": "head-551",
                    "repo": {"full_name": "fork/repo"},
                },
                "base": {
                    "ref": "main", "sha": "base",
                    "repo": {"full_name": "owner/repo"},
                },
                "state": "open",
                "draft": False,
                "html_url": "https://example.test/551",
                "author_association": "NONE",
            },
            {
                "number": 552,
                "title": "missing repo metadata",
                "body": "Fixes #55",
                "head": {"ref": "ai/55-x", "sha": "head-552"},
                "base": {"ref": "main", "sha": "base"},
                "state": "open",
                "draft": False,
                "html_url": "https://example.test/552",
                "author_association": "OWNER",
            },
        ]]
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with mock.patch.object(scheduler.subprocess, "run", return_value=completed):
            prs = scheduler.read_prs()

        self.assertEqual(len(prs), 3)
        self.assertEqual(prs[0]["authorAssociation"], "MEMBER")
        self.assertIs(prs[0]["isCrossRepository"], False)
        self.assertEqual(prs[0]["headRefOid"], "head-550")
        self.assertEqual(prs[0]["baseRefOid"], "base")
        self.assertEqual(prs[0]["headRefName"], "ai/55-x")
        self.assertIs(prs[1]["isCrossRepository"], True)
        self.assertIsNone(prs[2]["isCrossRepository"])

    def test_read_helper_failure_raises_scheduler_error(self):
        failed = subprocess.CompletedProcess([], 1, "", "boom")
        with mock.patch.object(scheduler.subprocess, "run", return_value=failed):
            with self.assertRaises(scheduler.SchedulerError):
                scheduler.read_issues()


class SchedulerCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "jobs.db"
        self.issues = [
            make_issue(41, "status:ready"),
            make_issue(42, "status:in-review"),
        ]
        self.prs = [make_pr(420, 42, base_sha="base-42")]

    def run_cli(self, *, json_output=False):
        output = io.StringIO()
        argv = ["dispatcher.py", "scheduler", "shadow"]
        if json_output:
            argv.append("--json")
        with (
            mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db),
            mock.patch.object(scheduler, "read_issues", return_value=self.issues),
            mock.patch.object(scheduler, "read_prs", return_value=self.prs),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(output),
        ):
            code = dispatcher.main()
        return code, output.getvalue()

    def test_cli_prints_desired_counts_and_db_state_counts(self):
        code, text = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("scheduler shadow", text)
        self.assertIn("desired=2 created=2 skipped=0 missing_pr=0", text)
        self.assertIn("implementer", text)
        self.assertIn("reviewer", text)
        self.assertIn("db state counts:", text)
        self.assertRegex(text, r"queued\s+2")
        self.assertIn("diff vs existing DB:", text)

        rows = job_store.JobStore(self.db).list_jobs()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["role"] for row in rows}, {"implementer", "reviewer"})
        self.assertTrue(all(row["state"] == "queued" for row in rows))

        code, text = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("desired=2 created=0 skipped=2 missing_pr=0", text)
        self.assertEqual(len(job_store.JobStore(self.db).list_jobs()), 2)

    def test_cli_json_is_machine_readable_and_idempotent(self):
        code, text = self.run_cli(json_output=True)
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertEqual(payload["created"], 2)
        self.assertEqual(payload["skipped"], 0)
        self.assertEqual(payload["missing_pr"], 0)
        self.assertEqual(payload["db_counts"]["queued"], 2)
        self.assertEqual(
            {job["role"] for job in payload["desired"]},
            {"implementer", "reviewer"},
        )
        self.assertEqual(payload["diff"]["created"][0]["state"], "queued")

        code, text = self.run_cli(json_output=True)
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertEqual(payload["created"], 0)
        self.assertEqual(payload["skipped"], 2)

    def test_cli_reports_gh_failure_without_traceback(self):
        output = io.StringIO()
        with (
            mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db),
            mock.patch.object(
                scheduler,
                "shadow_tick",
                side_effect=scheduler.SchedulerError("gh boom"),
            ),
            contextlib.redirect_stdout(output),
        ):
            code = dispatcher.cmd_scheduler(
                argparse.Namespace(scheduler_command="shadow", json=False)
            )
        self.assertEqual(code, 1)
        self.assertIn("scheduler shadow: gh boom", output.getvalue())

    def test_parser_dispatches_scheduler_shadow(self):
        with (
            mock.patch.object(sys, "argv", ["dispatcher.py", "scheduler", "shadow", "--json"]),
            mock.patch.object(dispatcher, "cmd_scheduler", return_value=0) as command,
        ):
            self.assertEqual(dispatcher.main(), 0)
        command.assert_called_once()
        args = command.call_args.args[0]
        self.assertEqual(args.scheduler_command, "shadow")
        self.assertTrue(args.json)

    def test_shadow_is_not_wired_into_run(self):
        self.assertNotIn(
            "scheduler.shadow_tick", inspect.getsource(dispatcher._run_loop_phases)
        )
        self.assertNotIn(
            "scheduler.shadow_tick", inspect.getsource(dispatcher.cmd_run)
        )


if __name__ == "__main__":
    unittest.main()
