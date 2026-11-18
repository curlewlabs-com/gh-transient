"""Workflow shell's way into retry.py: one `gh` call, re-issued while GitHub
fails to serve it.

    python3 -m gh_transient read [--budget-file PATH] -- <gh arguments>

Reads only, as retry.py's read_gh is: the verb is there so the workflow names
what it asserts about the call, and a write never comes here - one that failed
may have landed anyway.

`--budget-file` names a file holding the retries left to a whole step, which
every call the step makes draws from, so a scan of several records cannot
spend a ladder per record: the step writes the count once, and a call that
finds none left fails at its first failure. Each call has its own process, so
the count lives in the file rather than in memory. The calls drawing on one
file may run at once: each takes its retry under a lock on the file, so two
cannot both take the last one.

A subcommand that rewords gh's errors -
`gh secret set` prefixes its own - is retried only once classify.py carries a
live sample of that wording; until then its failures read as unrecognised.
gh's stdout is written back out as the bytes gh wrote, so a binary read such as
a run's log archive comes through intact; each retry is a warning annotation,
and a call GitHub keeps failing is an error annotation and exit status 1.
"""

from __future__ import annotations

import argparse
import fcntl
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from gh_transient.retry import (
    GhError,
    is_transient_gh_error,
    retry_transient,
    run_gh_bytes,
)

# Per attempt, where retry.py's default is minutes: a workflow job's timeout is
# minutes long and has to hold one call's whole ladder - every attempt and the
# sleeps between them - on top of its work, so that a call GitHub keeps failing
# ends in this command's error rather than a job cancelled at its cap. An
# answered call takes seconds.
ATTEMPT_TIMEOUT_SECONDS = 30


def spend(budget_file: Path) -> bool:
    """Take one retry from the step's budget file; False when it holds none.

    An unreadable count reads as none, so a malformed file stops retrying
    rather than letting every call retry without a bound. The lock is held
    from the read to the write, so calls in flight at once each take a retry
    of their own.
    """
    try:
        with budget_file.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            left = int(handle.read().strip())
            if left <= 0:
                return False
            handle.seek(0)
            handle.write(f"{left - 1}\n")
            handle.truncate()
    except (OSError, ValueError):
        return False
    return True


def main(
    argv: Sequence[str] | None = None, sleep: Callable[[float], None] = time.sleep
) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m gh_transient",
        description="One gh call, re-issued while GitHub fails to serve it.",
    )
    parser.add_argument("verb", choices=("read",))
    parser.add_argument("--budget-file", type=Path)
    # Split at `--` by hand: argparse's REMAINDER would also take
    # `--budget-file` when it follows the verb, and every argument after `--`
    # is gh's.
    given = list(sys.argv[1:] if argv is None else argv)
    if "--" not in given:
        parser.error("gh arguments go after --")
    split = given.index("--")
    args = parser.parse_args(given[:split])
    gh_arguments = given[split + 1 :]
    if not gh_arguments:
        parser.error("no gh arguments after --")
    try:
        out = retry_transient(
            lambda: run_gh_bytes(gh_arguments, timeout=ATTEMPT_TIMEOUT_SECONDS),
            is_transient_gh_error,
            on_retry=lambda delay, error: print(
                f"::warning::GitHub did not serve gh {gh_arguments[0]}; "
                f"retrying in {delay}s: {error}",
                file=sys.stderr,
            ),
            sleep=sleep,
            may_retry=None
            if args.budget_file is None
            else (lambda: spend(args.budget_file)),
        )
    except GhError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    sys.stdout.flush()
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return 0
