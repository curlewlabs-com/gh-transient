// readGhSync against the real command and a stand-in `gh` on PATH. Which
// failures are re-issued is the Python suite's; these cover what the
// TypeScript side adds: the bytes come back whole, the command's verdict
// becomes a thrown error whichever way the command failed, its annotations
// reach the caller's stderr, the options reach the command, and the Python
// that runs is the copy released beside this file over the standard library,
// whatever the caller's import path holds.

import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";

import { GhReadError, readGhSync } from "./index.ts";

const ARCHIVE = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0xff, 0x0d, 0x0a, 0x00]);

const FAKE_GH = `#!/bin/sh
echo call >> "$FAKE_GH_CALLS"
case "$FAKE_GH_MODE" in
  archive) printf 'PK\\003\\004\\377\\r\\n\\000' ;;
  refused) echo 'gh: Not Found (HTTP 404)' >&2; exit 1 ;;
  busy) echo 'gh: Server Error (HTTP 502)' >&2; exit 1 ;;
esac
`;

let dir: string;
const saved = { ...process.env };

function calls(): number {
  try {
    return readFileSync(join(dir, "calls"), "utf8").split("\n").filter(Boolean)
      .length;
  } catch {
    return 0;
  }
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "gh-transient-"));
  mkdirSync(join(dir, "bin"));
  writeFileSync(join(dir, "bin", "gh"), FAKE_GH);
  chmodSync(join(dir, "bin", "gh"), 0o755);
  process.env["PATH"] = `${join(dir, "bin")}${delimiter}${saved["PATH"] ?? ""}`;
  process.env["FAKE_GH_CALLS"] = join(dir, "calls");
  delete process.env["PYTHONPATH"];
});

afterEach(() => {
  process.env = { ...saved };
  rmSync(dir, { recursive: true, force: true });
});

describe("readGhSync", () => {
  test("returns the bytes gh wrote", () => {
    // A run's log archive is a zip; a decoded or newline-normalized copy is
    // one the caller cannot open.
    process.env["FAKE_GH_MODE"] = "archive";
    expect(readGhSync(["api", "x"]).equals(ARCHIVE)).toBe(true);
  });

  test("throws the command's error at once when GitHub answered", () => {
    process.env["FAKE_GH_MODE"] = "refused";
    let thrown: unknown;
    try {
      readGhSync(["api", "x"]);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(GhReadError);
    const message = (thrown as GhReadError).message;
    expect(message).toContain("gh api x");
    expect(message).toContain("Not Found (HTTP 404)");
    // A caller that logs the message must not raise a second annotation.
    expect(message.startsWith("::error::")).toBe(false);
    expect(calls()).toBe(1);
  });

  function thrownBy(read: () => unknown): GhReadError {
    let thrown: unknown;
    try {
      read();
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(GhReadError);
    return thrown as GhReadError;
  }

  test("passes the command's annotations on to stderr", () => {
    // A workflow log shows the retry warnings and the error only if they
    // reach the caller's stderr; the thrown error alone is not an annotation.
    process.env["FAKE_GH_MODE"] = "busy";
    const budgetFile = join(dir, "budget");
    writeFileSync(budgetFile, "0\n");
    const written: string[] = [];
    const spy = spyOn(process.stderr, "write").mockImplementation((chunk) => {
      written.push(String(chunk));
      return true;
    });
    try {
      thrownBy(() => readGhSync(["api", "x"], { budgetFile }));
    } finally {
      spy.mockRestore();
    }
    expect(written.join("")).toContain("::error::");
  });

  test("throws, naming the exit status, when the command refuses its arguments", () => {
    // A failure the command reports without an error annotation (here its
    // usage error) must still be a GhReadError, not a read that returned.
    const message = thrownBy(() => readGhSync([])).message;
    expect(message).toContain("exited 2");
    expect(calls()).toBe(0);
  });

  test("throws when python3 cannot be started", () => {
    process.env["PATH"] = join(dir, "bin");
    const message = thrownBy(() => readGhSync(["api", "x"])).message;
    expect(message).toContain("could not run");
    expect(calls()).toBe(0);
  });

  test("passes the budget file to the command", () => {
    // With no retries left in the file, a read GitHub failed to serve fails
    // at its first failure instead of waiting out the ladder.
    process.env["FAKE_GH_MODE"] = "busy";
    const budgetFile = join(dir, "budget");
    writeFileSync(budgetFile, "0\n");
    expect(() => readGhSync(["api", "x"], { budgetFile })).toThrow(GhReadError);
    expect(calls()).toBe(1);
  });

  // A workflow that also uses the action has another release's copy on
  // PYTHONPATH, and a script may run from a directory holding one; running
  // either would pair this file with a classifier it was not released with.
  function anotherRelease(root: string): void {
    const pkg = join(root, "gh_transient");
    mkdirSync(pkg, { recursive: true });
    writeFileSync(join(pkg, "__init__.py"), "");
    writeFileSync(join(pkg, "__main__.py"), "print('another release')\n");
  }

  test("runs the Python released beside it, not one on PYTHONPATH", () => {
    anotherRelease(join(dir, "other"));
    process.env["PYTHONPATH"] = join(dir, "other");
    process.env["FAKE_GH_MODE"] = "archive";
    expect(readGhSync(["api", "x"]).equals(ARCHIVE)).toBe(true);
  });

  test("no standard module on an inherited PYTHONPATH stands in for the real one", () => {
    // A caller whose PYTHONPATH holds `.` puts its working directory ahead of
    // the standard library; a module there named like one the command imports
    // would run in its place, and this one turns a refused read into a pass.
    writeFileSync(join(dir, "argparse.py"), "raise SystemExit(0)\n");
    process.env["PYTHONPATH"] = ".";
    process.env["FAKE_GH_MODE"] = "refused";
    const before = process.cwd();
    process.chdir(dir);
    try {
      thrownBy(() => readGhSync(["api", "x"]));
    } finally {
      process.chdir(before);
    }
    expect(calls()).toBe(1);
  });

  test("runs the Python released beside it, not one in the working directory", () => {
    anotherRelease(dir);
    process.env["FAKE_GH_MODE"] = "archive";
    const before = process.cwd();
    process.chdir(dir);
    try {
      expect(readGhSync(["api", "x"]).equals(ARCHIVE)).toBe(true);
    } finally {
      process.chdir(before);
    }
  });
});
