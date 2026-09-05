---
name: motion-graphics
description: Deterministic motion-graphics rendering execution Skill for the AI Video Production Ecosystem. Use it when a caller (normally video-production-agent) has already decided what graphic to show, when to show it, and roughly how it should look, and needs it rendered safely: title cards, lower thirds, free-form text overlays, and image/logo overlays, with built-in template animation (title/lower-third fade and slide) or a configurable linear fade (text/image overlays). Do NOT use it to decide what graphic to show, when, or how it should look (video-production-agent), to edit video (video-editing-skill), to grade color (color-grading-skill), to generate or translate subtitles (subtitle-skill), to measure or QC media (qc-skill, media-analysis-skill), or to run arbitrary ffmpeg commands or filters (it refuses them).
---

# motion-graphics-skill

Deterministic motion-graphics rendering execution for the AI Video Production Ecosystem, built on top of
[ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill). See `README.md` and `docs/` for the full contract,
graphics model, security boundary, and testing notes.

## Quick start

```bash
pip install -e .
motion-graphics doctor --json
motion-graphics validate request.json --json
motion-graphics run request.json --json --workspace .
```

## What this Skill is not

It does not reason about content, pick templates, decide timing, or judge whether a design "looks good". Those
are `video-production-agent`'s job. This Skill only renders a typed, already-decided Graphics Document.
