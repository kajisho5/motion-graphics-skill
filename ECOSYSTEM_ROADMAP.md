# Roadmap — motion-graphics-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

0.2.19, version single-sourced (step 1). 15 element types: title, lower_third, text/image/video overlay, bug, chapter, progress, countdown, sticker, hook, meme, `shape` ([#33](https://github.com/kajisho5/motion-graphics-skill/pull/33)), `rendered_layer` (step 7) and `logo` (step 11). Style presets and brand kit (step 6), text fit and CSV lower thirds (step 9), YouTube reserved regions (step 10), brand themes (step 11), the `kit@1` brand kit reader (step 14), per-element transparent layer export (step 8). CI on 3 OSes.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| タイトル・テロップ | done | titles burned into a programme; title and stinger material files are hyperframes-skill's |
| ロゴ・PiP・クロマキー合成 | done | added by the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O10): `logo`, `image_overlay` as a graphic, `video_overlay` with `chromakey` (ADR-15). Plain still compositing as part of an edit stays video-editing-skill's `静止画オーバーレイ` |
| 動くグラフィックの配置 | done | added by the same decisions (O1, O10): `rendered_layer` (step 7, ADR-25) is the animated-overlay path; recorded as such in step 12 |

## Wiring

Agent wires all 12 elements with every parameter, first from a pinned 0.2.8 contract (video-production-agent#82, ADR-064), and `shape` (video-production-agent#93); the agent's pin is 0.2.19 today. `motion.image`, `motion.logo`, `motion.video` and `motion.layer` reach `motion-graphics/run`. Delegates to ffmpeg-skill probe, graphics, overlay, and background for `shape` (ADR-22, docs/ffmpeg-skill.md).

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
- [x] **6. Style presets for the 13 elements** — named presets (swiss, minimal, neon, corporate, wafu, news) as typed bundles of the parameters the elements already have (colours, font_id, box, margins, fade), resolved from a brand kit when one is given; `contract --json` lists them. No new animation. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：「企業カラーで」「ニュース風に」の指示を、毎回色とフォントを並べずに通せる
  - Done when: each preset is a contract entry with a test; a preset plus a brand kit uses the kit's colours
  - Done (ADR-24): `style_presets` in the contract lists the six presets, their roles and what each element type takes; `element.style` / `options.style` / `options.brand_kit` in the request; explicit > kit > preset; `tests/test_styles.py` tests each preset for all 13 types and a preset plus a kit
- [x] **7. Rendered-layer element (animated graphics placed here)** — `rendered_layer`: a pre-rendered alpha clip (hyperframes-skill or remotion-ffmpeg-skill output: ProRes 4444 / VP9 alpha) placed at a time and position through ffmpeg-skill overlay `--video`, with its IN / LOOP / OUT segments when it has them (the loop repeated to the element's length). Animation beyond `fade` stays out of this skill, as today; typed overlay keyframes in the engine are on hold ([ffmpeg-skill#425](https://github.com/kajisho5/ffmpeg-skill/issues/425)), and this element is the way meanwhile. A stinger between two clips is not placed here: video-editing-skill's stinger transition cuts under it
  - なぜ要るか：動きのあるテロップやスティンガーを、焼き込み工程の中で他の要素と同じタイムラインに並べられる
  - Done when: a lower third with in 0.5 s / loop / out 0.5 s placed for 6 s shows the in, a looped middle and the out at the right frames
  - Done (ADR-25): `rendered_layer` with `in_duration` / `out_duration` / `loop`, one ffmpeg-skill overlay `--place` per piece; `tests/test_rendered_layer.py` renders a 3 s ProRes 4444 layer (in 0.5 s, loop 2 s, out 0.5 s) for 6 s and reads the in, two loop repeats at the same phase, the cut third repeat and the out back from the frames. VP9 alpha WebM is refused: ffmpeg-skill overlay decodes it without its alpha
- [x] **8. Layer export** — beside the burned programme, each element rendered alone on transparent with the same timing, one file per element and a manifest (element id, type, start, end, box), so the graphics can be rebuilt in After Effects or an NLE (nle-bridge-skill places them on upper tracks)
  - なぜ要るか：テロップだけ差し替えたい・NLE で組み直したいという納品後の修正に、再レンダーなしで応えられる
  - Needs: ffmpeg-skill alpha masters (Velisa ROADMAP proposal 5, accepted: [ffmpeg-skill#417](https://github.com/kajisho5/ffmpeg-skill/issues/417)) for ProRes 4444 / PNG sequence output
  - Done when: the layers composited over the clean programme equal the burned programme within a stated PSNR
  - Done (ADR-30, superseding ADR-26): `options.layer_export {directory, alpha_codec, alpha_mode}`; one transparent canvas (ffmpeg-skill 3.2.3 `background --transparent`), each element drawn on it by the call that burned it with `--keep-alpha`; `<id>.mov` per element and `manifest.json`, each layer's `alpha` check and `verified` recorded. `tests/test_layer_export.py` composites the layers over the clean video with ffmpeg-skill overlay: 34.1 dB average PSNR against the burned programme (stated minimum 30 dB; the clean video alone 15.2 dB). Older checkouts refuse the option up front
- [x] **9. Name fit for lower thirds, any language** — `lower_third` / `title` text that does not fit its box is shrunk down to a stated minimum and broken at the language's own break opportunities (Unicode UAX #14; phrase breaks with BudouX, Apache-2.0, where it supports the language), right-to-left text laid out by its direction; the size used and the break are reported, the text is never cut. Regional rules are opt-in profiles, for example Japanese kinsoku from subtitle-skill's locale profiles. A CSV of names and affiliations renders one element per row
  - Why: "lower thirds for 10 speakers from this CSV" fits long names and affiliations in any language（なぜ要るか：長い氏名・所属も多言語ではみ出さずに揃う）
  - Done when: a 10-row CSV fixture with English, Arabic and Japanese rows, one with a 40-character affiliation, renders 10 lower thirds within the box; the Japanese row with the kinsoku profile has no line starting with a closing bracket
  - Done (ADR-27): `elements[].fit` on lower_third / title (shrink to `min_scale`, then UAX #14 breaks; `ja-kinsoku` profile; `rtl` reported) and `motion-graphics lower-thirds <csv>`; `tests/test_text_fit.py` renders `tests/fixtures/speakers.csv` (10 rows: en / es / ar / ja, a 40-character affiliation, two kinsoku rows) and measures every lower third within the 300 px box. Not done: BudouX phrase breaks (no dependency added; the rule-based breaks only)
- [x] **10. End-screen and card areas (YouTube)** — YouTube Studio lets the editor move end-screen elements, so the reserved regions are an input: the intended element positions for the last 5-20 s (or one of the stated default layouts per aspect, which the editor then reproduces in Studio, written in the result). Card teasers are covered the same way: each card's teaser time and its reserved area (top-right by default, as an input). Every other element is kept out of those regions at those times, and a graphic that overlaps one is reported
  - Why: end-screen elements and card teasers never cover a title or a lower third（なぜ要るか：終了画面やカードとテロップが重なる事故を書き出し前に防げる）
  - Done when: a fixture places a title under an end-screen element during the end screen and another under a card teaser at its time, and both overlaps are reported; with element positions given as input, the check uses those positions
  - Done (ADR-28): `options.reserved_regions` (end screen 5-20 s with input positions or a stated default layout per aspect; cards with teaser time and area, top-right by default; `on_overlap` report or refuse); `tests/test_reserved_regions.py` reports the title under the end screen and the one under a card teaser, and uses input positions when given. Element boxes are exact for shapes and sized assets, estimated for templates (stated per overlap)
- [x] **11. Brand themes** — colours (primary, secondary, background, text), font, logo and spacing in one theme file (JSON), applied at once to lower thirds, titles, end cards and the other elements; several themes per request, one chosen; colour pairs with too little contrast are warned
  - なぜ要るか：ブランドごとの配色・書体・ロゴ・余白を毎回並べずに、テーマを切り替えるだけで全テロップに反映できる
  - Done when: two themes render the same template, and each element's colours and fonts match its theme, checked on the pixels / metadata; a low-contrast theme warns
  - Done (ADR-29): `options.themes` / `options.theme`, the `logo` element type, the theme font on every graphics template, the contrast report (`response.theme.contrast`, `theme-check`); `tests/test_themes.py` renders one document with two themes and reads each element's colours from the pixels and its font from ffmpeg-skill's report, and a low-contrast theme warns
- [ ] **12. `rendered_layer` is the animated-overlay path** — under the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O1), animated overlays are made by hyperframes-skill (segments, alpha) and placed here by `rendered_layer`; typed keyframes on ffmpeg-skill overlay ([ffmpeg-skill#425](https://github.com/kajisho5/ffmpeg-skill/issues/425)) are not needed for it. Say so where a caller looks: SKILL.md, the contract's `unsupported_animations` text (a refused `slide` / `move` / `scale` / `opacity_keyframes` names `rendered_layer` as the way), and docs/decisions.md. `rendered_layer` also reads the material's `title.json` (the Velisa spec for title metadata, hyperframes-skill step 17) for `in_duration` / `out_duration` / `loop` and the alpha mode when the caller gives the file instead of the numbers; values the caller states win
  - Why: one path for moving graphics, so nobody waits for engine keyframes or builds a second one（なぜ要るか：動くテロップの経路を一つに決め、二重実装を避ける）
  - Done when: a refused `slide` animation's error names `rendered_layer`; a hyperframes-skill lower third (ProRes 4444, `--segments`) placed from its `title.json` alone shows the in, the looped middle and the out at the same frames as with the numbers given by hand
  - Status 2026-11-13: the path itself exists (`rendered_layer`, ADR-25), and the agent runs hyperframes-skill and passes its clip to `motion.layer` with in / out / loop from the rendered segments (video-production-agent [#155](https://github.com/kajisho5/video-production-agent/pull/155), ADR-114). **Not met:** `UNSUPPORTED_ANIMATIONS` (contract `unsupported_animations`) does not name `rendered_layer`, and `rendered_layer` does not read `title.json`. Both are code and contract changes, left to this step
- [x] **13. Record the overlay boundary (logo / PiP / chroma key)** — write down, in docs/decisions.md and SKILL.md, what the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O10) settled: logos, images as graphics (fade, theme, safe zones), PiP and chroma key, and the placement of moving graphics are this skill's; plain still compositing at a named position as part of an edit is video-editing-skill's `OVERLAY`; ffmpeg-skill is the engine under both (its `Bロール合成` is the cut-away, not PiP). No contract change
  - Why: the agent and a person choosing between `edit.overlay` and `motion.image` read the same rule（なぜ要るか：静止画の重ね方の使い分けを一か所に書く）
  - Done when: the decision is in docs/decisions.md with the decision linked, and SKILL.md's overlay section points to it
  - Done ([#48](https://github.com/kajisho5/motion-graphics-skill/pull/48), ADR-32): the decision is in docs/decisions.md with the Velisa decision linked, and SKILL.md's new section "Overlays: this Skill or video-editing-skill" points to it. No contract change
- [x] **14. Brand kit reader (`kit@1`)** — `options.kit` takes a brand kit in the Velisa `kit@1` shape (a superset of ffmpeg-skill `brand.json`) and maps it onto `theme@1` and `brand_kit` by the mapping table in the Velisa spec; the mapped theme is written in the response. Explicit values > kit > preset, as today for themes. A kit key with no `theme@1` counterpart is reported, not dropped silently. [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O5)
  - Why: one brand file drives this skill and the other renderers alike（なぜ要るか：同じブランドファイルを全レンダラーで共有できる）
  - Done when: a `kit@1` fixture and the equivalent `theme@1` render the same document to the same pixels; the contrast report runs on the kit's colours
  - Done ([#47](https://github.com/kajisho5/motion-graphics-skill/pull/47), ADR-31): `options.kit` (a kit@1 or plain brand.json file) maps onto `theme@1` with all four colour roles, else onto `brand_kit` with the roles it has; only what the kit states is passed on, and every key with no counterpart is listed in `response.kit.unmapped` with the reason and warned; the mapped theme is `response.kit.theme`. `tests/test_kit.py` renders `tests/fixtures/kit_lagoon.json` and the equivalent `theme@1` to the same decoded frames with the same contrast report on the kit's colours

## Boundaries

Under the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156): burned-in titles (`タイトル・テロップ`) are here; title and stinger material files are hyperframes-skill's; remotion-ffmpeg-skill's titles stay inside its compositions (O9). Logos, PiP, chroma key and the placement of moving graphics are here; plain still compositing in an edit is video-editing-skill's; ffmpeg-skill is the engine (O10). Animated overlays are hyperframes-skill material placed by `rendered_layer` (O1).

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
