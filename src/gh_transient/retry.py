"""The bounded re-issue of a `gh` read that GitHub failed to serve.

READS ONLY. read_gh re-issues a call, which is safe for a read because a read
has no side effect: the second attempt asks again and may see newer state, but
the first changed nothing. A create, comment or dispatch that failed may have
landed anyway, so a mutation goes through run_gh, once, and its caller decides
whether repeating it is harmless. Which failures count as GitHub not serving
the call is classify.py's.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, overload

from gh_transient.classify import is_transient, names_secondary_rate_limit

# Bound on one `gh` attempt. A call against a stalled socket would otherwise
# hold its job to the job's own timeout, which kills the run rather than
# reporting the read that hung. Short enough that one whole ladder of these
# fits a scheduled job's minutes-long timeout on top of its work, long enough
# for a paginated read that is answering; a caller under a tighter budget
# passes its own.
GH_TIMEOUT_SECONDS = 60

# How long a read waits between attempts, and - one attempt per delay, plus the
# last one with nothing left to wait for - how many it gets. Bounded because
# every caller runs under a runtime budget that kills it rather than reporting
# the read - a workflow job's timeout-minutes, or a cron wrapper's runtime
# guard - and that budget has to hold one full ladder plus an attempt's timeout
# apiece, so a caller sizes its budget from these.
#
# The first two ride out a gateway blip. The third is 60s because GitHub's REST
# best practices say to "wait for at least one minute before retrying" a
# secondary rate limit that names no reset time
# (https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api,
# "Handle rate limit errors appropriately"), so a shorter one would spend the
# attempt with no chance of clearing it - which is also why this is a
# written-out ladder rather than a pure exponential.
#
# A secondary rate limit skips the rungs shorter than that minute rather than
# taking a ladder of its own: it gets fewer attempts, never a longer worst
# case, so every caller's runtime budget, sized to this ladder, still holds.
RETRY_DELAYS_SECONDS = (5, 20, 60)
SECONDARY_RATE_LIMIT_WAIT_SECONDS = 60


class GhError(RuntimeError):
    """A `gh` call failed. `transient` says GitHub failed to serve it rather
    than answered it, the one failure read_gh re-issues; it is set where the
    evidence exists, the failed call's stderr or its timeout."""

    def __init__(self, message: str, *, stderr: str = "", transient: bool = False):
        super().__init__(message)
        self.stderr = stderr
        self.transient = transient


@overload
def _run_once(
    args: Sequence[str], *, timeout: float, cwd: Path | None, text: Literal[True]
) -> str: ...
@overload
def _run_once(
    args: Sequence[str], *, timeout: float, cwd: Path | None, text: Literal[False]
) -> bytes: ...
def _run_once(
    args: Sequence[str], *, timeout: float, cwd: Path | None, text: bool
) -> str | bytes:
    command = f"gh {' '.join(args)}"
    try:
        completed = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=text,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except OSError as error:
        raise GhError(f"{command}: {error}") from error
    except subprocess.TimeoutExpired as error:
        # A stalled socket is GitHub not answering, the same news as a 5xx.
        raise GhError(
            f"{command} timed out after {timeout}s", transient=True
        ) from error
    # Replaced rather than strict, so a stray byte in gh's message cannot turn
    # the failure it reports into a UnicodeDecodeError that hides it.
    stderr: str = (
        completed.stderr if text else completed.stderr.decode(errors="replace")
    )
    if completed.returncode != 0:
        raise GhError(
            f"{command} exited {completed.returncode}: {stderr.strip()}",
            stderr=stderr,
            transient=is_transient(stderr),
        )
    stdout: str | bytes = completed.stdout
    return stdout


def run_gh(
    args: Sequence[str],
    *,
    timeout: float = GH_TIMEOUT_SECONDS,
    cwd: Path | None = None,
) -> str:
    """Run `gh` ONCE and return its stdout, raising GhError on any failure.

    The call a mutation makes. gh's stderr is captured to classify it and
    carried in the error's message, which is then the only record of why the
    call failed.
    """
    return _run_once(args, timeout=timeout, cwd=cwd, text=True)


def run_gh_bytes(
    args: Sequence[str],
    *,
    timeout: float = GH_TIMEOUT_SECONDS,
    cwd: Path | None = None,
) -> bytes:
    """run_gh, with gh's stdout returned as the bytes gh wrote.

    For output that is not text, such as a run's log archive, which decoding
    would corrupt. Its failures are run_gh's.
    """
    return _run_once(args, timeout=timeout, cwd=cwd, text=False)


def say_retry(delay: float, error: Exception) -> None:
    print(f"retrying in {delay}s: {error}", file=sys.stderr)


def retry_transient[T](
    attempt: Callable[[], T],
    transient: Callable[[Exception], bool],
    *,
    on_retry: Callable[[float, Exception], None] = say_retry,
    sleep: Callable[[float], None] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    may_retry: Callable[[], bool] | None = None,
) -> T:
    """`attempt()`, re-issued on RETRY_DELAYS_SECONDS while `transient` says its
    failure was GitHub not answering.

    `deadline`, a `clock` instant, is a budget several reads share: no wait
    starts that would end past it, so the failure it would have retried
    propagates instead. `may_retry`, asked before each wait, is a count of
    retries several reads share, even across processes: False lets the
    failure propagate too.

    For a caller that keeps its own error type around its own `gh` runner; it
    names which of its errors are transient. One attempt per delay plus a final
    one with nothing left to wait for, so the last failure propagates from the
    same place a first-attempt one does. A non-transient failure never sleeps:
    waiting on an answer only delays the failure it has already earned.

    A failure whose `stderr` - GhError's, or a `stderr` string on the caller's
    own error type - names the secondary rate limit skips the delays shorter
    than SECONDARY_RATE_LIMIT_WAIT_SECONDS. Only gh's stderr is read for it,
    never the error's whole message, which carries the command's arguments.
    """
    delays = list(RETRY_DELAYS_SECONDS)
    while delays:
        try:
            return attempt()
        except Exception as error:
            if not transient(error):
                raise
            stderr = getattr(error, "stderr", None)
            if isinstance(stderr, str) and names_secondary_rate_limit(stderr):
                delays = [
                    delay
                    for delay in delays
                    if delay >= SECONDARY_RATE_LIMIT_WAIT_SECONDS
                ]
                if not delays:
                    raise
            delay = delays.pop(0)
            if deadline is not None and clock() + delay >= deadline:
                raise
            if may_retry is not None and not may_retry():
                raise
            on_retry(delay, error)
            # Looked up per call rather than bound as the default, so a test that
            # patches time.sleep reaches every caller's ladder.
            (sleep or time.sleep)(delay)
    return attempt()


def is_transient_gh_error(error: Exception) -> bool:
    return isinstance(error, GhError) and error.transient


def read_gh(
    args: Sequence[str],
    *,
    timeout: float = GH_TIMEOUT_SECONDS,
    cwd: Path | None = None,
    on_retry: Callable[[float, Exception], None] = say_retry,
    sleep: Callable[[float], None] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    may_retry: Callable[[], bool] | None = None,
) -> str:
    """run_gh for a READ, re-issued while its failure says GitHub was busy.

    With a `deadline`, each attempt is also cut off by the time left before it,
    and one that would start with none left fails as transient without running.
    """

    def attempt() -> str:
        bound = timeout
        if deadline is not None:
            left = deadline - clock()
            if left <= 0:
                raise GhError(
                    f"gh {' '.join(args)}: no time left before its deadline",
                    transient=True,
                )
            bound = min(timeout, left)
        return run_gh(args, timeout=bound, cwd=cwd)

    return retry_transient(
        attempt,
        is_transient_gh_error,
        on_retry=on_retry,
        sleep=sleep,
        deadline=deadline,
        clock=clock,
        may_retry=may_retry,
    )
