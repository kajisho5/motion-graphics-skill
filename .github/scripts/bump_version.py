"""Write NEW_VERSION (an env var, never interpolated via `${{ }}` into shell syntax) into pyproject.toml's
`version = "..."` line. Only run when resolve_version.py decided this is an automatic bump."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
VERSION_LINE_RE = re.compile(r'(?m)^version = "[^"]+"$')


def main() -> None:
    new_version = os.environ["NEW_VERSION"]
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = VERSION_LINE_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        print("::error::expected exactly one `version = \"...\"` line in pyproject.toml, found", count, file=sys.stderr)
        sys.exit(1)
    PYPROJECT.write_text(new_text, encoding="utf-8")
    print(f"pyproject.toml version -> {new_version}")


if __name__ == "__main__":
    main()
