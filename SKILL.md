---
name: motion-graphics
description: Deterministic motion-graphics rendering execution Skill for the AI Video Production Ecosystem. Use it when a caller (normally video-production-agent) has already decided what graphic to show, when to show it, and roughly how it should look, and needs it rendered safely: title cards, lower thirds, free-form text overlays, and image/logo overlays, with built-in template animation (title/lower-third fade and slide) or a configurable linear fade (text/image overlays). Do NOT use it to decide what graphic to show, when, or how it should look (video-production-agent), to edit video (video-editing-skill), to grade color (color-grading-skill), to generate or translate subtitles (subtitle-skill), to measure or QC media (qc-skill, media-analysis-skill), or to run arbitrary ffmpeg commands or filters (it refuses them).
---

# motion-graphics-skill

Deterministic motion-graphics rendering execution for the AI Video Production Ecosystem, built on top of
[ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill). See `README.md` and `docs/` for the full contract,
graphics model, security boundary, and testing notes.

## Ask for this, not that

Ask this Skill to render: an exact `title`/`lower_third`/`text_overlay`/`image_overlay` element, with exact
`start`/`end` seconds and exact parameter values already decided. Every field is typed and range-checked; there is
no free-text "make it look nice" input.

Do not ask it to: choose what text or image to show, choose when something should appear, choose colors/fonts/
layout for aesthetic reasons, animate anything beyond a linear `fade` (no slide/move/scale — see
`unsupported_animations` in the contract), draw an arbitrary shape (no typed delegate for that yet), or accept a
raw ffmpeg filter/command/argv/shell/env — those field names are rejected wherever they appear in the request,
recursively (`INVALID_REQUEST`). All of that is `video-production-agent`'s job, not this Skill's.

## Workflow

```bash
pip install -e .
motion-graphics doctor --json --ffmpeg-skill /path/to/ffmpeg-skill   # 1. confirm the environment once
motion-graphics validate request.json --json                          # 2. structural check, no file access
motion-graphics plan request.json --json --workspace .                # 3. optional: resolve/probe, write nothing
motion-graphics run request.json --json --workspace .                 # 4. render
```

1. **`doctor`** once per environment (or when a render fails with `TOOL_ERROR`) — read `checks.element_types` to
   confirm the element types this request needs are `supported`, not `unsupported`/`unknown`.
2. **`validate`** a programmatically-built request before render if there's any doubt it's well-formed — it never
   touches the file system, so it's cheap to call speculatively.
3. **`plan`** (or `run --dry-run`) when the caller wants to catch a missing asset/font/out-of-range timeline
   before spending a real render on it. It resolves and probes inputs but writes no media.
4. **`run`** to actually render. Read `output.sha256`/`output.path` for the artifact and `provenance` for the full
   chain (source video, per-element asset/font identities, operation chain, output hash).

Full request/response shapes: `motion-graphics contract --json` (`request.shape`, `response.success.{run,plan,validate}`).

## On failure

Every failure is `{"ok": false, "error": {"code", "message", "retryable", "details"}}` with a fixed exit code
(`contract --json` → `errors.exit_codes`). Branch on `error.code`/`error.retryable`, never on `error.message`:

- `error.retryable: true` (`TOOL_ERROR`, `CANCELLED`) — safe to retry as-is; the request was fine, the engine or
  environment had a transient problem.
- Anything else — the request itself needs to change before retrying. In particular:
  - `UNSUPPORTED_OPERATION` on an element `type` or an `animation.kind` — it is not implemented (check
    `unsupported_element_types` / `unsupported_animations` in the contract for why and what to use instead); do
    not retry the same request, and do not approximate it with a different element type without deciding that
    explicitly upstream.
  - `MISSING_INPUT` (unknown `font_id`) / `INVALID_INPUT` (missing/unreadable asset or video) / `PATH_NOT_ALLOWED`
    (asset or output outside the allowed workspace/roots) — fix the reference or the path, this Skill never
    substitutes a different font or asset and calls it success.
  - `INVALID_TIME_RANGE` — an element's `start`/`end` (or `animation.duration`) doesn't fit the element's own
    window or the video's real, probed duration.
  - `VALIDATION_ERROR` — the render completed but the output failed its own post-render check (resolution,
    duration, video stream); treat as a real failure, not a transient one, even though media was written.

## What this Skill is not

It does not reason about content, pick templates, decide timing, or judge whether a design "looks good". Those
are `video-production-agent`'s job. This Skill only renders a typed, already-decided Graphics Document.
