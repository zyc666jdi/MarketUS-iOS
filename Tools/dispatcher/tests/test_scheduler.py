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
import threading
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


class SchedulerRunOnceTests(unittest.TestCase):
    """Phase 1c-iii execution: claim, heartbeat, role command, finish/requeue."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "jobs.db"
        self.now = 1_000_000.0
        self.store = job_store.JobStore(self.db, now=lambda: self.now)

    def enqueue(self, role="implementer", ticket=134, pr=None, *, now=100.0,
                prompt_hash=None, store=None):
        return (store or self.store).enqueue(
            role,
            ticket,
            pr,
            prompt_hash=prompt_hash or PROMPT_HASHES.get(role, "sha256:job"),
            now=now,
        )

    @staticmethod
    def completed(code=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def run_tick(self, **kwargs):
        kwargs.setdefault("owner", "tester")
        kwargs.setdefault("lease_seconds", 60)
        kwargs.setdefault("heartbeat_interval", 0.01)
        return scheduler.run_once(self.store, **kwargs)

    def test_success_marks_job_done_with_dispatcher_command(self):
        job = self.enqueue()
        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(0, "ok")
        ) as run:
            result = self.run_tick()

        self.assertEqual(result["action"], "executed")
        self.assertEqual(result["state"], "done")
        self.assertTrue(result["finished"])
        self.assertTrue(result["executed"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["error"], "")
        self.assertTrue(result["heartbeat"]["started"])
        self.assertTrue(result["heartbeat"]["stopped"])
        self.assertFalse(result["heartbeat"]["alive"])

        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "done")
        self.assertEqual(stored["attempt"], 1)
        self.assertIsNone(stored["lease_owner"])

        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                sys.executable,
                str(scheduler.DISPATCHER_PATH),
                "once",
                "--issue",
                "134",
                "--execute",
            ],
        )
        self.assertNotIn("merge", command)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_each_role_maps_to_its_existing_dispatcher_entry_point(self):
        cases = (
            ("implementer", None, ["once", "--issue", "134", "--execute"]),
            ("reviewer", 42, ["review", "--pr", "42", "--execute"]),
            ("repair", 43, ["repair", "--pr", "43", "--execute"]),
        )
        for role, pr, tail in cases:
            with self.subTest(role=role):
                db = Path(self.tmp.name) / f"{role}.db"
                store = job_store.JobStore(db)
                job = self.enqueue(role=role, ticket=134, pr=pr, store=store)
                with mock.patch.object(
                    scheduler.subprocess, "run", return_value=self.completed(0)
                ) as run:
                    result = scheduler.run_once(
                        store,
                        owner="tester",
                        lease_seconds=60,
                        heartbeat_interval=0.01,
                    )
                self.assertEqual(result["action"], "executed")
                self.assertEqual(result["state"], "done")
                self.assertEqual(run.call_args.args[0][1:], [str(scheduler.DISPATCHER_PATH), *tail])
                self.assertEqual(store.get_job(job["job_id"])["state"], "done")

    def test_nonzero_exit_marks_job_failed_with_truncated_stderr(self):
        job = self.enqueue()
        stderr = "x" * 5000
        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(7, "out", stderr)
        ):
            result = self.run_tick()

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["finished"])
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["stdout"], "out")
        self.assertEqual(len(result["stderr"]), scheduler.MAX_ERROR_CHARS)
        self.assertTrue(result["stderr"].endswith("x" * 10))
        self.assertLessEqual(len(result["error"]), scheduler.MAX_ERROR_CHARS)
        self.assertTrue(result["error"].endswith("x" * 10))
        self.assertTrue(result["heartbeat"]["stopped"])

        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["error"], result["error"])
        self.assertIsNone(stored["lease_owner"])

    def test_lock_busy_requeues_with_short_backoff_without_marking_failed(self):
        job = self.enqueue()
        with mock.patch.object(
            scheduler.subprocess,
            "run",
            return_value=self.completed(scheduler.EXIT_LOCK_BUSY, "", "lock busy"),
        ):
            result = self.run_tick()

        self.assertEqual(result["action"], "requeued")
        self.assertEqual(result["state"], "queued")
        self.assertTrue(result["finished"])
        self.assertEqual(result["error"], "")
        self.assertEqual(result["retry_delay"], scheduler.LOCK_BUSY_RETRY_SECONDS)
        self.assertEqual(result["retry_at"], self.now + scheduler.LOCK_BUSY_RETRY_SECONDS)
        self.assertTrue(result["heartbeat"]["stopped"])

        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["error"], "")
        self.assertEqual(stored["attempt"], 2)
        self.assertEqual(stored["retry_at"], self.now + scheduler.LOCK_BUSY_RETRY_SECONDS)
        self.assertIsNone(stored["lease_owner"])
        self.assertIsNone(stored["lease_until"])
        self.assertIsNone(self.store.claim("too-early", 60, now=self.now + 4.0))

        reclaimed = self.store.claim("next-tick", 60, now=self.now + 5.0)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["attempt"], 3)

    def test_github_timeout_requeues_with_backoff_instead_of_failing(self):
        job = self.enqueue()
        stderr = "gh: HTTP request timed out after 30s"
        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(1, "", stderr)
        ):
            result = self.run_tick(recovery=lambda _job: {"ok": True, "retryable": True})

        self.assertEqual(result["action"], "requeued")
        self.assertEqual(result["state"], "queued")
        self.assertTrue(result["finished"])
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["stderr"], stderr)
        self.assertEqual(result["error"], "")
        self.assertEqual(result["retry_delay"], 60.0)
        self.assertEqual(result["retry_at"], self.now + 60.0)

        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempt"], 2)
        self.assertEqual(stored["retry_at"], self.now + 60.0)
        self.assertIn("timed out", stored["error"])
        self.assertIsNone(self.store.claim("too-early", 60, now=self.now + 59.0))

        reclaimed = self.store.claim("next-tick", 60, now=self.now + 60.0)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["attempt"], 3)

    def test_infra_without_recovery_fails_closed(self):
        job = self.enqueue()
        with mock.patch.object(
            scheduler.subprocess,
            "run",
            return_value=self.completed(1, "", "HTTP request timed out"),
        ):
            result = self.run_tick()

        self.assertEqual(result["action"], "failed")
        self.assertFalse(result["retryable"])
        self.assertIn("未配置 retry recovery", result["error"])
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertIsNone(stored["retry_at"])

    def test_infra_requeue_runs_recovery_before_requeue(self):
        job = self.enqueue()
        seen = []

        def recovery(claimed):
            seen.append(claimed)
            return {
                "ok": True,
                "retryable": True,
                "status": "status:ready",
                "worktree_action": "cleaned",
                "reason": "",
            }

        with mock.patch.object(
            scheduler.subprocess,
            "run",
            return_value=self.completed(1, "", "HTTP request timed out"),
        ):
            result = self.run_tick(recovery=recovery)

        self.assertEqual(result["action"], "requeued")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["recovery"]["status"], "status:ready")
        self.assertEqual(result["recovery"]["worktree_action"], "cleaned")
        self.assertEqual(seen[0]["job_id"], job["job_id"])
        self.assertEqual(seen[0]["role"], "implementer")
        self.assertEqual(seen[0]["ticket"], 134)
        self.assertEqual(seen[0]["attempt"], 1)
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "queued")

    def test_infra_recovery_failure_fails_job_with_visible_reason(self):
        job = self.enqueue()

        def recovery(_claimed):
            return {
                "ok": False,
                "retryable": False,
                "status": "status:ready",
                "reason": "worktree 有未提交改动，保留以避免丢失 Agent 工作",
            }

        with mock.patch.object(
            scheduler.subprocess,
            "run",
            return_value=self.completed(1, "", "connection reset by peer"),
        ):
            result = self.run_tick(recovery=recovery)

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["retryable"])
        self.assertIn("retry recovery failed", result["error"])
        self.assertIn("未提交改动", result["error"])
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertIn("未提交改动", stored["error"])
        self.assertIsNone(stored["retry_at"])
        self.assertIsNone(self.store.claim("next-tick", 60, now=self.now + 60.0))

    def test_infra_recovery_exception_fails_job_with_clear_error(self):
        job = self.enqueue()

        def recovery(_claimed):
            raise RuntimeError("gh down")

        with mock.patch.object(
            scheduler.subprocess,
            "run",
            return_value=self.completed(1, "", "GraphQL: network error"),
        ):
            result = self.run_tick(recovery=recovery)

        self.assertEqual(result["action"], "failed")
        self.assertIn("RuntimeError: gh down", result["error"])
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "failed")

    def test_next_tick_after_infra_recovery_requeue_can_execute(self):
        job = self.enqueue()
        before = {
            "number": 134,
            "title": "test ticket",
            "state": "open",
            "labels": [
                {"name": "status:claimed"},
                {"name": "status:in-progress"},
            ],
        }
        after = {
            **before,
            "labels": [{"name": "status:ready"}],
        }
        status_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(dispatcher, "WORKTREE_ROOT", Path(tmp)),
                mock.patch.object(
                    dispatcher, "issue_detail", side_effect=[before, after]
                ),
                mock.patch.object(
                    dispatcher, "clean_issue_worktree", return_value=""
                ) as clean,
                mock.patch.object(
                    dispatcher,
                    "gh",
                    side_effect=lambda args: status_calls.append(args),
                ),
                mock.patch.object(
                    scheduler.subprocess,
                    "run",
                    return_value=self.completed(1, "", "HTTP request timed out"),
                ),
            ):
                first = self.run_tick(recovery=dispatcher.recover_scheduler_retry)
            worktree = Path(tmp) / "134-test-ticket"

        self.assertEqual(first["action"], "requeued")
        self.assertEqual(first["recovery"]["status"], "status:ready")
        self.assertEqual(first["recovery"]["worktree_action"], "cleaned")
        self.assertEqual(first["state"], "queued")
        clean.assert_called_once_with(
            "ai/134-test-ticket", worktree, remove=True
        )
        self.assertIn("status:ready", status_calls[0])
        self.assertIn("status:claimed", status_calls[0])

        self.now += first["retry_delay"]
        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(0, "ok")
        ) as run:
            second = self.run_tick()

        self.assertEqual(second["action"], "executed")
        self.assertEqual(second["state"], "done")
        self.assertEqual(run.call_count, 1)
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "done")
        self.assertEqual(stored["attempt"], 3)

    def test_lock_busy_does_not_invoke_retry_recovery(self):
        self.enqueue()

        def recovery(_claimed):
            raise AssertionError("lock busy must not run recovery")

        with mock.patch.object(
            scheduler.subprocess,
            "run",
            return_value=self.completed(scheduler.EXIT_LOCK_BUSY),
        ):
            result = self.run_tick(recovery=recovery)

        self.assertEqual(result["action"], "requeued")

    def test_github_infrastructure_classifier_covers_required_failures(self):
        infrastructure = (
            "timeout",
            "request timed out",
            "connection refused",
            "API rate limit exceeded",
            "HTTP 502: Bad Gateway",
            "status code: 503",
            "GraphQL: network error",
            "network is unreachable",
        )
        for text in infrastructure:
            with self.subTest(text=text):
                self.assertTrue(scheduler.is_github_infrastructure_error(text))
        for text in ("assertion failed", "syntax error", "agent exited 1", ""):
            with self.subTest(text=text):
                self.assertFalse(scheduler.is_github_infrastructure_error(text))

    def test_github_retry_delay_grows_linearly_and_caps_at_fifteen_minutes(self):
        self.assertEqual(scheduler.github_retry_delay(1), 60.0)
        self.assertEqual(scheduler.github_retry_delay(2), 120.0)
        self.assertEqual(scheduler.github_retry_delay(0), 60.0)
        self.assertEqual(scheduler.github_retry_delay(14), 840.0)
        self.assertEqual(scheduler.github_retry_delay(15), 900.0)
        self.assertEqual(scheduler.github_retry_delay(99), 900.0)

    def test_non_infra_failure_keeps_stdout_and_stderr_tails(self):
        job = self.enqueue()
        stdout = "agent output line"
        stderr = "fatal: validation failed"
        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(1, stdout, stderr)
        ):
            result = self.run_tick()

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["stdout"], stdout)
        self.assertEqual(result["stderr"], stderr)
        self.assertIn("stdout:\n" + stdout, result["error"])
        self.assertIn("stderr:\n" + stderr, result["error"])
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["error"], result["error"])

    def test_heartbeat_renews_while_command_runs_and_stops_afterwards(self):
        job = self.enqueue()
        first_beat = threading.Event()
        calls = []

        def fake_heartbeat(job_id, owner, ttl, **kwargs):
            calls.append((job_id, owner, ttl))
            first_beat.set()
            return True

        def fake_run(command, **kwargs):
            if not first_beat.wait(2.0):
                return subprocess.CompletedProcess(command, 99, "", "no heartbeat")
            return subprocess.CompletedProcess(command, 0, "ok", "")

        with (
            mock.patch.object(self.store, "heartbeat", side_effect=fake_heartbeat),
            mock.patch.object(scheduler.subprocess, "run", side_effect=fake_run),
        ):
            result = self.run_tick(owner="beat-owner", lease_seconds=60)

        self.assertEqual(calls[0], (job["job_id"], "beat-owner", 60))
        self.assertGreaterEqual(result["heartbeat"]["beats"], 1)
        self.assertTrue(result["heartbeat"]["started"])
        self.assertTrue(result["heartbeat"]["stopped"])
        self.assertFalse(result["heartbeat"]["alive"])
        self.assertFalse(
            any(
                thread.name == f"scheduler-heartbeat-{job['job_id']}"
                and thread.is_alive()
                for thread in threading.enumerate()
            )
        )
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "done")

    def test_one_tick_claims_only_one_job_and_the_next_tick_gets_the_other(self):
        first = self.enqueue(ticket=134, now=100.0)
        second = self.enqueue(ticket=135, now=200.0)
        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(0)
        ) as run:
            first_result = self.run_tick()

        self.assertEqual(run.call_count, 1)
        self.assertEqual(first_result["job"]["job_id"], first["job_id"])
        self.assertEqual(self.store.get_job(first["job_id"])["state"], "done")
        self.assertEqual(self.store.get_job(second["job_id"])["state"], "queued")

        with mock.patch.object(
            scheduler.subprocess, "run", return_value=self.completed(0)
        ) as second_run:
            second_result = self.run_tick()
        self.assertEqual(second_run.call_count, 1)
        self.assertEqual(second_result["job"]["job_id"], second["job_id"])
        self.assertEqual(self.store.get_job(second["job_id"])["state"], "done")

        with mock.patch.object(scheduler.subprocess, "run") as idle_run:
            idle = self.run_tick()
        idle_run.assert_not_called()
        self.assertEqual(idle["action"], "idle")
        self.assertIsNone(idle["job"])
        self.assertFalse(idle["heartbeat"]["started"])

    def test_a_second_tick_cannot_execute_an_already_leased_job(self):
        job = self.enqueue()
        running = threading.Event()
        release = threading.Event()
        calls = []
        results = {}

        def fake_run(command, **kwargs):
            calls.append(command)
            running.set()
            if not release.wait(2.0):
                return subprocess.CompletedProcess(command, 99, "", "stuck")
            return subprocess.CompletedProcess(command, 0, "ok", "")

        def worker():
            results["first"] = scheduler.run_once(
                self.store,
                owner="tick-a",
                lease_seconds=60,
                heartbeat_interval=0.01,
            )

        with mock.patch.object(scheduler.subprocess, "run", side_effect=fake_run):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(running.wait(2.0))
                second = scheduler.run_once(
                    self.store,
                    owner="tick-b",
                    lease_seconds=60,
                    heartbeat_interval=0.01,
                )
                self.assertEqual(second["action"], "idle")
                self.assertIsNone(second["job"])
            finally:
                release.set()
                thread.join(timeout=3.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(results["first"]["state"], "done")
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "done")

    def test_oserror_marks_job_failed_and_still_stops_heartbeat(self):
        job = self.enqueue()
        with mock.patch.object(
            scheduler.subprocess, "run", side_effect=OSError("cannot exec")
        ):
            result = self.run_tick()

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["exit_code"])
        self.assertIn("cannot exec", result["error"])
        self.assertTrue(result["heartbeat"]["started"])
        self.assertTrue(result["heartbeat"]["stopped"])
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "failed")

    def test_unknown_role_fails_without_running_a_command(self):
        job = self.enqueue(role="planner", prompt_hash="sha256:planner")
        with mock.patch.object(scheduler.subprocess, "run") as run:
            result = self.run_tick()

        run.assert_not_called()
        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertIn("未知 role", result["error"])
        self.assertFalse(result["heartbeat"]["started"])
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "failed")

    def test_lock_busy_exit_code_matches_dispatcher_contract(self):
        self.assertEqual(scheduler.EXIT_LOCK_BUSY, dispatcher.EXIT_LOCK_BUSY)
        self.assertEqual(dispatcher.EXIT_LOCK_BUSY, 2)

    def test_dry_run_previews_without_claiming_or_executing(self):
        job = self.enqueue()
        with mock.patch.object(scheduler.subprocess, "run") as run:
            result = self.run_tick(execute=False)

        run.assert_not_called()
        self.assertEqual(result["action"], "dry_run")
        self.assertEqual(result["state"], "queued")
        self.assertFalse(result["executed"])
        self.assertEqual(result["command"][2:], ["once", "--issue", "134", "--execute"])
        self.assertFalse(result["heartbeat"]["started"])
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempt"], 0)
        self.assertIsNone(stored["lease_owner"])

    def test_empty_queue_is_idle(self):
        with mock.patch.object(scheduler.subprocess, "run") as run:
            result = self.run_tick()

        run.assert_not_called()
        self.assertEqual(result["action"], "idle")
        self.assertEqual(result["state"], None)
        self.assertFalse(result["executed"])
        self.assertFalse(result["finished"])

    def test_run_once_rejects_invalid_lease_or_heartbeat_interval(self):
        self.enqueue()
        with self.assertRaises(ValueError):
            scheduler.run_once(self.store, lease_seconds=0)
        with self.assertRaises(ValueError):
            scheduler.run_once(self.store, heartbeat_interval=0)
        with self.assertRaises(ValueError):
            scheduler.run_once(self.store, heartbeat_interval=float("nan"))


class SchedulerRunCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "jobs.db"

    def run_cli(self, *argv):
        output = io.StringIO()
        with (
            mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db),
            mock.patch.object(sys, "argv", ["dispatcher.py", "scheduler", "run", *argv]),
            contextlib.redirect_stdout(output),
        ):
            code = dispatcher.main()
        return code, output.getvalue()

    def result(self, action="executed"):
        return {
            "action": action,
            "job": {
                "job_id": "implementer-134-abcdef0123456789",
                "role": "implementer",
                "ticket": 134,
                "pr": None,
                "state": "done" if action == "executed" else "failed",
                "attempt": 1,
            },
            "state": "done" if action == "executed" else "failed",
            "owner": "scheduler-1",
            "lease_seconds": 7200,
            "command": [sys.executable, str(scheduler.DISPATCHER_PATH), "once",
                        "--issue", "134", "--execute"],
            "exit_code": 0 if action == "executed" else 1,
            "error": "" if action == "executed" else "agent failed",
            "finished": True,
            "executed": True,
            "heartbeat": {
                "started": True, "stopped": True, "alive": False,
                "beats": 1, "rejected": 0, "errors": 0,
                "interval_seconds": 30.0,
            },
        }

    def test_parser_dispatches_scheduler_run_once_json(self):
        with (
            mock.patch.object(
                sys, "argv",
                ["dispatcher.py", "scheduler", "run", "--once", "--json"],
            ),
            mock.patch.object(dispatcher, "cmd_scheduler", return_value=0) as command,
        ):
            self.assertEqual(dispatcher.main(), 0)
        command.assert_called_once()
        args = command.call_args.args[0]
        self.assertEqual(args.scheduler_command, "run")
        self.assertTrue(args.once)
        self.assertTrue(args.json)

    def test_cli_json_reports_a_completed_tick(self):
        with mock.patch.object(
            scheduler, "run_once", return_value=self.result()
        ) as run_once:
            code, text = self.run_cli("--once", "--json")

        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertEqual(payload["action"], "executed")
        self.assertEqual(payload["job"]["ticket"], 134)
        run_once.assert_called_once_with(
            mock.ANY, recovery=dispatcher.recover_scheduler_retry
        )

    def test_cli_returns_nonzero_for_a_failed_job(self):
        with mock.patch.object(scheduler, "run_once", return_value=self.result("failed")):
            code, text = self.run_cli("--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["error"], "agent failed")

    def test_cli_reports_execution_errors_without_traceback(self):
        output = io.StringIO()
        with (
            mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db),
            mock.patch.object(
                scheduler, "run_once", side_effect=scheduler.SchedulerError("db boom")
            ),
            contextlib.redirect_stdout(output),
        ):
            code = dispatcher.cmd_scheduler(
                argparse.Namespace(scheduler_command="run", json=False)
            )
        self.assertEqual(code, 1)
        self.assertIn("scheduler run: db boom", output.getvalue())


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
