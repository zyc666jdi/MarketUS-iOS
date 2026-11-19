"""Phase 1c-i tests: SQLite job store, CAS leases, reaper and jobs CLI."""

import argparse
import contextlib
import io
import sqlite3
import sys
import tempfile
import threading
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
    def test_store_and_reducer_job_ids_are_distinct_namespaces(self):
        store_id = jobs.derive_job_id("implementer", 114, 42, "base000", PROMPT_HASH)
        operation_id = reducer.derive_job_id(
            "implementer", 114, "base000", "ai/114-job-store"
        )
        self.assertRegex(store_id, r"^implementer-114-[0-9a-f]{16}$")
        self.assertRegex(operation_id, r"^implementer-114-[0-9a-f]{16}$")
        self.assertNotEqual(store_id, operation_id)
        # The store binds pr + prompt_hash; the operation log binds branch.
        self.assertNotEqual(
            store_id, jobs.derive_job_id("implementer", 114, 43, "base000", PROMPT_HASH)
        )
        self.assertNotEqual(
            operation_id,
            reducer.derive_job_id("implementer", 114, "base000", "ai/other"),
        )
        # dispatcher.py keeps the reducer scheme explicitly named so scheduler
        # integration cannot mistake it for the store id.
        self.assertIs(dispatcher.derive_operation_id, reducer.derive_job_id)


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


class SchemaTests(JobStoreTestCase):
    def test_store_creates_wal_database_with_required_columns(self):
        self.assertTrue(self.db.is_file())
        connection = sqlite3.connect(self.db)
        try:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            ]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO jobs (job_id, role, ticket, state, created_at, updated_at) "
                    "VALUES ('bad', 'implementer', 1, 'unknown', 0, 0)"
                )
        finally:
            connection.close()
        self.assertEqual(mode.lower(), "wal")
        self.assertEqual(
            columns,
            [
                "job_id",
                "role",
                "ticket",
                "pr",
                "base_sha",
                "head_sha",
                "prompt_hash",
                "state",
                "attempt",
                "lease_owner",
                "lease_until",
                "heartbeat_at",
                "retry_at",
                "created_at",
                "updated_at",
                "error",
            ],
        )

    def test_old_database_is_migrated_with_retry_at_column(self):
        legacy_db = self.root / "legacy-jobs.db"
        connection = sqlite3.connect(legacy_db)
        try:
            connection.executescript(
                """
                CREATE TABLE jobs (
                    job_id       TEXT PRIMARY KEY,
                    role         TEXT NOT NULL,
                    ticket       INTEGER NOT NULL,
                    pr           INTEGER,
                    base_sha     TEXT NOT NULL DEFAULT '',
                    head_sha     TEXT NOT NULL DEFAULT '',
                    prompt_hash  TEXT NOT NULL DEFAULT '',
                    state        TEXT NOT NULL DEFAULT 'queued',
                    attempt      INTEGER NOT NULL DEFAULT 0,
                    lease_owner  TEXT,
                    lease_until  REAL,
                    heartbeat_at REAL,
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL,
                    error        TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX idx_jobs_state_lease ON jobs (state, lease_until);
                CREATE INDEX idx_jobs_created ON jobs (created_at);
                INSERT INTO jobs (
                    job_id, role, ticket, state, attempt, created_at, updated_at
                ) VALUES ('old-job', 'implementer', 7, 'queued', 2, 100.0, 100.0);
                """
            )
            connection.commit()
        finally:
            connection.close()

        store = jobs.JobStore(legacy_db)
        connection = sqlite3.connect(legacy_db)
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(jobs)")
            }
        finally:
            connection.close()
        self.assertIn("retry_at", columns)
        migrated = store.get_job("old-job")
        self.assertEqual(migrated["state"], "queued")
        self.assertEqual(migrated["attempt"], 2)
        self.assertIsNone(migrated["retry_at"])

    def test_store_persists_across_instances(self):
        job = self.enqueue()
        reopened = jobs.JobStore(self.db).get_job(job["job_id"])
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened["job_id"], job["job_id"])
        self.assertEqual(reopened["state"], "queued")


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

    def test_enqueue_explicit_job_id_rejects_a_different_identity(self):
        self.enqueue(job_id="manual-job-1")
        with self.assertRaises(ValueError):
            self.store.enqueue(
                "reviewer",
                115,
                43,
                base_sha="other-base",
                head_sha="head",
                prompt_hash="other-prompt",
                job_id="manual-job-1",
                now=200.0,
            )
        stored = self.store.get_job("manual-job-1")
        self.assertEqual(stored["role"], "implementer")
        self.assertEqual(stored["ticket"], 114)
        self.assertEqual(stored["pr"], 42)


class ClaimTests(JobStoreTestCase):
    def test_claim_leases_oldest_queued_job_and_reports_none_when_empty(self):
        first = self.enqueue(ticket=1, now=100.0)
        second = self.enqueue(ticket=2, now=200.0)
        claimed = self.store.claim("runner-a", 30, now=300.0)
        self.assertEqual(claimed["job_id"], first["job_id"])
        self.assertEqual(claimed["state"], "leased")
        self.assertEqual(claimed["attempt"], 1)
        self.assertEqual(claimed["lease_owner"], "runner-a")
        self.assertEqual(claimed["lease_until"], 330.0)
        self.assertEqual(claimed["heartbeat_at"], 300.0)
        self.assertEqual(claimed["updated_at"], 300.0)
        next_claimed = self.store.claim("runner-a", 30, now=300.0)
        self.assertEqual(next_claimed["job_id"], second["job_id"])
        self.assertIsNone(self.store.claim("runner-a", 30, now=300.0))

    def test_claim_does_not_steal_an_active_lease(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertIsNone(self.store.claim("runner-b", 30, now=129.9))
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["lease_owner"], "runner-a")
        self.assertEqual(stored["attempt"], 1)

    def test_claim_reclaims_an_expired_lease_without_waiting_for_reaper(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        claimed = self.store.claim("runner-b", 30, now=131.0)
        self.assertEqual(claimed["job_id"], job["job_id"])
        self.assertEqual(claimed["lease_owner"], "runner-b")
        self.assertEqual(claimed["attempt"], 2)
        self.assertEqual(claimed["lease_until"], 161.0)

    def test_concurrent_claims_have_exactly_one_winner(self):
        job = self.enqueue()
        worker_count = 8
        barrier = threading.Barrier(worker_count)
        winners = []
        errors = []
        answers_lock = threading.Lock()

        def worker(index):
            barrier.wait()
            try:
                claimed = self.store.claim(f"runner-{index}", 30, now=100.0)
            except BaseException as error:  # noqa: BLE001 - surfaced by assertion
                with answers_lock:
                    errors.append(error)
                return
            if claimed is not None:
                with answers_lock:
                    winners.append(claimed)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["job_id"], job["job_id"])
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["attempt"], 1)

    def test_claim_rejects_invalid_owner_or_ttl(self):
        self.enqueue()
        with self.assertRaises(ValueError):
            self.store.claim("", 30, now=100.0)
        with self.assertRaises(ValueError):
            self.store.claim("runner", 0, now=100.0)
        with self.assertRaises(ValueError):
            self.store.claim("runner", -1, now=100.0)


class HeartbeatTests(JobStoreTestCase):
    def test_heartbeat_extends_lease_for_the_owner(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertTrue(self.store.heartbeat(job["job_id"], "runner-a", 60, now=110.0))
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["lease_until"], 170.0)
        self.assertEqual(stored["heartbeat_at"], 110.0)
        self.assertEqual(stored["updated_at"], 110.0)
        self.assertEqual(stored["state"], "leased")

    def test_heartbeat_from_wrong_owner_or_after_finish_is_rejected(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertFalse(self.store.heartbeat(job["job_id"], "runner-b", 60, now=110.0))
        self.assertEqual(self.store.get_job(job["job_id"])["lease_until"], 130.0)
        self.assertTrue(
            self.store.finish(job["job_id"], "done", owner="runner-a", now=120.0)
        )
        self.assertFalse(self.store.heartbeat(job["job_id"], "runner-a", 60, now=130.0))
        self.assertEqual(self.store.get_job(job["job_id"])["lease_until"], None)

    def test_heartbeat_on_queued_or_missing_job_returns_false(self):
        job = self.enqueue()
        self.assertFalse(self.store.heartbeat(job["job_id"], "runner-a", 30, now=100.0))
        self.assertFalse(self.store.heartbeat("missing", "runner-a", 30, now=100.0))


class FinishTests(JobStoreTestCase):
    def test_finish_done_clears_lease_and_sets_terminal_state(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertTrue(
            self.store.finish(job["job_id"], "done", owner="runner-a", now=120.0)
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "done")
        self.assertEqual(stored["error"], "")
        self.assertIsNone(stored["lease_owner"])
        self.assertIsNone(stored["lease_until"])
        self.assertIsNone(stored["heartbeat_at"])
        self.assertEqual(stored["updated_at"], 120.0)
        self.assertEqual(stored["attempt"], 1)
        self.assertFalse(
            self.store.finish(job["job_id"], "done", owner="runner-a", now=130.0)
        )

    def test_finish_failed_records_error_and_releases_job(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertTrue(
            self.store.finish(
                job["job_id"], "failed", "agent exited 1", owner="runner-a", now=120.0
            )
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["error"], "agent exited 1")
        self.assertIsNone(stored["lease_until"])
        self.assertIsNone(self.store.claim("runner-b", 30, now=200.0))

    def test_finish_by_stale_owner_cannot_override_the_new_lease(self):
        job = self.enqueue()
        self.store.claim("runner-a", 1, now=100.0)
        second = self.store.claim("runner-b", 30, now=102.0)
        self.assertEqual(second["attempt"], 2)

        self.assertFalse(
            self.store.finish(job["job_id"], "done", owner="runner-a", now=103.0)
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["lease_owner"], "runner-b")
        self.assertEqual(stored["lease_until"], 132.0)
        self.assertEqual(stored["attempt"], 2)

        self.assertTrue(
            self.store.finish(job["job_id"], "done", owner="runner-b", now=104.0)
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "done")
        self.assertIsNone(stored["lease_owner"])

    def test_finish_with_attempt_fences_old_attempt_from_the_same_owner(self):
        job = self.enqueue()
        self.store.claim("runner-a", 1, now=100.0)
        self.store.claim("runner-a", 30, now=102.0)

        self.assertFalse(
            self.store.finish(
                job["job_id"], "done", owner="runner-a", attempt=1, now=103.0
            )
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["attempt"], 2)
        self.assertEqual(stored["lease_until"], 132.0)

        self.assertTrue(
            self.store.finish(
                job["job_id"], "done", owner="runner-a", attempt=2, now=104.0
            )
        )
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "done")

    def test_finish_queued_job_without_owner_is_explicit_cancel_path(self):
        job = self.enqueue()
        self.assertTrue(
            self.store.finish(job["job_id"], "failed", "cancelled", now=110.0)
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["error"], "cancelled")

    def test_finish_leased_job_without_owner_is_rejected(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertFalse(self.store.finish(job["job_id"], "done", now=110.0))
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["lease_owner"], "runner-a")
        self.assertEqual(stored["lease_until"], 130.0)

    def test_finish_rejects_non_terminal_state(self):
        job = self.enqueue()
        with self.assertRaises(ValueError):
            self.store.finish(job["job_id"], "running", now=100.0)
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "queued")


class RequeueTests(JobStoreTestCase):
    def test_requeue_leased_job_clears_lease_and_keeps_it_claimable(self):
        job = self.enqueue()
        claimed = self.store.claim("runner-a", 30, now=100.0)
        self.assertEqual(claimed["attempt"], 1)
        self.assertTrue(
            self.store.requeue(job["job_id"], "runner-a", attempt=1, now=120.0)
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempt"], 2)
        self.assertIsNone(stored["retry_at"])
        self.assertIsNone(stored["lease_owner"])
        self.assertIsNone(stored["lease_until"])
        self.assertIsNone(stored["heartbeat_at"])
        self.assertEqual(stored["error"], "")
        self.assertEqual(stored["updated_at"], 120.0)

        reclaimed = self.store.claim("runner-b", 30, now=130.0)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["attempt"], 3)
        self.assertIsNone(reclaimed["retry_at"])

    def test_requeue_delay_sets_retry_at_and_claim_waits_for_it(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertTrue(
            self.store.requeue(
                job["job_id"], "runner-a", attempt=1, delay=60, now=120.0
            )
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempt"], 2)
        self.assertEqual(stored["retry_at"], 180.0)
        self.assertIsNone(self.store.claim("runner-b", 30, now=179.0))

        reclaimed = self.store.claim("runner-b", 30, now=180.0)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["attempt"], 3)
        self.assertEqual(reclaimed["state"], "leased")
        self.assertIsNone(reclaimed["retry_at"])

    def test_requeue_accepts_an_absolute_retry_at(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertTrue(
            self.store.requeue(
                job["job_id"], "runner-a", attempt=1, retry_at=500, now=120.0
            )
        )
        self.assertEqual(self.store.get_job(job["job_id"])["retry_at"], 500.0)
        self.assertIsNone(self.store.claim("runner-b", 30, now=499.0))
        self.assertIsNotNone(self.store.claim("runner-b", 30, now=500.0))

    def test_requeue_rejects_ambiguous_or_negative_backoff(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        for kwargs in (
            {"retry_at": 200, "delay": 60},
            {"delay": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.store.requeue(
                        job["job_id"], "runner-a", attempt=1, now=110.0, **kwargs
                    )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["attempt"], 1)

    def test_requeue_is_fenced_by_owner_and_attempt(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        self.assertFalse(self.store.requeue(job["job_id"], "runner-b", now=110.0))
        self.assertFalse(
            self.store.requeue(job["job_id"], "runner-a", attempt=2, now=110.0)
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["lease_owner"], "runner-a")
        self.assertTrue(
            self.store.requeue(job["job_id"], "runner-a", attempt=1, now=111.0)
        )

    def test_requeue_rejects_non_active_or_missing_jobs(self):
        done = self.enqueue(ticket=1)
        self.store.claim("runner-a", 30, now=100.0)
        self.store.finish(done["job_id"], "done", owner="runner-a", now=110.0)
        queued = self.enqueue(ticket=2, now=200.0)
        self.assertFalse(self.store.requeue(queued["job_id"], "runner-a", now=210.0))
        self.assertFalse(self.store.requeue(done["job_id"], "runner-a", now=220.0))
        self.assertFalse(self.store.requeue("missing", "runner-a", now=220.0))
        self.assertEqual(self.store.get_job(done["job_id"])["state"], "done")

    def test_requeue_validates_owner_and_attempt(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        with self.assertRaises(ValueError):
            self.store.requeue(job["job_id"], "", now=110.0)
        with self.assertRaises(ValueError):
            self.store.requeue(job["job_id"], "runner-a", attempt=-1, now=110.0)


class RetryTests(JobStoreTestCase):
    def _failed(self, ticket=1):
        job = self.enqueue(ticket=ticket)
        self.store.claim("runner-a", 30, now=100.0)
        self.assertTrue(
            self.store.finish(job["job_id"], "failed", "boom", owner="runner-a", now=110.0)
        )
        return job

    def test_retry_resets_failed_job_and_preserves_attempt_history(self):
        job = self._failed()
        self.assertTrue(self.store.retry(job["job_id"], now=120.0))
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["error"], "")
        self.assertIsNone(stored["retry_at"])
        self.assertIsNone(stored["lease_owner"])
        self.assertIsNone(stored["lease_until"])
        self.assertIsNone(stored["heartbeat_at"])
        self.assertEqual(stored["attempt"], 2)
        self.assertEqual(stored["updated_at"], 120.0)

        reclaimed = self.store.claim("runner-b", 30, now=130.0)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["attempt"], 3)

    def test_retry_resets_stale_job_and_clears_pending_retry_at(self):
        job = self.enqueue()
        self.store.claim("runner-a", 10, now=100.0)
        self.store.reap_stale(now=111.0)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "UPDATE jobs SET retry_at = 999 WHERE job_id = ?", (job["job_id"],)
            )
            connection.commit()
        finally:
            connection.close()

        self.assertTrue(self.store.retry(job["job_id"], now=120.0))
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertIsNone(stored["retry_at"])
        self.assertEqual(stored["attempt"], 2)
        self.assertIsNotNone(self.store.claim("runner-b", 30, now=120.0))

    def test_retry_rejects_queued_active_done_and_missing_jobs(self):
        active = self.enqueue(ticket=2, now=50.0)
        self.store.claim("runner-a", 30, now=100.0)
        done = self.enqueue(ticket=3, now=60.0)
        self.store.claim("runner-a", 30, now=110.0)
        self.store.finish(done["job_id"], "done", owner="runner-a", now=120.0)
        queued = self.enqueue(ticket=1, now=200.0)

        for job in (queued, active, done):
            with self.subTest(ticket=job["ticket"]):
                self.assertFalse(self.store.retry(job["job_id"], now=220.0))
        self.assertFalse(self.store.retry("missing", now=220.0))
        self.assertEqual(self.store.get_job(queued["job_id"])["state"], "queued")
        self.assertEqual(self.store.get_job(active["job_id"])["state"], "leased")
        self.assertEqual(self.store.get_job(done["job_id"])["state"], "done")


class ReapStaleTests(JobStoreTestCase):
    def test_reap_stale_marks_only_expired_active_leases(self):
        expired = self.enqueue(ticket=1, now=100.0)
        queued = self.enqueue(ticket=2, now=101.0)
        self.store.claim("runner-a", 10, now=100.0)
        self.assertEqual(self.store.reap_stale(now=109.0), [])

        reaped = self.store.reap_stale(now=111.0)
        self.assertEqual([row["job_id"] for row in reaped], [expired["job_id"]])
        self.assertEqual(reaped[0]["state"], "stale")
        self.assertIsNone(reaped[0]["lease_owner"])
        self.assertIsNone(reaped[0]["lease_until"])
        self.assertEqual(reaped[0]["error"], "lease expired")
        self.assertEqual(self.store.get_job(queued["job_id"])["state"], "queued")
        self.assertEqual(self.store.reap_stale(now=112.0), [])

    def test_reap_stale_ignores_finished_and_queued_jobs(self):
        done = self.enqueue(ticket=1, now=100.0)
        self.store.claim("runner-a", 10, now=100.0)
        self.store.finish(done["job_id"], "done", owner="runner-a", now=101.0)
        self.enqueue(ticket=2, now=102.0)
        self.assertEqual(self.store.reap_stale(now=10_000.0), [])
        self.assertEqual(self.store.get_job(done["job_id"])["state"], "done")

    def test_stale_job_can_be_claimed_again_with_a_new_attempt(self):
        job = self.enqueue()
        self.store.claim("runner-a", 10, now=100.0)
        self.store.reap_stale(now=111.0)
        reclaimed = self.store.claim("runner-b", 30, now=120.0)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["state"], "leased")
        self.assertEqual(reclaimed["lease_owner"], "runner-b")
        self.assertEqual(reclaimed["attempt"], 2)
        self.assertEqual(reclaimed["lease_until"], 150.0)


class ListingTests(JobStoreTestCase):
    def test_list_jobs_filters_state_orders_newest_first_and_honors_limit(self):
        oldest = self.enqueue(ticket=1, now=100.0)
        middle = self.enqueue(ticket=2, now=200.0)
        newest = self.enqueue(ticket=3, now=300.0)
        self.store.claim("runner-a", 30, now=400.0)
        self.store.finish(oldest["job_id"], "done", owner="runner-a", now=500.0)

        all_jobs = self.store.list_jobs()
        self.assertEqual(
            [job["job_id"] for job in all_jobs],
            [newest["job_id"], middle["job_id"], oldest["job_id"]],
        )
        queued = self.store.list_jobs(state="queued")
        self.assertEqual([job["job_id"] for job in queued], [newest["job_id"], middle["job_id"]])
        limited = self.store.list_jobs(limit=2)
        self.assertEqual([job["job_id"] for job in limited], [newest["job_id"], middle["job_id"]])
        self.assertEqual(self.store.list_jobs(limit=0), [])

    def test_list_jobs_rejects_unknown_state_and_negative_limit(self):
        with self.assertRaises(ValueError):
            self.store.list_jobs(state="unknown")
        with self.assertRaises(ValueError):
            self.store.list_jobs(limit=-1)

    def test_get_job_returns_none_for_missing_id(self):
        self.assertIsNone(self.store.get_job("missing"))


class JobsCliTests(JobStoreTestCase):
    def _seed(self):
        store = jobs.JobStore(self.db)
        older = store.enqueue(
            "implementer", 114, 42, "base000", "head111", PROMPT_HASH, now=1000.0
        )
        newer = store.enqueue(
            "reviewer", 115, 43, "base222", "head333", "prompt-two", now=2000.0
        )
        store.claim("runner-a", 60, now=2000.0)
        return older, newer

    def _output(self, function, args):
        output = io.StringIO()
        with mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db):
            with contextlib.redirect_stdout(output):
                code = function(args)
        return code, output.getvalue()

    def test_jobs_list_prints_jobs_and_state_filter(self):
        older, newer = self._seed()
        code, text = self._output(
            dispatcher.cmd_jobs_list, argparse.Namespace(state=None, limit=50)
        )
        self.assertEqual(code, 0)
        self.assertIn("job_id", text)
        self.assertIn(older["job_id"], text)
        self.assertIn(newer["job_id"], text)
        self.assertIn("leased", text)
        self.assertIn("queued", text)
        self.assertIn("1970-01-01T00:33:20Z", text)

        code, text = self._output(
            dispatcher.cmd_jobs_list, argparse.Namespace(state="queued", limit=50)
        )
        self.assertEqual(code, 0)
        self.assertIn(newer["job_id"], text)
        self.assertNotIn(older["job_id"], text)

    def test_jobs_show_prints_all_fields_and_missing_job_errors(self):
        older, _ = self._seed()
        code, text = self._output(
            dispatcher.cmd_jobs_show, argparse.Namespace(job_id=older["job_id"])
        )
        self.assertEqual(code, 0)
        self.assertIn(f"job_id       {older['job_id']}", text)
        self.assertIn("role         implementer", text)
        self.assertIn("ticket       114", text)
        self.assertIn("state        leased", text)
        self.assertIn("attempt      1", text)
        self.assertIn("lease_owner  runner-a", text)
        self.assertIn("prompt_hash  " + PROMPT_HASH, text)
        self.assertIn("1970-01-01T00:33:20Z", text)

        code, text = self._output(
            dispatcher.cmd_jobs_show, argparse.Namespace(job_id="missing")
        )
        self.assertEqual(code, 1)
        self.assertIn("未找到 job", text)

    def test_jobs_list_empty_store_reports_no_records(self):
        code, text = self._output(
            dispatcher.cmd_jobs_list, argparse.Namespace(state=None, limit=50)
        )
        self.assertEqual(code, 0)
        self.assertIn("暂无记录", text)

    def test_jobs_retry_resets_failed_job(self):
        store = jobs.JobStore(self.db)
        job = store.enqueue(
            "implementer", 116, 44, "base000", "head111", PROMPT_HASH, now=1000.0
        )
        store.claim("runner-a", 60, now=1000.0)
        store.finish(job["job_id"], "failed", "agent failed", owner="runner-a", now=1100.0)

        code, text = self._output(
            dispatcher.cmd_jobs_retry, argparse.Namespace(job_id=job["job_id"])
        )
        self.assertEqual(code, 0)
        self.assertIn("-> queued", text)
        stored = store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempt"], 2)
        self.assertEqual(stored["error"], "")
        self.assertIsNone(stored["retry_at"])

    def test_jobs_retry_rejects_non_retryable_and_missing_jobs(self):
        _, newer = self._seed()
        code, text = self._output(
            dispatcher.cmd_jobs_retry, argparse.Namespace(job_id=newer["job_id"])
        )
        self.assertEqual(code, 1)
        self.assertIn("仅 failed/stale 可 retry", text)
        self.assertEqual(
            jobs.JobStore(self.db).get_job(newer["job_id"])["state"], "queued"
        )

        code, text = self._output(
            dispatcher.cmd_jobs_retry, argparse.Namespace(job_id="missing")
        )
        self.assertEqual(code, 1)
        self.assertIn("未找到 job", text)

    def test_main_dispatches_jobs_retry_from_argv(self):
        store = jobs.JobStore(self.db)
        job = store.enqueue(
            "implementer", 117, 45, "base000", "head111", PROMPT_HASH, now=1000.0
        )
        store.claim("runner-a", 60, now=1000.0)
        store.finish(job["job_id"], "failed", "boom", owner="runner-a", now=1100.0)

        output = io.StringIO()
        with (
            mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db),
            mock.patch.object(
                sys, "argv", ["dispatcher.py", "jobs", "retry", job["job_id"]]
            ),
            contextlib.redirect_stdout(output),
        ):
            code = dispatcher.main()
        self.assertEqual(code, 0)
        self.assertIn("-> queued", output.getvalue())
        self.assertEqual(
            jobs.JobStore(self.db).get_job(job["job_id"])["state"], "queued"
        )

    def test_main_dispatches_jobs_list_from_argv(self):
        _, newer = self._seed()
        output = io.StringIO()
        with (
            mock.patch.object(dispatcher, "JOBS_DB_PATH", self.db),
            mock.patch.object(sys, "argv", ["dispatcher.py", "jobs", "list", "--state", "queued"]),
            contextlib.redirect_stdout(output),
        ):
            code = dispatcher.main()
        self.assertEqual(code, 0)
        self.assertIn(newer["job_id"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
