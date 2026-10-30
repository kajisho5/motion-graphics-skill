"""motion-graphics-skill: deterministic motion-graphics rendering execution Skill (not an AI agent).

It executes a typed, validated Graphics Document (titles, lower thirds, text overlays, image/logo overlays,
with fade animation) through ffmpeg-skill and reports provenance. It does not decide what to show, when to show
it, or how it should look (video-production-agent); it does not edit video (video-editing-skill); it does not
grade color (color-grading-skill); it does not generate or translate subtitles (subtitle-skill); and it never
runs a shell, an arbitrary command, or an arbitrary ffmpeg filter."""

SKILL_ID = "motion-graphics"
PACKAGE_NAME = "motion-graphics-skill"
def _version() -> str:
    """The package version, from one place: this checkout's pyproject.toml when running from source,
    otherwise the installed distribution's metadata. Releases bump only pyproject.toml."""
    import re as _re
    from pathlib import Path as _Path
    pyproject = _Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if _re.search(r'^name\s*=\s*"motion-graphics-skill"', text, _re.M):
            found = _re.search(r'^version\s*=\s*"([^"]+)"', text, _re.M)
            if found:
                return found.group(1)
    import sys as _sys
    for base in _sys.path:
        for meta in sorted(_Path(base or ".").glob("motion_graphics_skill-*.dist-info/METADATA")):
            found = _re.search(r"^Version: *(\S+)", meta.read_text(encoding="utf-8", errors="replace"), _re.M)
            if found:
                return found.group(1)
    return "0.0.0+unknown"


VERSION = _version()

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION"]
