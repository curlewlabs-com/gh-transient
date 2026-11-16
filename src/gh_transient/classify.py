"""Whether a failed `gh` call was GitHub not answering rather than answering.

WHY THIS IS ONE PLACE. Every tool that reads GitHub through `gh` meets the same
blips - a 5xx, a reset socket, a GraphQL resolver that gave up - and a tool that
takes one as final aborts a deploy or voids a scheduled report over a call that
would have succeeded seconds later. The shapes below are sampled from live
failures, and a second catalogue would part from this one. What a caller does
with the answer - re-issue a read - is retry.py's.

FAIL CLOSED. An unrecognised failure is NOT transient: it costs what it cost
before any read was retried, a first-attempt failure, and never a guess.
"""

from __future__ import annotations

import re

# `gh`'s error lines for a failed HTTP request, which are the only place a status
# code appears - the response BODY goes to stdout, checked against a live 404,
# so a failure puts one of these on stderr and nothing else.
#
# A form apiece because `gh api` reports differently from every other subcommand:
# the `gh: ` form came back from `gh api` at 401/403/404/422, and the bare `HTTP`
# form from `gh issue list` and `gh label create`. Matching only the first is what
# left issue-list reads with no retry at all.
#
# `gh api` has a second form for an error body that held no message it could
# read, such as a 5xx with an empty or non-JSON body: the status alone, as
# `gh: HTTP 500` (gh 2.101.0's processResponse falls back to `HTTP %d`). That one
# came back live from a `gh api` ref delete.
#
# The GraphQL client under `gh pr comment` reports a non-200 answer in a form of
# its own, `non-200 OK status code: 502 Bad Gateway body: "<html>..."`, sampled
# from a failed `gh pr comment` on 2026-11-06.
#
# Each is anchored at column 0, and the `gh api` ones at their close too, because
# the anchors are the CLI's own text: an API message that happens to name a status
# code cannot reach the classifier through them, and neither can a `jq`
# diagnostic, which carries no prefix at all. The `\b` after a bare status is
# what keeps `HTTP 4011` from reading as a 401.
GH_API_HTTP_ERROR = re.compile(r"^gh: (?P<message>.*) \(HTTP (?P<status>\d{3})\)$")
GH_API_BARE_HTTP_ERROR = re.compile(r"^gh: HTTP (?P<status>\d{3})(?P<message>)$")
GH_HTTP_ERROR = re.compile(r"^HTTP (?P<status>\d{3})\b(?P<message>.*)$")
GH_GRAPHQL_NON_200 = re.compile(
    r"^non-200 OK status code: (?P<status>\d{3})\b(?P<message>.*)$"
)

# The failures that carry no status code. A failed name lookup is the one `gh`
# words itself: its first line, verbatim, and the whole of what makes it
# recognisable.
GH_CONNECT_FAILURE = "error connecting to "

# Any other request `gh` sent and got no answer to - a dial or TLS handshake that
# timed out, a reset connection, a proxy that refused - prints Go's error for it,
# which opens with the method and the quoted URL, e.g. `Get "<url>": dial tcp
# ...: i/o timeout` from a `gh api` ref read. The shape is matched rather than a
# list of causes, which Go words many ways.
#
# `gh run list` puts its own `failed to get runs: ` ahead of that error, sampled
# on 2026-11-05 with a TLS handshake timeout behind it, so a
# `failed to <verb phrase>: ` lead is let through.
GH_NO_ANSWER = re.compile(
    r'^(?:failed to [a-z ]+: )?(?:Get|Head|Post|Put|Patch|Delete) "[^"]*": '
)

# An answer that arrived empty or cut off: Go's JSON decoder ran out of input.
# `gh api --jq` decodes the answer before it reports any status, so this line is
# all `gh` prints. Taken verbatim from a `gh api --jq` ref read.
GH_CUT_OFF_ANSWER = "unexpected end of JSON input"

# GitHub's GraphQL endpoint answering 200 with this error is its resolver giving
# up, in GitHub's words "the result of a timeout, or ... a GitHub bug", rather
# than an answer about the query. The prefix is taken from `gh pr comment` on
# 2026-11-06; what follows it is a timestamp and a request id.
GH_GRAPHQL_GAVE_UP = "GraphQL: Something went wrong while executing your query"

# The 403 that is a busy API rather than a refusal, in GitHub's words. Needed
# only on that status: GitHub answers either rate limit with a 403 or a 429, and
# a 429 is already retried whichever limit produced it - where a 403 is also how
# every ordinary refusal arrives, so on that one the message is all there is to
# tell them apart.
#
# The PRIMARY limit's 403 stays non-retryable, and its 429 is retried for free
# by the status rule. That asymmetry is deliberate rather than tidy: what it
# costs is one ladder of sleeping before a failure that was coming anyway, and
# the tidy version would put a second message rule on the status that does not
# need one. Its reset can be an hour out either way, so no bounded backoff
# clears it, and a caller that has really spent its budget is better off saying
# so.
SECONDARY_RATE_LIMIT = "secondary rate limit"


def http_failure(line: str) -> tuple[int, str] | None:
    """The status and message of a `gh` HTTP-error line, or None for any other.

    One reader for every shape the HTTP-error patterns above describe, so the
    retry rule is written once against a status rather than once per
    subcommand's phrasing.
    """
    for pattern in (
        GH_API_HTTP_ERROR,
        GH_API_BARE_HTTP_ERROR,
        GH_HTTP_ERROR,
        GH_GRAPHQL_NON_200,
    ):
        failure = pattern.match(line)
        if failure is not None:
            return int(failure["status"]), failure["message"]
    return None


def is_transient(stderr: str) -> bool:
    """Whether a failed `gh` call says the API was busy rather than that the
    thing being asked for is wrong.

    Only the first is worth another attempt, and the direction of an unrecognised
    message is NOT transient, so a shape nobody has catalogued costs nothing new.

    The classes, all read off shapes taken from live `gh` runs rather than from a
    model of its output:

      - An HTTP failure, in any shape `gh` reports one in (http_failure).
        429 and the 5xx family are the upstream saying it could not serve THIS
        request; every other status is an answer about the request itself. The
        403 is split by its message, because GitHub's primary and secondary rate
        limits are one status and want opposite handling
        (SECONDARY_RATE_LIMIT).
      - `error connecting to <host>` - `gh` never reached the API at all.
      - A request `gh` sent and got no answer to (GH_NO_ANSWER), or an answer
        that arrived empty or cut off (GH_CUT_OFF_ANSWER). Neither carries a
        status; each is the API failing to serve this request, as a 5xx is.
      - The GraphQL resolver's own give-up (GH_GRAPHQL_GAVE_UP), the 5xx of a
        200 response.
      - Anything else, `jq` shape drift included: `jq`'s diagnostic carries no
        prefix and no status, so it falls through to False, which is the abort
        the drift deserves.

    Every other `GraphQL: <message>` line stays in that last arm. A 200 carrying
    errors is also how a resolution failure arrives - a PR or label that does
    not exist - and only the give-up wording above has been sampled as GitHub
    failing rather than answering.

    Read line by line rather than over the blob: `gh` can print a warning ahead
    of the error, and the anchors are per-line. The response body cannot reach
    here to false-match - it goes to stdout, checked against a live 404.

    The whole 5xx family counts, 500 included, on the reasoning that any 4xx but
    429 is a definitive verdict a retry cannot fix. A 500 that is really a
    server-side bug costs one bounded backoff inside one process, after which
    the read fails exactly as it would have.
    """
    for line in stderr.splitlines():
        if (
            line.startswith((GH_CONNECT_FAILURE, GH_GRAPHQL_GAVE_UP))
            or GH_NO_ANSWER.match(line)
            or line == GH_CUT_OFF_ANSWER
        ):
            return True
        failure = http_failure(line)
        if failure is None:
            continue
        status, message = failure
        if status == 429 or 500 <= status < 600:
            return True
        if status == 403 and SECONDARY_RATE_LIMIT in message.lower():
            return True
    return False
