"""Decide the version this push should release, and whether that's an automatic bump or a value the maintainer
already set by hand. Runs inside .github/workflows/release.yml; pure git + file reads, no network access, no
untrusted-string interpolation (see that workflow's header comment for the injection concern this avoids).

Rule:
  - No tag exists yet at all -> nothing to bump from; trust whatever version is already in pyproject.toml as the
    intended first release (bumped=false). This avoids ever trusting release-drafter's own "no history" default
    (which can start from 0.0.0 and undercut an already-set higher starting version).
  - The latest tag's version matches pyproject.toml's current version -> no manual edit has happened since that
    tag; let release-drafter's dry-run resolved version (passed in via DRAFTED_TAG) win (bumped=true).
  - Otherwise pyproject.toml is already ahead of the latest tag -> a human bumped it by hand; respect that value
    verbatim and do not auto-bump on top of it (bumped=false).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
VERSION_RE = re.compile(r'(?m)^version = "([^"]+)"$')


def latest_tag() -> str | None:
    result = subprocess.run(["git", "tag", "--sort=-v:refname"], capture_output=True, text=True, check=True)
    tags = [t for t in result.stdout.splitlines() if t.strip()]
    return tags[0] if tags else None


def current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        print("::error::could not find a `version = \"...\"` line in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def write_outputs(**kwargs: str) -> None:
    out_path = os.environ["GITHUB_OUTPUT"]
    with open(out_path, "a", encoding="utf-8") as f:
        for key, value in kwargs.items():
            f.write(f"{key}={value}\n")


def main() -> None:
    tag = latest_tag()
    current = current_version()
    drafted_tag = os.environ.get("DRAFTED_TAG", "").strip()
    drafted_version = strip_v(drafted_tag) if drafted_tag else ""

    if tag is None:
        version, bumped, reason = current, "false", "no tag exists yet; using pyproject.toml's version as the first release"
    elif current == strip_v(tag):
        if not drafted_version:
            print("::error::no tag exists ahead of pyproject.toml's version, but release-drafter did not resolve a next version", file=sys.stderr)
            sys.exit(1)
        version, bumped, reason = drafted_version, "true", f"pyproject.toml matches latest tag {tag}; auto-bumping via release-drafter"
    else:
        version, bumped, reason = current, "false", f"pyproject.toml ({current}) is already ahead of latest tag {tag}; respecting the manual bump"

    print(f"::notice::{reason} -> v{version} (bumped={bumped})")
    write_outputs(version=version, bumped=bumped, previous_tag=tag or "")


if __name__ == "__main__":
    main()
