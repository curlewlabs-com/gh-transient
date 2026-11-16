"""Tell a `gh` call GitHub failed to serve from one it answered, and re-issue a
read that was the first kind. See the README.

The package root re-exports the public names, so a caller imports from
`gh_transient` and the module layout below it stays free to change.
"""

import sys

# Checked here, before any other module of the package is imported, so an older
# interpreter gets this message rather than a syntax error from a newer form.
if sys.version_info < (3, 12):  # noqa: UP036 - the point is to run on older ones
    sys.exit("gh-transient needs Python 3.12 or newer")

from gh_transient.classify import http_failure, is_transient
from gh_transient.retry import (
    GH_TIMEOUT_SECONDS,
    RETRY_DELAYS_SECONDS,
    GhError,
    is_transient_gh_error,
    read_gh,
    retry_transient,
    run_gh,
    say_retry,
)

__all__ = [
    "GH_TIMEOUT_SECONDS",
    "RETRY_DELAYS_SECONDS",
    "GhError",
    "http_failure",
    "is_transient",
    "is_transient_gh_error",
    "read_gh",
    "retry_transient",
    "run_gh",
    "say_retry",
]
