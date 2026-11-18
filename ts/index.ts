// The TypeScript way into the command: one `gh` read, re-issued while GitHub
// fails to serve it, run through the Python package this tarball carries.
//
// Nothing here classifies a failure or keeps a ladder. Those live once, in
// src/gh_transient, and this file runs them, so a shape learned from a live
// failure reaches TypeScript callers in the same release that teaches it to
// Python ones. The package's own copy goes first on PYTHONPATH and the working
// directory is kept off the import path, so the Python that runs is always the
// one released beside this file, whatever else the caller's environment or
// directory carries.
//
// There is no timeout here: the command bounds each attempt and the ladder
// between them (ATTEMPT_TIMEOUT_SECONDS in cli.py, RETRY_DELAYS_SECONDS in
// retry.py), so a call that GitHub keeps failing ends in the command's own
// error. A caller sizes its job's deadline from those.

import { spawnSync } from "node:child_process";
import { delimiter } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_PYTHON = fileURLToPath(new URL("../src", import.meta.url));
const ERROR_ANNOTATION = "::error::";

export interface ReadGhOptions {
  // A file holding the retries left to every call that names it; see the
  // README's `--budget-file`.
  readonly budgetFile?: string;
}

export class GhReadError extends Error {
  override name = "GhReadError";
}

// The bytes `gh` wrote to stdout, for a read: a call that creates, comments
// or dispatches may have landed even when it failed, so it never comes here.
// Throws GhReadError when GitHub answered with a failure, when it kept failing
// to serve the read, or when the command could not run.
export function readGhSync(
  args: readonly string[],
  options: ReadGhOptions = {},
): Buffer {
  const call = `gh ${args.join(" ")}`;
  const budget =
    options.budgetFile === undefined
      ? []
      : ["--budget-file", options.budgetFile];
  const inherited = process.env["PYTHONPATH"];
  const result = spawnSync(
    "python3",
    // -P keeps the working directory off sys.path, where `-m` would put it
    // ahead of PYTHONPATH and run any gh_transient the caller is standing in.
    ["-P", "-m", "gh_transient", "read", ...budget, "--", ...args],
    {
      env: {
        ...process.env,
        PYTHONPATH:
          inherited === undefined || inherited === ""
            ? PACKAGE_PYTHON
            : `${PACKAGE_PYTHON}${delimiter}${inherited}`,
      },
      stdio: ["ignore", "pipe", "pipe"],
      // A run's log archive can be far past the default buffer, which would
      // kill the command partway through writing it.
      maxBuffer: Infinity,
    },
  );
  const stderr = result.stderr?.toString() ?? "";
  // The command's retry warnings and its error are workflow annotations, so
  // they are passed on as written rather than kept only in the thrown error.
  if (stderr !== "") process.stderr.write(stderr);
  if (result.error !== undefined) {
    throw new GhReadError(
      `${call}: could not run python3 -m gh_transient: ${result.error.message}`,
      { cause: result.error },
    );
  }
  if (result.status !== 0) {
    // The command's error line already names the call and gh's message. It is
    // taken without its annotation prefix, so a caller that logs the message
    // in a workflow does not raise the same annotation twice.
    const reported = stderr
      .split("\n")
      .filter((line) => line.startsWith(ERROR_ANNOTATION))
      .pop();
    if (reported !== undefined) {
      throw new GhReadError(reported.slice(ERROR_ANNOTATION.length));
    }
    const how =
      result.status === null
        ? `was killed by ${String(result.signal)}`
        : `exited ${String(result.status)}`;
    throw new GhReadError(
      `${call}: python3 -m gh_transient ${how}: ${stderr.trim()}`,
    );
  }
  return result.stdout;
}
