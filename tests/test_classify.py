"""Which failed `gh` calls are GitHub not answering.

The shapes are taken from live `gh` failures rather than from a model of its
output, each named where it came from and trimmed of the repository, branch and
query it carried. The rest are boundary cases built against those shapes - a
longer number, a status inside a message, a warning ahead of the error.
"""

from __future__ import annotations

import unittest

from gh_transient import is_transient


class TestABusyApiIsNotAVerdict(unittest.TestCase):
    """Which failed reads are re-issued, which abort, and the precision that
    keeps the two apart.

    Each direction costs something real. A blip read as a verdict voided a whole
    morning's sweep on a job that runs once daily and aborted a deploy; a
    genuine failure read as a blip spends the backoff and then fails anyway,
    which is why an unrecognised message stays non-transient.
    """

    def test_a_gateway_error_is_retried(self) -> None:
        self.assertTrue(is_transient("gh: Server Error (HTTP 502)"))

    def test_the_issue_reads_error_shape_is_read_too(self) -> None:
        """`gh api` reports a failure differently from every other subcommand,
        and callers use `gh api` and the others - so a shape gap here leaves the
        issue-list reads with no retry at all, which is the whole failure one call
        over."""
        self.assertTrue(
            is_transient("HTTP 502: Bad Gateway (https://api.github.com/graphql)")
        )

    def test_that_shape_aborts_on_a_definitive_status(self) -> None:
        self.assertFalse(
            is_transient("HTTP 401: Bad credentials (https://api.github.com/graphql)")
        )

    def test_that_shape_carries_no_message_when_the_response_had_none(self) -> None:
        """`gh` drops the `: <message>` half when the response carried none, so
        the status has to decide without one."""
        self.assertTrue(
            is_transient("HTTP 503 (https://api.github.com/repos/o/n/labels)")
        )

    def test_a_longer_number_is_not_a_status(self) -> None:
        """The bare shape has no closing anchor, so the boundary after the
        status is what stops `4011` reading as a retryable `401`."""
        self.assertFalse(is_transient("HTTP 5029: whatever"))

    def test_gh_apis_status_without_a_message_is_read(self) -> None:
        """`gh api` prints the status alone when the error body held no message,
        as a 5xx with an empty body does. Read as status-less, a gateway blip
        was a first-attempt abort; a bare refusal must still abort."""
        self.assertTrue(is_transient("gh: HTTP 500"))
        self.assertFalse(is_transient("gh: HTTP 404"))

    def test_a_request_that_got_no_answer_is_retried(self) -> None:
        """Go's error for a request `gh` sent with no answer back: the timeouts
        are `gh api` ref reads, and the proxy line is a GraphQL POST, the
        transport `gh issue list` uses. Only a failed name lookup used to
        count as unreachable."""
        for stderr in (
            (
                'Get "https://api.github.com/repos/o/n/git/matching-refs/x/?per_page=100":'
                " dial tcp 20.29.134.17:443: i/o timeout"
            ),
            (
                'Get "https://api.github.com/repos/o/n/git/matching-refs/x/?per_page=100":'
                " net/http: TLS handshake timeout"
            ),
            (
                'Post "https://api.github.com/graphql": proxyconnect tcp: dial tcp'
                " 127.0.0.1:9: connect: connection refused"
            ),
        ):
            with self.subTest(stderr=stderr):
                self.assertTrue(is_transient(stderr))

    def test_a_cut_off_answer_is_retried(self) -> None:
        """An answer that arrived empty or cut off fails `gh api --jq` with Go's
        decoder error and no status - the line a `gh api --jq` ref read logged."""
        self.assertTrue(is_transient("unexpected end of JSON input"))

    def test_the_graphql_resolver_giving_up_is_retried(self) -> None:
        """A 200 whose error is GitHub's resolver giving up, verbatim from a
        `gh pr comment` that failed on 2026-11-06. It was the one GraphQL shape
        left unrecognised, and it failed a job whose work was already done."""
        self.assertTrue(
            is_transient(
                "GraphQL: Something went wrong while executing your query on "
                "2026-11-06T23:45:47Z. Please include "
                "`93BC:190DFE:9D6631:E15679:6AEE66A9` when reporting this issue."
            )
        )

    def test_a_graphql_answer_about_the_query_is_not(self) -> None:
        """The same prefix carries resolution failures, which a retry cannot
        fix, so only the give-up wording is read as busy."""
        self.assertFalse(
            is_transient(
                "GraphQL: Could not resolve to a PullRequest with the number "
                "of 99999999. (repository.pullRequest)"
            )
        )

    def test_the_graphql_clients_non_200_is_read_by_status(self) -> None:
        """`gh pr comment`'s GraphQL client words a non-200 its own way, taken
        from a failure the same day."""
        self.assertTrue(
            is_transient(
                'non-200 OK status code: 502 Bad Gateway body: "<html>\\r\\n'
                '<head><title>502 Bad Gateway</title></head>"'
            )
        )
        self.assertFalse(
            is_transient('non-200 OK status code: 401 Unauthorized body: "{}"')
        )

    def test_gh_run_lists_lead_does_not_hide_a_request_with_no_answer(self) -> None:
        """`gh run list` prefixes Go's error with its own words, sampled on
        2026-11-05; anchored at column 0, the shape
        alone missed it."""
        self.assertTrue(
            is_transient(
                'failed to get runs: Get "https://api.github.com/repos/o/n/actions/'
                'runs?branch=b&per_page=100": net/http: TLS handshake timeout'
            )
        )

    def test_too_many_requests_is_retried(self) -> None:
        self.assertTrue(is_transient("gh: Too Many Requests (HTTP 429)"))

    def test_an_unreachable_api_is_retried(self) -> None:
        """`gh`'s own words when it never reached GitHub at all - the one
        failure that carries no status code to read."""
        self.assertTrue(
            is_transient(
                "error connecting to nonexistent.invalid\n"
                "check your internet connection or https://githubstatus.com"
            )
        )

    def test_a_secondary_rate_limit_is_retried(self) -> None:
        """The 403 that means the API was busy rather than that the read was
        refused, told apart by GitHub's own wording for it."""
        self.assertTrue(
            is_transient(
                "gh: You have exceeded a secondary rate limit and have been "
                "temporarily blocked from content creation. Please retry your "
                "request again later. (HTTP 403)"
            )
        )

    def test_the_primary_rate_limit_is_not(self) -> None:
        """Same status, opposite handling: its reset can be an hour out, so a
        bounded backoff cannot clear it and a caller should surface a budget it has
        really spent."""
        self.assertFalse(
            is_transient("gh: API rate limit exceeded for user ID 1. (HTTP 403)")
        )

    def test_a_refusal_is_not(self) -> None:
        self.assertFalse(
            is_transient(
                "gh: You must have repository read permissions or have the "
                "repository runners fine-grained permission. (HTTP 403)"
            )
        )

    def test_a_missing_resource_is_not(self) -> None:
        self.assertFalse(is_transient("gh: Not Found (HTTP 404)"))

    def test_bad_credentials_are_not(self) -> None:
        self.assertFalse(is_transient("gh: Bad credentials (HTTP 401)"))

    def test_jq_shape_drift_is_not(self) -> None:
        """The failure that must abort rather than wait: a projection that no
        longer fits the payload is not going to fit it in 5 seconds, and a
        projection that silently reads nothing makes a report look healthy."""
        self.assertFalse(is_transient('cannot iterate over: string ("a")'))

    def test_a_status_code_inside_the_message_is_not_the_status(self) -> None:
        """The false positive a bare substring would produce: the code that
        decides is `gh`'s own trailing one, not a number the API happened to
        name in its message."""
        self.assertFalse(
            is_transient("gh: Could not resolve to a Repository 'o/502'. (HTTP 404)")
        )

    def test_an_error_under_a_warning_still_reads(self) -> None:
        """Read per line, because `gh` can print something ahead of the error
        and the anchors are line-anchored."""
        self.assertTrue(is_transient("warning: something\ngh: Bad Gateway (HTTP 502)"))

    def test_silence_is_not_evidence_of_a_blip(self) -> None:
        self.assertFalse(is_transient(""))


if __name__ == "__main__":
    unittest.main()
