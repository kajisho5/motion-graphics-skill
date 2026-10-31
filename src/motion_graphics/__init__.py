"""motion-graphics-skill: deterministic motion-graphics rendering execution Skill (not an AI agent).

It executes a typed, validated Graphics Document (titles, lower thirds, text overlays, image/logo overlays,
with fade animation) through ffmpeg-skill and reports provenance. It does not decide what to show, when to show
it, or how it should look (video-production-agent); it does not edit video (video-editing-skill); it does not
grade color (color-grading-skill); it does not generate or translate subtitles (subtitle-skill); and it never
runs a shell, an arbitrary command, or an arbitrary ffmpeg filter."""

SKILL_ID = "motion-graphics"
PACKAGE_NAME = "motion-graphics-skill"


def _version() -> str:
    """The package version, from one place: pyproject.toml (the only file the release workflow bumps -- ADR-21).

    From a source checkout (including an editable install, whose recorded metadata goes stale on the next release
    bump) it is read straight from this checkout's pyproject.toml; from an installed wheel, from the distribution
    metadata (importlib.metadata) that was built from that same pyproject.toml. If neither is found it reports
    0.0.0+unknown rather than a plausible but wrong number."""
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if re.search(r'^name\s*=\s*"motion-graphics-skill"\s*$', text, re.M):
            found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.M)
            if found:
                return found.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8 is not supported
        return "0.0.0+unknown"
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.0.0+unknown"


VERSION = _version()

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION"]
