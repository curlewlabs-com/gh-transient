# Contributing

Thanks for your interest in improving `gh-transient`. The bar for a change is
whether it keeps the two kinds of failure apart: a call GitHub failed to serve
is re-issued, and a call GitHub answered fails at once. A new failure shape,
a sharper anchor on an existing one, and clearer docs are all welcome.

## Adding a failure shape

A shape joins the classifier only with a live sample of it: the exact stderr a
real `gh` run printed, with the repository, branch and query trimmed out. Put
that sample in the test that covers it, and say in the pattern's comment which
subcommand produced it and when. A shape inferred from `gh`'s source or from
what a failure "would" look like is not enough, because a wrong "transient"
spends a whole ladder of waiting before every failure of that kind.

Keep the direction of an unrecognised failure as it is: not transient.

## Running the checks

These are the checks CI runs on every push and pull request:

```sh
python3 -m unittest discover -s tests -t .
python3 -m pip install -r requirements-dev.txt
ruff format --check . && ruff check . && mypy
```

## Conventions

- **Tests ship with the change.** A bug fix or a feature includes a test in the
  same pull request. A test that guards a failure mode says which one.
- **No clock in the tests.** The retry takes its `sleep` and `clock` as
  arguments, and tests pass their own, so no test waits.
- **Fail closed.** An unrecognised failure is reported, never guessed at.
- **Standard library only at runtime**, so the action needs nothing installed.
- **The action's step calls a script.** Its `run:` in `action.yml` is one call
  of `action/put-on-pythonpath.sh`, so shellcheck reads all of the shell it
  runs.
- **Pin dependencies exactly.** An action reference is its release's commit
  SHA with the version beside it, since a tag can be moved, and the tools in
  `requirements-dev.txt` are exact versions, so a resolver cannot move them.
  Renovate proposes their updates; a pin written inline in a workflow needs a
  `# renovate:` comment above it saying what the literal is.
- **Comments explain why, not what.**
- **Keep tracked text ASCII.** Use plain hyphens and words instead of Unicode
  punctuation or decorative symbols.

## Submitting

Keep each pull request focused on one change, make sure the checks pass, and
describe the why in the pull request body. CI must be green before merge.

## Releasing

A patch or minor release needs no approval: whoever cuts it follows the steps
below in order, on a `main` commit whose required checks have passed. A major
version, a 1.0, or a minor before 1.0 that changes an interface consumers use
needs the maintainer's explicit approval before its tag is pushed.

Every release gets a fixed `vMAJOR.MINOR.PATCH` tag. The repository blocks
updates and deletions of those tags, and publishing the GitHub Release makes
the tag and its assets immutable. The floating `vMAJOR` tag is moved to each new
release in that major series, so workflows on `@v0` get fixes. Never attach a
release to the floating tag, because immutability would stop the next move.

1. Bump `version` in `pyproject.toml` and merge that to `main`.
2. Tag the merge commit `vMAJOR.MINOR.PATCH` and push the tag.
3. Move `vMAJOR` to the same commit and push it.
4. Publish the fixed tag: `gh release create vMAJOR.MINOR.PATCH`.

## License

By submitting a contribution for inclusion in this project, you agree that it
is licensed under the MIT and Apache-2.0 licenses, as the README's License
section describes, without any additional terms or conditions.
