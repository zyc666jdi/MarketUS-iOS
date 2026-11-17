"""Phase 1c-i tests: job identity, idempotent enqueue and the jobs CLI."""

import argparse
import contextlib
import io
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
import jobs  # noqa: E402
import reducer  # noqa: E402

PROMPT_HASH = "sha256:0123456789abcdef"


class JobStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "jobs.db"
        self.store = jobs.JobStore(self.db)

    def enqueue(self, ticket=114, pr=42, role="implementer", now=100.0, **overrides):
        options = {
            "base_sha": "base000",
            "head_sha": "head111",
            "prompt_hash": PROMPT_HASH,
        }
        options.update(overrides)
        return self.store.enqueue(role, ticket, pr, now=now, **options)


class JobIdTests(unittest.TestCase):
    def test_derive_job_id_is_deterministic_and_identity_sensitive(self):
        first = jobs.derive_job_id("implementer", 114, 42, "base", PROMPT_HASH)
        self.assertEqual(
            first, jobs.derive_job_id("implementer", 114, 42, "base", PROMPT_HASH)
        )
        self.assertRegex(first, r"^implementer-114-[0-9a-f]{16}$")
        self.assertNotEqual(
            first, jobs.derive_job_id("repair", 114, 42, "base", PROMPT_HASH)
        )
        self.assertNotEqual(
            first, jobs.derive_job_id("implementer", 115, 42, "base", PROMPT_HASH)
        )
        self.assertNotEqual(
            first, jobs.derive_job_id("implementer", 114, 43, "base", PROMPT_HASH)
        )
        self.assertNotEqual(
            first, jobs.derive_job_id("implementer", 114, 42, "other", PROMPT_HASH)
        )
        self.assertNotEqual(
            first, jobs.derive_job_id("implementer", 114, 42, "base", "other")
        )

    def test_derive_job_id_treats_missing_and_zero_pr_the_same(self):
        self.assertEqual(
            jobs.derive_job_id("implementer", 114, None, "base", PROMPT_HASH),
            jobs.derive_job_id("implementer", 114, 0, "base", PROMPT_HASH),
        )

    def test_derive_job_id_rejects_invalid_identity(self):
        with self.assertRaises(ValueError):
            jobs.derive_job_id("", 114, 1, "base", PROMPT_HASH)
        with self.assertRaises(ValueError):
            jobs.derive_job_id("implementer", 0, 1, "base", PROMPT_HASH)
        with self.assertRaises(ValueError):
            jobs.derive_job_id("implementer", 114, -1, "base", PROMPT_HASH)


class JobIdNamespaceTests(unittest.TestCase):
    def test_store_and_reducer_ids_are_distinct_namespaces(self):
        store_id = jobs.derive_job_id("implementer", 114, 42, "base", PROMPT_HASH)
        operation_id = reducer.derive_job_id(
            "implementer", 114, "base", "ai/114-job-store"
        )
        self.assertNotEqual(store_id, operation_id)
        self.assertIs(dispatcher.derive_operation_id, reducer.derive_job_id)


class EnqueueTests(JobStoreTestCase):
    def test_enqueue_same_identity_twice_keeps_one_queued_row(self):
        first = self.enqueue(now=100.0)
        second = self.enqueue(now=200.0, head_sha="different-head")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(second["state"], "queued")
        self.assertEqual(second["attempt"], 0)
        self.assertEqual(second["head_sha"], "head111")
        self.assertEqual(second["created_at"], 100.0)
        self.assertEqual(second["updated_at"], 100.0)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_enqueue_different_prompt_or_base_creates_new_jobs(self):
        first = self.enqueue()
        second = self.enqueue(prompt_hash="other-prompt")
        third = self.enqueue(base_sha="other-base")
        self.assertEqual(len({first["job_id"], second["job_id"], third["job_id"]}), 3)
        self.assertEqual(len(self.store.list_jobs()), 3)

    def test_enqueue_accepts_explicit_job_id(self):
        job = self.enqueue(job_id="manual-job-1")
        self.assertEqual(job["job_id"], "manual-job-1")
        self.assertEqual(self.store.get_job("manual-job-1")["state"], "queued")
        repeated = self.enqueue(job_id="manual-job-1", head_sha="new-head")
        self.assertEqual(repeated["job_id"], "manual-job-1")
        self.assertEqual(repeated["head_sha"], "head111")

    def test_enqueue_explicit_job_id_rejects_conflicting_identity(self):
        job = self.enqueue(job_id="shared-job")
        with self.assertRaises(ValueError):
            self.store.enqueue(
                "reviewer",
                115,
                43,
                base_sha="other-base",
                prompt_hash="other-prompt",
                job_id="shared-job",
                now=200.0,
            )
        stored = self.store.get_job("shared-job")
        self.assertEqual(stored["job_id"], job["job_id"])
        self.assertEqual(stored["role"], "implementer")
        self.assertEqual(stored["ticket"], 114)


if __name__ == "__main__":
    unittest.main()
