"""The workflow-shell front end to retry.py: what a step sees, and the retry
budget its calls share. The busy line is `gh api`'s."""

from __future__ import annotations

import contextlib
import fcntl
import io
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from gh_transient.cli import main, spend

BUSY = subprocess.CompletedProcess(["gh"], 1, "", "gh: Server Error (HTTP 502)\n")
DONE = subprocess.CompletedProcess(["gh"], 0, "ok\n", "")


class TestGhRetryCli(unittest.TestCase):
    def run_cli(
        self,
        argv: list[str],
        results: list[subprocess.CompletedProcess[str]],
    ) -> tuple[int, str, str, mock.MagicMock]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("subprocess.run", side_effect=results) as run,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = main(argv, sleep=lambda delay: None)
        return code, out.getvalue(), err.getvalue(), run

    def test_a_refusal_is_an_error_and_a_failed_exit_at_once(self) -> None:
        refused = subprocess.CompletedProcess(
            ["gh"], 1, "", "gh: Not Found (HTTP 404)\n"
        )
        code, out, err, run = self.run_cli(["read", "--", "api", "x"], [refused])
        self.assertEqual((code, out, run.call_count), (1, "", 1))
        self.assertIn("::error::", err)

    def test_calls_of_one_step_share_its_retry_budget(self) -> None:
        # A scan's reads each run their own process; drawing on one file is what
        # keeps a scan of several records from spending a ladder per record.
        with tempfile.TemporaryDirectory() as tmp:
            budget = Path(tmp) / "retries"
            budget.write_text("1\n")
            argv = ["read", "--budget-file", str(budget), "--", "api", "x"]
            first = self.run_cli(argv, [BUSY, DONE])
            second = self.run_cli(argv, [BUSY])
            self.assertEqual((first[0], first[3].call_count), (0, 2))
            self.assertIn("::warning::", first[2])
            self.assertEqual((second[0], second[3].call_count), (1, 1))
            self.assertEqual(budget.read_text().strip(), "0")

    def test_a_call_in_flight_waits_for_the_budget_another_holds(self) -> None:
        # Calls drawing on one file at once would otherwise both read the last
        # retry and both take it. The other call is let go only once it has
        # reached the lock this test holds, so it can read the count only after
        # the change below - and a spend that took no lock never signals.
        real_flock = fcntl.flock
        reached_lock = threading.Event()

        def flock(fd: object, operation: int) -> None:
            reached_lock.set()
            real_flock(fd, operation)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as tmp:
            budget = Path(tmp) / "retries"
            budget.write_text("1\n")
            taken: list[bool] = []
            with budget.open("r+") as held:
                real_flock(held, fcntl.LOCK_EX)
                with mock.patch.object(fcntl, "flock", flock):
                    other = threading.Thread(target=lambda: taken.append(spend(budget)))
                    other.start()
                    self.assertTrue(reached_lock.wait(timeout=10))
                    held.seek(0)
                    held.write("0\n")
                    held.truncate()
                    held.flush()
                    real_flock(held, fcntl.LOCK_UN)
                    other.join(timeout=10)
            self.assertFalse(other.is_alive())
            self.assertEqual(taken, [False])
            self.assertEqual(budget.read_text().strip(), "0")


if __name__ == "__main__":
    unittest.main()
