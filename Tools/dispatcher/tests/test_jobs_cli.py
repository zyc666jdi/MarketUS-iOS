"""Phase 1c-i tests: ``dispatcher.py jobs list/show`` CLI."""

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

PROMPT_HASH = "sha256:0123456789abcdef"


class JobsCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "jobs.db"
        self.store = jobs.JobStore(self.db)

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

    def test_jobs_list_rejects_negative_limit(self):
        code, text = self._output(
            dispatcher.cmd_jobs_list, argparse.Namespace(state=None, limit=-1)
        )
        self.assertEqual(code, 1)
        self.assertIn("limit 不能为负数", text)

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
