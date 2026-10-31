"""Write NEW_VERSION (an env var, never interpolated via `${{ }}` into shell syntax) into pyproject.toml's
`version = "..."` line. Only run when resolve_version.py decided this is an automatic bump.

pyproject.toml is the only file that carries the version (ADR-21): motion_graphics.VERSION, the contract's
`version` and its tool spec's `version` are all read from it at runtime, so this one write is the whole bump.
After writing, the package is imported from this checkout's src/ and its contract must report NEW_VERSION;
the release stops here otherwise, before anything is committed or tagged."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
VERSION_LINE_RE = re.compile(r'(?m)^version = "[^"]+"$')


def reported_versions() -> set:
    """Every version the package reports, read from this checkout's src/ (not from an installed copy)."""
    sys.path.insert(0, str(Path("src").resolve()))
    import motion_graphics
    from motion_graphics.contract import skill_contract

    contract = skill_contract()
    return {motion_graphics.VERSION, contract["version"], *(t["version"] for t in contract["tools"])}


def main() -> None:
    new_version = os.environ["NEW_VERSION"]
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = VERSION_LINE_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        print("::error::expected exactly one `version = \"...\"` line in pyproject.toml, found", count, file=sys.stderr)
        sys.exit(1)
    PYPROJECT.write_text(new_text, encoding="utf-8")
    print(f"pyproject.toml version -> {new_version}")
    reported = reported_versions()
    if reported != {new_version}:
        print(f"::error::after the bump the package still reports {sorted(reported)}, not {new_version}", file=sys.stderr)
        sys.exit(1)
    print(f"motion_graphics.VERSION and the contract report {new_version}")


if __name__ == "__main__":
    main()
