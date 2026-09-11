# Security Policy

## Reporting a vulnerability

Please do not open a public issue or pull request for a security vulnerability.

Instead, use GitHub's private vulnerability reporting for this repository: go to the **Security** tab →
**Advisories** → **Report a vulnerability**, or use this direct link:

https://github.com/kajisho5/motion-graphics-skill/security/advisories/new

This opens a report visible only to the maintainer, so details aren't disclosed publicly before a fix is ready.

If that button isn't available, private vulnerability reporting may not be enabled yet for this repository
(**Settings → Security → Private vulnerability reporting**) — in that case, open a minimal public issue asking
the maintainer to enable it, without describing the vulnerability itself.

## Scope

This repository delegates all actual media processing to
[`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill) through typed, argv-only subprocess calls, never a
shell and never a constructed filter string (see [`docs/security.md`](docs/security.md) for the full boundary:
the tool allowlist, `PathPolicy`, the forbidden-field list, and the environment allowlist). In scope for this
repository are vulnerabilities in that boundary itself — anything that lets a request:

- inject a shell command or reach an executable outside the `ffmpeg-skill` tool allowlist,
- escape `PathPolicy`'s workspace/allowed-roots confinement (traversal, symlink escape, an unsafe file name),
- smuggle a raw ffmpeg filter/command/argv/environment variable past the forbidden-field checks, or
- otherwise make `doctor`/`contract` claim support this Skill cannot actually back with a working renderer.

Vulnerabilities in `ffmpeg`, `ffprobe`, or `ffmpeg-skill` itself are out of scope here — please report those to
the [`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill) repository (or upstream FFmpeg) instead.

## Supported versions

This project has not yet reached a `1.0` release. Only the latest version on the default branch receives
security fixes.
