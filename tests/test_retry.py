"""The ladder a `gh` read walks when GitHub fails to serve it.

The stderr lines are live `gh` failure shapes; which of them count as busy is
classify.py's, tested in test_classify.py.
"""

from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from unittest import mock

from gh_transient import RETRY_DELAYS_SECONDS, GhError, read_gh
from gh_transient.retry import SECONDARY_RATE_LIMIT_WAIT_SECONDS

READ = ["api", "repos/o/n/actions/runs/1"]


def answers(*stderrs: str) -> list[subprocess.CompletedProcess[str]]:
    """One `gh` result per stderr, failing for each non-empty one."""
    return [
        subprocess.CompletedProcess(["gh"], 1 if stderr else 0, "{}", stderr)
        for stderr in stderrs
    ]


class TestReadGh(unittest.TestCase):
    """The ladder a read walks. Re-issuing the wrong failure costs a wait before
    an abort that was coming anyway; missing a blip costs the caller's whole run,
    a deploy or a daily report."""

    def read(
        self, results: Sequence[subprocess.CompletedProcess[str] | Exception]
    ) -> list[float]:
        slept: list[float] = []
        with mock.patch("subprocess.run", side_effect=results):
            read_gh(READ, sleep=slept.append, on_retry=lambda delay, error: None)
        return slept

    def test_a_bad_gateway_is_read_again(self) -> None:
        self.assertEqual(
            self.read(answers("gh: Server Error (HTTP 502)\n", "")),
            [RETRY_DELAYS_SECONDS[0]],
        )

    def test_a_stalled_read_is_read_again(self) -> None:
        # A stalled socket carries no stderr to classify, so only run_gh's
        # timeout branch marks it worth another attempt.
        stalled = subprocess.TimeoutExpired(["gh"], 120)
        self.assertEqual(self.read([stalled, *answers("")]), [RETRY_DELAYS_SECONDS[0]])

    def test_a_secondary_rate_limit_waits_the_minute_github_asks(self) -> None:
        # GitHub asks for at least a minute before retrying a secondary limit;
        # the 5s and 20s rungs would spend attempts it cannot clear in, and a
        # ladder of its own would lengthen every caller's sized worst case.
        limited = (
            "gh: You have exceeded a secondary rate limit. Please wait a few"
            " minutes before you try again. (HTTP 403)\n"
        )
        self.assertEqual(
            self.read(answers(limited, "")), [SECONDARY_RATE_LIMIT_WAIT_SECONDS]
        )
        self.assertEqual(
            self.read(answers("gh: Server Error (HTTP 502)\n", limited, "")),
            [RETRY_DELAYS_SECONDS[0], SECONDARY_RATE_LIMIT_WAIT_SECONDS],
        )

    def test_an_answer_is_reported_at_once(self) -> None:
        slept: list[float] = []
        with (
            mock.patch(
                "subprocess.run", side_effect=answers("gh: Not Found (HTTP 404)")
            ),
            self.assertRaisesRegex(GhError, "HTTP 404"),
        ):
            read_gh(READ, sleep=slept.append)
        self.assertEqual(slept, [])

    def test_a_read_github_keeps_failing_fails_after_the_last_wait(self) -> None:
        # Bounded, so the caller still fails with GitHub's message inside its
        # job's timeout rather than retrying until the job is cancelled.
        slept: list[float] = []
        failures = answers(*["gh: HTTP 500"] * (len(RETRY_DELAYS_SECONDS) + 1))
        with (
            mock.patch("subprocess.run", side_effect=failures),
            self.assertRaisesRegex(GhError, "HTTP 500"),
        ):
            read_gh(READ, sleep=slept.append, on_retry=lambda delay, error: None)
        self.assertEqual(slept, list(RETRY_DELAYS_SECONDS))


class TestSharedDeadline(unittest.TestCase):
    """A deadline several reads share: the render or loop above them has one
    budget, and a ladder apiece would spend several."""

    def test_no_wait_starts_past_the_deadline(self) -> None:
        slept: list[float] = []
        with (
            mock.patch("subprocess.run", side_effect=answers("gh: HTTP 502")),
            self.assertRaisesRegex(GhError, "HTTP 502"),
        ):
            read_gh(
                READ,
                sleep=slept.append,
                on_retry=lambda delay, error: None,
                deadline=103.0,
                clock=lambda: 100.0,
            )
        self.assertEqual(slept, [])

    def test_an_attempt_is_cut_off_by_the_time_left(self) -> None:
        with mock.patch("subprocess.run", side_effect=answers("")) as run:
            read_gh(READ, deadline=110.0, clock=lambda: 100.0)
        self.assertEqual(run.call_args.kwargs["timeout"], 10.0)

    def test_a_spent_deadline_runs_nothing(self) -> None:
        with (
            mock.patch("subprocess.run") as run,
            self.assertRaises(GhError) as raised,
        ):
            read_gh(READ, deadline=100.0, clock=lambda: 100.0)
        self.assertTrue(raised.exception.transient)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
