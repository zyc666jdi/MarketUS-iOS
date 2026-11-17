"""Phase 1c-i tests: SQLite job store, CAS leases and stale reaping."""

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DISPATCHER_ROOT = ROOT / "Tools/dispatcher"
if str(DISPATCHER_ROOT) not in sys.path:
    sys.path.insert(0, str(DISPATCHER_ROOT))

import jobs  # noqa: E402

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
                "created_at",
                "updated_at",
                "error",
            ],
        )

    def test_store_persists_across_instances(self):
        job = self.enqueue()
        reopened = jobs.JobStore(self.db).get_job(job["job_id"])
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened["job_id"], job["job_id"])
        self.assertEqual(reopened["state"], "queued")


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
        self.assertEqual(stored["updated_at"], 120.0)
        self.assertEqual(stored["attempt"], 1)
        self.assertIsNone(stored["heartbeat_at"])
        self.assertFalse(
            self.store.finish(job["job_id"], "done", owner="runner-a", now=130.0)
        )

    def test_finish_rejects_stale_owner_after_lease_reassigned(self):
        job = self.enqueue()
        self.store.claim("runner-a", 30, now=100.0)
        reassigned = self.store.claim("runner-b", 30, now=131.0)
        self.assertEqual(reassigned["attempt"], 2)

        self.assertFalse(self.store.finish(job["job_id"], "done", now=132.0))
        self.assertFalse(
            self.store.finish(job["job_id"], "done", owner="runner-a", now=132.0)
        )
        self.assertFalse(
            self.store.finish(
                job["job_id"], "done", owner="runner-b", attempt=1, now=132.0
            )
        )
        stored = self.store.get_job(job["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["lease_owner"], "runner-b")
        self.assertEqual(stored["lease_until"], 161.0)
        self.assertEqual(stored["attempt"], 2)

        self.assertTrue(
            self.store.finish(
                job["job_id"], "done", owner="runner-b", attempt=2, now=140.0
            )
        )
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "done")

    def test_finish_without_owner_cancels_only_queued_jobs(self):
        queued = self.enqueue(ticket=1)
        self.assertTrue(
            self.store.finish(queued["job_id"], "failed", "cancelled", now=100.0)
        )
        self.assertEqual(self.store.get_job(queued["job_id"])["state"], "failed")

        leased = self.enqueue(ticket=2)
        self.store.claim("runner-a", 30, now=100.0)
        self.assertFalse(self.store.finish(leased["job_id"], "done", now=101.0))
        stored = self.store.get_job(leased["job_id"])
        self.assertEqual(stored["state"], "leased")
        self.assertEqual(stored["lease_owner"], "runner-a")

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

    def test_finish_rejects_non_terminal_state(self):
        job = self.enqueue()
        with self.assertRaises(ValueError):
            self.store.finish(job["job_id"], "running", now=100.0)
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "queued")


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

if __name__ == "__main__":
    unittest.main()
