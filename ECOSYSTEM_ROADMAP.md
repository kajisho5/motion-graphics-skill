# Roadmap — motion-graphics-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

0.2.8, version single-sourced (step 1). 12 elements (title, lower_third, text/image/video overlay, bug, chapter, progress, countdown, sticker, hook, meme); `shape` (a solid rectangle, two ffmpeg-skill calls in one stage) added by [#33](https://github.com/kajisho5/motion-graphics-skill/pull/33). 241 tests, CI on 3 OSes.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| タイトル・テロップ | done |  |

## Wiring

Agent wires all 12 elements with every parameter, read from its pinned 0.2.8 contract (video-production-agent#82, ADR-064), and `shape` (video-production-agent#93). Delegates to ffmpeg-skill probe, graphics, overlay, and background for `shape` (ADR-22, docs/ffmpeg-skill.md).

## Steps

- [x] **1. Single-source the version** — VERSION from package metadata; release script covers it; coordinate with the agent's version gate — [#28](https://github.com/kajisho5/motion-graphics-skill/pull/28), [#32](https://github.com/kajisho5/motion-graphics-skill/pull/32)
  - Done when: contract version == pyproject; test
- [x] **2. Distinct provides ids** — text_overlay and image_overlay both map to motion_graphics.overlay
  - Done when: no duplicate ids
- [x] **3. contract_version + dependencies**
  - Done when: OS schema check
- [x] **4. (optional) `shape` element** — via ffmpeg-skill background + overlay; ADR on two calls per element ([#33](https://github.com/kajisho5/motion-graphics-skill/pull/33), ADR-22)
  - Done when: integration test
- [x] **5. ffmpeg-skill 3.0.0** — accept its contract_version 2.0; Python 3.9 CI against the last 2.x (ADR-23) ([#34](https://github.com/kajisho5/motion-graphics-skill/pull/34))
  - Done when: CI passes against 3.0.0 and the range includes it
- [ ] **6. Style presets for the 13 elements** — named presets (swiss, minimal, neon, corporate, wafu, news) as typed bundles of the parameters the elements already have (colours, font_id, box, margins, fade), resolved from a brand kit when one is given; `contract --json` lists them. No new animation. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：「企業カラーで」「ニュース風に」の指示を、毎回色とフォントを並べずに通せる
  - Done when: each preset is a contract entry with a test; a preset plus a brand kit uses the kit's colours
- [ ] **7. Rendered-layer element (animated graphics placed here)** — `rendered_layer`: a pre-rendered alpha clip (hyperframes-skill or remotion-ffmpeg-skill output: ProRes 4444 / VP9 alpha) placed at a time and position through ffmpeg-skill overlay `--video`, with its IN / LOOP / OUT segments when it has them (the loop repeated to the element's length). Animation beyond `fade` stays out of this skill, as today; typed overlay keyframes in the engine are on hold ([ffmpeg-skill#425](https://github.com/kajisho5/ffmpeg-skill/issues/425)), and this element is the way meanwhile
  - なぜ要るか：動きのあるテロップやスティンガーを、焼き込み工程の中で他の要素と同じタイムラインに並べられる
  - Done when: a lower third with in 0.5 s / loop / out 0.5 s placed for 6 s shows the in, a looped middle and the out at the right frames
- [ ] **8. Layer export** — beside the burned programme, each element rendered alone on transparent with the same timing, one file per element and a manifest (element id, type, start, end, box), so the graphics can be rebuilt in After Effects or an NLE (nle-bridge-skill places them on upper tracks)
  - なぜ要るか：テロップだけ差し替えたい・NLE で組み直したいという納品後の修正に、再レンダーなしで応えられる
  - Needs: ffmpeg-skill alpha masters (Velisa ROADMAP proposal 5, accepted: [ffmpeg-skill#417](https://github.com/kajisho5/ffmpeg-skill/issues/417)) for ProRes 4444 / PNG sequence output
  - Done when: the layers composited over the clean programme equal the burned programme within a stated PSNR
- [ ] **9. Japanese name fit for lower thirds** — `lower_third` / `title` text that does not fit its box is shrunk down to a stated minimum and broken at phrase boundaries (BudouX, Apache-2.0) with subtitle-skill's kinsoku rules; the size used and the break are reported, the text is never cut. A CSV of names and affiliations renders one element per row
  - なぜ要るか：「登壇者テロップを10人分、CSV から」で、長い所属や氏名もはみ出さずに揃う
  - Done when: a 10-row CSV fixture with a 40-character affiliation renders 10 lower thirds within the box, no line starting with a closing bracket
- [ ] **10. End-screen and card areas (YouTube)** — an `end_screen` layout that reserves the regions YouTube's end-screen elements use in the last 5-20 s and keeps every other element out of them (a stated layout per aspect), and a check that reports a graphic overlapping a reserved region
  - なぜ要るか：終了画面の要素とテロップが重なる事故を、書き出し前に防げる
  - Done when: a fixture places a title in the reserved region during the end screen and gets the overlap reported

## Boundaries

video_overlay (PiP) overlaps ffmpeg-skill's Bロール・画面合成; titles also exist in hyperframes / remotion templates and ffmpeg-skill graphics. ecosystem.json keeps タイトル・テロップ here.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
