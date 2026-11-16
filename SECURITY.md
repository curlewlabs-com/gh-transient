# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
privately via
[GitHub's security advisory feature](https://github.com/curlewlabs-com/gh-transient/security/advisories/new).

Do not open a public issue for security vulnerabilities.

## Scope

The package runs `gh` with whatever credentials `gh` already has, and adds no
network access, token handling or file access of its own beyond the budget
file a caller names. The security surface is:

- **Repeated calls.** Only reads are re-issued. A call that changes state is
  meant to go through `run_gh`, which runs it once; a caller that sends a
  mutation through `read_gh` or the `read` command can repeat it.
- **Arguments.** The command passes everything after `--` to `gh` as an
  argument list, never through a shell.
- **The `gh` it runs** is the first one on `PATH`, which it trusts as it trusts
  `python3`.
- **The action** only prepends its own directory to `PYTHONPATH` for the rest
  of the job, so a module named `gh_transient` elsewhere on that path is
  shadowed.
