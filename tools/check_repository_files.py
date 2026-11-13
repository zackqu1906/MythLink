#!/usr/bin/env python3
"""Reject files in the Git index that repository ignore rules keep local."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    # Inspect the index, including `git add -f`, but permit staged removals.
    # Use repository .gitignore files only: global/per-machine excludes must
    # not make the local hook and CI disagree about what belongs in a release.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--ignored", "-z",
         "--exclude-per-directory=.gitignore"],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        sys.stderr.write(os.fsdecode(result.stderr))
        return result.returncode
    paths = sorted(set(os.fsdecode(p) for p in result.stdout.split(b"\0") if p))
    if paths:
        print("Commit blocked: these paths are reserved for local files:", file=sys.stderr)
        for path in paths:
            print(f"  {path}", file=sys.stderr)
        print(
            "Unstage newly added files, or use git rm --cached to stop tracking "
            "existing files while keeping local copies.\n"
            "Product assets and synthetic test fixtures need a specific reviewed "
            ".gitignore exception. See docs/REPOSITORY_CONTENTS.md.",
            file=sys.stderr,
        )
        return 1
    print("Repository file check passed: no ignored local files in the Git index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
