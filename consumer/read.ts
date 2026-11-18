// One read through the installed package, as a caller writes it: CI type-checks
// this file against the packed tarball, then runs it.

import {
  GhReadError,
  readGhSync,
  type ReadGhOptions,
} from "@curlewlabs-com/gh-transient";

const options: ReadGhOptions = {};
const repo = process.env["GITHUB_REPOSITORY"] ?? "";
try {
  process.stdout.write(
    readGhSync(["api", `repos/${repo}`, "--jq", ".full_name"], options),
  );
} catch (error) {
  if (error instanceof GhReadError) process.stderr.write(`${error.message}\n`);
  throw error;
}
