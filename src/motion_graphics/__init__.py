"""motion-graphics-skill: deterministic motion-graphics rendering execution Skill (not an AI agent).

It executes a typed, validated Graphics Document (titles, lower thirds, text overlays, image/logo overlays,
with fade animation) through ffmpeg-skill and reports provenance. It does not decide what to show, when to show
it, or how it should look (video-production-agent); it does not edit video (video-editing-skill); it does not
grade color (color-grading-skill); it does not generate or translate subtitles (subtitle-skill); and it never
runs a shell, an arbitrary command, or an arbitrary ffmpeg filter."""

SKILL_ID = "motion-graphics"
PACKAGE_NAME = "motion-graphics-skill"
VERSION = "0.1.0"

CONTRACT_SCHEMA_VERSION = 1
REQUEST_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
DOCTOR_SCHEMA_VERSION = 1

__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION"]
