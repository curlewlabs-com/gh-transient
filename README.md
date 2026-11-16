# gh-transient

## What it is

A small standard-library Python package that answers one question about a
failed [`gh`](https://cli.github.com/) call: did GitHub fail to serve it, or
did GitHub answer it? A read that GitHub failed to serve is re-issued on a
short, bounded ladder; one that GitHub answered fails at once.

It comes three ways:

- **A classifier**, `is_transient(stderr)`, over the shapes `gh` prints when
  GitHub did not answer: a 5xx or 429 in any of the forms `gh` reports a status
  in, the secondary rate limit's 403, a request that got no answer (timeouts,
  resets, refused proxies), an answer cut off mid-JSON, and the GraphQL
  resolver giving up.
- **A retry**, `read_gh(args)`, which runs `gh`, re-issues a read on that
  evidence, and raises `GhError` with `gh`'s message when it keeps failing.
- **A command and a GitHub Action** for workflow shell steps:
  `python3 -m gh_transient read -- <gh arguments>`, with the action putting
  the package on the job's `PYTHONPATH` so nothing is installed.

## Why it exists

`gh` has no retry of its own ([cli/cli#7533](https://github.com/cli/cli/issues/7533)).
GitHub's API fails some calls outright - a gateway error, a reset socket, a
GraphQL query that timed out on GitHub's side - and the same call a few seconds
later succeeds. A script that takes one of those failures as final aborts a
deploy, or voids a scheduled report, over a call nobody needed to repeat by
hand.

The usual fixes are worse in one direction or the other. A retry-the-whole-step
action repeats on any failure, so a 404, a bad token or a `jq` filter that no
longer matches costs a ladder of waiting before the failure it already earned,
and it cannot sit inside a script. A loop in shell has no way to tell the two
kinds of failure apart either.

This package tells them apart from what `gh` prints, and it fails closed: a
failure it does not recognise is not transient, so it is reported on the first
attempt exactly as it would be without the package. Every shape it recognises
was taken from a live `gh` failure rather than from a model of `gh`'s output.

Only reads are re-issued. A create, comment or dispatch that failed may have
landed anyway, so `run_gh` runs a mutation once and leaves to its caller
whether repeating it is harmless.

## How to use it

### In a workflow step

```yaml
steps:
  - uses: curlewlabs-com/gh-transient@v0
  - name: Read the latest release
    env:
      GH_TOKEN: ${{ github.token }}
    run: |
      tag=$(python3 -m gh_transient read -- api "repos/$GITHUB_REPOSITORY/releases/latest" --jq .tag_name)
      echo "latest: $tag"
```

The action needs no checkout of this repository and installs nothing: it puts
the package beside it on `PYTHONPATH` for every later step of the job. Each
retry is a `::warning::` annotation, and a read GitHub keeps failing is an
`::error::` annotation and exit status 1.

The command is for text reads - JSON, `--jq` and `--template` output, the
listings `gh` prints. It reads `gh`'s stdout as text and writes it back out,
which decodes it and normalizes line endings, so a binary download such as a
zipball or a release asset does not belong here; run `gh` for those directly.

The command's options:

- `--budget-file PATH` names a file holding the retries left to a whole step.
  Every call the step makes draws from it, so a loop over many records cannot
  spend a full ladder per record. Write the count once at the top of the step;
  a call that finds none left fails at its first failure. Calls sharing one
  file may run at once: each takes its retry under a lock on the file.

```sh
budget=$(mktemp); echo 3 > "$budget"
for pr in $prs; do
  python3 -m gh_transient read --budget-file "$budget" -- pr view "$pr" --json state
done
```

Give the job a `timeout-minutes` that holds one full ladder (below) on top of
its own work, so a read GitHub keeps failing ends in the command's error rather
than a cancelled job.

### From Python

Outside Actions, install it from this repository at a fixed tag:

```sh
python3 -m pip install "gh-transient @ git+https://github.com/curlewlabs-com/gh-transient@v0.2.0"
```

```python
from gh_transient import GhError, read_gh, run_gh

try:
    body = read_gh(["api", "repos/OWNER/REPO/actions/runs", "--jq", ".total_count"])
except GhError as error:
    # error.transient: GitHub kept failing rather than answering.
    # error.stderr: what gh printed on the last attempt.
    raise SystemExit(f"could not read runs: {error}")

run_gh(["issue", "comment", "1", "--body", "done"])  # a mutation: once
```

`read_gh` takes these keyword arguments:

- `timeout`: per attempt, default 60 seconds. A stalled attempt counts as
  transient.
- `deadline` and `clock`: a budget several reads share. No wait starts that
  would end past the deadline, and each attempt is cut off by the time left.
- `may_retry`: asked before each wait, so several reads can share a count.
- `on_retry` and `sleep`: hooks for logging and for tests.

A caller that keeps its own error type around its own `gh` runner can use the
pieces separately: `is_transient(stderr)` to classify, and
`retry_transient(attempt, transient)` for the ladder.

## What counts as transient

| `gh` printed                                                    | Transient                                  |
| --------------------------------------------------------------- | ------------------------------------------ |
| A 429 or any 5xx, in any form `gh` reports a status in          | yes                                        |
| A 403 naming the secondary rate limit                           | yes                                        |
| A 403 for the primary rate limit, or any other 4xx              | no - it is GitHub's answer                 |
| `error connecting to <host>`                                    | yes                                        |
| Go's error for a request with no answer (`Get "<url>": ...`)    | yes                                        |
| `unexpected end of JSON input` (an answer cut off)              | yes                                        |
| `GraphQL: Something went wrong while executing your query ...`  | yes                                        |
| Any other `GraphQL: ...` line                                   | no - it is how a missing PR or label reads |
| Anything else, a `jq` error included                            | no                                         |

The primary rate limit's 403 is deliberately not retried. Its reset can be an
hour away, which no bounded backoff clears, and a caller that has spent its
budget is better off saying so. (Its 429 form is retried, as every 429 is.)

A subcommand that rewords `gh`'s errors, as `gh secret set` does, is retried
only once a live sample of that wording is in the classifier. Until then its
failures read as unrecognised and fail on the first attempt.

## The retry ladder

A read is attempted, then re-attempted after 5, 20 and 60 seconds: four
attempts at most. The last wait is a minute because GitHub's REST guidance asks
for at least one minute before retrying a secondary rate limit that names no
reset time, so a secondary rate limit skips the shorter waits and is re-issued
once, after the minute. The command line cuts each attempt off at 30 seconds,
so one call's whole ladder fits in a few minutes of a job's timeout.

## Requirements

- Python 3.12 or newer, standard library only.
- `gh` on `PATH`, authenticated as usual (`GH_TOKEN` in a workflow).
- The action runs on Linux and macOS runners.

## Versioning

Releases are tagged `vMAJOR.MINOR.PATCH`, and those tags are immutable. The
floating `v0` tag follows the latest `v0.x` release, for workflows on `@v0`.
The Python names the package root exports, the command's verb and options, and
the action's behavior are the contract. Before 1.0.0 a change to any of them is
a minor version, and its release notes say how to move.

## Development

```sh
python3 -m unittest discover -s tests -t .
python3 -m pip install -r requirements-dev.txt
ruff format --check . && ruff check . && mypy
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the conventions, including what a new
failure shape needs before it joins the classifier.

## License

MIT - see [LICENSE](LICENSE).
