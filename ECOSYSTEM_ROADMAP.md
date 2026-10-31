# Roadmap — motion-graphics-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

pyproject v0.2.0 but `VERSION` in `__init__` is 0.1.0, so the contract reports 0.1.0. 12 elements (title, lower_third, text/image/video overlay, bug, chapter, progress, countdown, sticker, hook, meme); `shape` (a solid rectangle, two ffmpeg-skill calls in one stage) added by [#33](https://github.com/kajisho5/motion-graphics-skill/pull/33). 241 tests, CI on 3 OSes.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| タイトル・テロップ | done |  |

## Wiring

Agent accepts only 0.1.x and exposes title, lower_third, text_overlay, image_overlay; the other 8 elements are unreachable. Delegates to ffmpeg-skill probe, graphics, overlay.

## Steps

- [x] **1. Single-source the version** — VERSION from package metadata; release script covers it; coordinate with the agent's version gate — [#28](https://github.com/kajisho5/motion-graphics-skill/pull/28), [#32](https://github.com/kajisho5/motion-graphics-skill/pull/32)
  - Done when: contract version == pyproject; test
- [x] **2. Distinct provides ids** — text_overlay and image_overlay both map to motion_graphics.overlay
  - Done when: no duplicate ids
- [x] **3. contract_version + dependencies**
  - Done when: OS schema check
- [x] **4. (optional) `shape` element** — via ffmpeg-skill background + overlay; ADR on two calls per element ([#33](https://github.com/kajisho5/motion-graphics-skill/pull/33), ADR-22)
  - Done when: integration test
- [x] **5. ffmpeg-skill 3.0.0** — accept its contract_version 2.0; Python 3.9 CI against the last 2.x (ADR-23) ([#PRNUM](https://github.com/kajisho5/motion-graphics-skill/pull/PRNUM))
  - Done when: CI passes against 3.0.0 and the range includes it

## Boundaries

video_overlay (PiP) overlaps ffmpeg-skill's Bロール・画面合成; titles also exist in hyperframes / remotion templates and ffmpeg-skill graphics. ecosystem.json keeps タイトル・テロップ here.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
