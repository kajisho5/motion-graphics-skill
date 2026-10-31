<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/app-icon-dark.png">
    <img src="brand/app-icon.png" width="128" alt="motion-graphics-skill app icon">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/logo/motion-graphics-skill-wordmark-white.svg">
    <img src="brand/logo/motion-graphics-skill-wordmark-black.svg" height="48" alt="motion-graphics-skill">
  </picture>
</p>

<p align="center"><b>Deterministic motion-graphics rendering execution Skill for the AI Video Production Ecosystem.</b><br>
Renders a typed, already-decided Graphics Document onto a video through ffmpeg-skill, and reports provenance.
It is not an AI agent: it never decides what to show, and never accepts an arbitrary ffmpeg filter or shell command.</p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a> ·
  <a href="#responsibility-boundary">Responsibility boundary</a> ·
  <a href="#contract--agent-integration">Contract / agent integration</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="docs/testing.md">Testing</a>
</p>

<p align="center">
  <a href="https://github.com/kajisho5/motion-graphics-skill/actions/workflows/tests.yml"><img src="https://github.com/kajisho5/motion-graphics-skill/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"></a>
  <a href="https://github.com/sponsors/kajisho5"><img src="https://img.shields.io/badge/sponsor-%E2%9D%A4-ea4aaa?logo=githubsponsors" alt="Sponsor"></a>
</p>

Deterministic motion-graphics **rendering execution** Skill for the AI Video Production Ecosystem. It renders a
typed, already-decided Graphics Document — titles, lower thirds, free-form text overlays, image/logo overlays,
video-on-video picture-in-picture/chroma-key overlays, persistent corner "bug" watermarks, chapter chips, a bottom
progress bar, and a countdown — onto a video, through
[ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill), and reports provenance.

It is **not** an AI agent. It never decides what to show, when to show it, or how it should look; it never
accepts or constructs an arbitrary ffmpeg filter, shell command, or expression.

## Quick start

Requires Python 3.9+ and a working `ffmpeg`/`ffprobe` on `PATH`, plus a checkout of
[`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill) of any version: what it can do is detected from its own
contract rather than its version number (see [`docs/ffmpeg-skill.md`](docs/ffmpeg-skill.md) for how it's located
and checked).

```bash
pip install -e .
motion-graphics doctor --json --ffmpeg-skill /path/to/ffmpeg-skill   # confirm the environment first
```

A request document (`document.video`/`document.output` are always required; every element needs a unique `id`):

```json
{
  "schema": "motion-graphics/request@1",
  "video": {"path": "input.mp4"},
  "output": {"path": "out/output.mp4"},
  "elements": [
    {"id": "title1", "type": "title", "start": 0, "end": 3, "parameters": {"title": "Episode 12", "subtitle": "The math of video"}},
    {"id": "logo1", "type": "image_overlay", "start": 0, "end": 999, "parameters": {"image_path": "logo.png", "position": "top-right"},
     "animation": {"kind": "fade", "parameters": {"duration": 1.0}}}
  ]
}
```

```bash
motion-graphics validate request.json --json     # structural check only, touches no files
motion-graphics plan request.json --json --workspace .        # dry run: resolves/probes inputs, writes no media
motion-graphics run request.json --json --workspace .         # renders, returns output path + sha256 + provenance
```

Every command prints exactly one JSON document on stdout and a non-zero exit code from a fixed table on failure
(`motion-graphics contract --json` → `errors.exit_codes`) — see [Contract / agent integration](#contract--agent-integration)
below for how a caller is expected to use these.

## Responsibility boundary

```
video-production-agent                         motion-graphics-skill
  Observation -> Event/Context -> Inference       typed request
  -> Policy/Preference/Constraint -> Decision      -> graphics validation
  -> ProductionPlan -> Project IR -> Compiler       -> timeline validation
                                    |                -> animation validation
                                    v                -> deterministic rendering (delegated to ffmpeg-skill)
                              motion-graphics-skill   -> output validation
                                                       -> structured response + provenance
```

| Skill | Responsibility |
|---|---|
| `video-production-agent` | Decides *what* to show, *when*, and *how it should look* (design/timing/content decisions). |
| **`motion-graphics-skill`** (this repo) | Renders a *given* graphics specification deterministically. No design judgement. |
| `video-editing-skill` | Cuts, trims, and assembles video. |
| `color-grading-skill` | Color grading. |
| `subtitle-skill` | Subtitle generation/translation. |
| `ffmpeg-skill` | The deterministic media-processing engine every skill above delegates actual ffmpeg execution to. |

This skill never talks to ffmpeg/ffprobe directly: every render is a typed, argv-only call into ffmpeg-skill's
`graphics.py` (the title, lower-third, bug, chapter, progress, countdown, sticker, hook and meme templates) or `overlay.py`
(text / image / video overlays). See
[`docs/ffmpeg-skill.md`](docs/ffmpeg-skill.md).

## Graphics model

- **GraphicsDocument**: one input video, one output path, a list of `GraphicsElement`, and render options
  (`reuse_intermediates`, `crf`, `preset`, `audio_stream`, `platform`).
- **GraphicsElement**: `id`, `type`, `start`/`end` (seconds, half-open, both finite), type-specific typed
  `parameters`, and an optional `animation`.
- **Colours** (`color` parameters): a colour name from the contract's `colors.named` table (FFmpeg's own 140 names, any
  case) or bare `RRGGBB` hex, optionally `@alpha` -- except on the template elements (`title`, `lower_third`, `bug`,
  `chapter`, `progress`, `countdown`, `sticker`, `hook`), whose colour parameters say `"alpha": false`: ffmpeg-skill's
  `graphics.py` takes opaque `RRGGBB` only, so this skill sends a name as its `RRGGBB` and refuses `@alpha` there before
  anything renders (`UNSUPPORTED_OPERATION`, naming the element). See ADR-20 in [`docs/decisions.md`](docs/decisions.md).
- **Animation**: `{"kind": "fade", "parameters": {"duration": <seconds>}}` — a linear alpha fade in at `start` and
  out at `end`, over one shared duration. That is the only configurable animation this contract exposes (see
  [`docs/decisions.md`](docs/decisions.md) for why "slide", "move" and "scale" animations are not implemented).

Supported element types today — see `motion-graphics skill --json` (`element_types`) for the authoritative,
generated list:

| type | delegate | built-in animation |
|---|---|---|
| `title` | `ffmpeg-skill/graphics --template title` | fixed 0.3s fade in/out (not configurable) |
| `lower_third` | `ffmpeg-skill/graphics --template lower-third` | fixed slide-in/out + fade (not configurable) |
| `text_overlay` | `ffmpeg-skill/overlay --text` | none, or a configurable `fade` |
| `image_overlay` | `ffmpeg-skill/overlay --image` | none, or a configurable `fade` |
| `video_overlay` | `ffmpeg-skill/overlay --video` (+ `--chromakey*`) | none at all -- `--video` never applies a fade |
| `bug` | `ffmpeg-skill/graphics --template bug` | fixed 0.3s fade in/out (not configurable) |
| `chapter` | `ffmpeg-skill/graphics --template chapter` | fixed 0.3s fade in/out (not configurable) |
| `progress` | `ffmpeg-skill/graphics --template progress` | none at all -- driven entirely by its own start/end fill, no alpha |
| `countdown` | `ffmpeg-skill/graphics --template countdown` | fixed, per-digit 0.5s pulse (not configurable) |
| `sticker` | `ffmpeg-skill/graphics --template sticker` | fixed pop-in (0.25s fade up + rise) and 0.3s fade-out (not configurable) |
| `hook` | `ffmpeg-skill/graphics --template hook` | none at all -- a bar that empties across the window, no fade |
| `meme` | `ffmpeg-skill/graphics --template meme` | none at all |
| `shape` | `ffmpeg-skill/background` (the rectangle as a clip), then `ffmpeg-skill/overlay --video` | none at all -- `--video` never applies a fade |

`bug` is a persistent text watermark in one of the four corners (e.g. `"LIVE"` or `"@handle"`) — its `position` is
a closed vocabulary of just those four corners (`top-left`/`top-right`/`bottom-left`/`bottom-right`), a strict
subset of the 9-way `position` type `text_overlay`/`image_overlay` use, because ffmpeg-skill/graphics's `bug`
template has no `{x, y}` support at all.

`chapter` is a small chip in a corner (e.g. `"Part 2 — Setup"`) — same closed-vocabulary `position` as `bug`
(default `bottom-left` instead of `bug`'s `top-right`), but its overridable color is `primary_color` (the chip's
background), not `text_color`: ffmpeg-skill/graphics's `chapter` branch always draws chip text in the brand
background color regardless of any color override, so `text_color` is not an accepted parameter for `chapter`.

`progress` is a thin bar along the bottom of the frame that fills left-to-right from empty to full over the
element's own `start`/`end` window. It has no `title`, no `position` (always full-width along the bottom), and no
animation — the fill itself, driven by the timeline, is the only motion it has. Its only parameter is
`primary_color` (the fill color); the track behind it is a fixed brand background color, not overridable.

`countdown` draws big centered numbers counting down from `count_from` (default 5, 1-60) to 0, evenly spaced
across the element's own `start`/`end` window, each with a brief built-in "pulse" (a fixed opacity dip at the
start of its segment) — distinct from `fade` and not configurable. No `title`, no `position` (always centered);
its only parameter besides `count_from` is `primary_color` (digit color).

`sticker` is a filled chip of short text in one of the four corners (`"NEW"`, `"LIVE NOW"`), same closed-vocabulary
`position` as `bug` (default `top-right`); `primary_color` is the chip, and there is no `text_color` because the template
draws the text in its fixed dark colour.

`hook` is the short-form opener: a full-width band with a large centered `title` (optional `text_color`/`primary_color`) and a
thin bar along the top that empties across the element's own `start`/`end` window, so the card stays up for exactly that window.

`meme` draws heavy white upper-case lines with a black outline at the top and bottom margins: `top` and `bottom`, at least
one of them. Colours and position are fixed by the template.

`sticker`, `hook` and `meme` need an ffmpeg-skill newer than 0.12.5 (they arrived together with `--platform`). `doctor` reports
them `unsupported` on an older checkout, and a request for one is refused before anything renders (`TOOL_ERROR`,
`retryable: false`, `details.reason: ffmpeg_skill_lacks_template`) — see `docs/decisions.md` ADR-19.

`shape` is a solid-colour rectangle: `width` and `height` (even pixels, 2-7680; ffmpeg-skill's background refuses odd sizes),
`color` (opaque: a name or RRGGBB, no `@alpha`), `position` (default `center`), `margin` and `opacity` (default 1). Use it for a
backing plate behind text, a letterbox bar or a highlight box. It is the one element type that takes two ffmpeg-skill calls in
its stage (`docs/decisions.md` ADR-22): `ffmpeg-skill/background` generates the rectangle as a clip lasting until the element's
`end`, then `ffmpeg-skill/overlay --video` composites it in the element's window. The clip is a derived input: it has its own
identity (size, colour, duration, encoder settings, tool versions; never a path), lives in the document's work dir, and is reused
when its manifest and sha256 match. The response's operation for the element names it under `derived`. Rectangles only: no rounded
corners, border, rotation, gradient or fade. A checkout without `background.py` (ffmpeg-skill before 0.11.0) reports `shape`
`unsupported` in `doctor`, and a request for one is refused before anything renders (`TOOL_ERROR`, `details.reason:
ffmpeg_skill_lacks_tool`).

## Platform safe zones

`options.platform` (`broadcast`, `facebook`, `instagram`, `linkedin`, `podcast`, `reels`, `shorts`, `tiktok`, `twitter`,
`youtube`, `youtube-shorts`) is passed to every `graphics` and `overlay` stage as ffmpeg-skill's `--platform`: each edge's margin
becomes that destination's safe zone (`broadcast` is 5 % on every edge; a top-left logo clears TikTok's status bar). A graphics
template follows it directly. An overlay element follows it when the request gives no `margin`; a `margin` you give is sent and wins,
except that ffmpeg-skill treats an explicit `24` (its own default) as not given. Explicit `{x, y}` positions are unaffected.

```json
{"options": {"platform": "broadcast"}}
```

An ffmpeg-skill without `--platform` refuses such a request before anything renders (`ffmpeg_skill_lacks_platform_flag`).

## Video-on-video / picture-in-picture / chroma-key

`video_overlay` composites a second video as a PiP layer, mirroring `image_overlay`'s position/margin/scale/opacity
model almost exactly (`video_path` instead of `image_path`, the same 9-way `position` type) plus optional
`chromakey`/`chromakey_similarity`/`chromakey_blend` (green-screen removal). `video_path` goes through the same
PathPolicy as every other input and is probed up front (alongside every other asset) to confirm it actually has a
video stream — there is no fixed extension whitelist here the way there is for `image_overlay` (a PiP layer's
container can legitimately be almost anything `ffmpeg-skill/overlay --video` itself accepts). Only this element's
own audio track, if any, is dropped; the base video's audio is unaffected. It never accepts a configurable
`fade`: `ffmpeg-skill/overlay`'s `--video` branch never applies one at all (see `docs/decisions.md` ADR-15).

## Timeline validation

- `start >= 0`, `end > start`, both **finite** (`NaN`/`Infinity` are rejected as `INVALID_TIME_RANGE`).
- Element `id`s must be unique within a document (`DEPENDENCY_ERROR` on a duplicate).
- Elements are rendered in **(start, id) order**, regardless of the order they were listed in the request — an
  execution-order decision, not a correction of the caller's data (`docs/decisions.md`).
- `render` (not `validate`) additionally rejects any element whose `end` is beyond the real, probed video
  duration.
- Nothing here is silently repaired: an invalid timeline is rejected before anything renders.

## Text / lower third / fonts

Text-bearing parameters (`text`, `title`, `subtitle`, `name`) are plain strings passed as typed CLI arguments to
ffmpeg-skill, which escapes them for `drawtext`; **no HTML, CSS, or JavaScript is ever executed** — there is no
such engine in this pipeline.

Fonts are a closed registry (`font_id` in `system:dejavu-sans` / `system:dejavu-serif` / `system:dejavu-sans-mono`)
or a `font_file` resolved through the same PathPolicy as every other input, with an allowed-extension check
(`.ttf`/`.otf`/`.ttc`) and a `font_file_hash` (sha256) recorded in provenance. An unknown `font_id` or missing
`font_file` is rejected (`MISSING_INPUT`/`INVALID_INPUT`) — never silently substituted for a different, available
font. No font is specified at all -> the explicit default `system:dejavu-sans` is used and recorded as such.

The default DejaVu fonts do not include CJK glyphs — Unicode text renders (this skill never rejects or mangles
it), but non-Latin scripts need a `font_file` pointing at a CJK-capable font (e.g. Noto Sans CJK) to display
correctly. This is a font *content* limitation, not a rendering bug; it is the same behavior `ffmpeg-skill/overlay`
documents for its own `--font-file` option.

## Image / logo overlay

`image_overlay.parameters.image_path` goes through PathPolicy (workspace/allowed-roots/symlink checks), must be
`.png`/`.jpg`/`.jpeg`, and its sha256 is recorded in provenance. It is never treated as anything executable.

## Multi-audio-track sources

`document.options.audio_stream` (optional integer, 0-based) selects which audio track of `document.video.path`
survives, threaded through to `--audio-stream` on every `graphics`/`overlay` invocation in the pipeline — useful
for a dubbed-language or M&E-stem source that carries more than one audio track. Omitted entirely when not set
(both delegate tools already default to track 0). Bounded structurally to `[0, 63]` to catch a typo; whether a
*real* input actually has that many tracks is something only `ffmpeg-skill/{graphics,overlay}` can know, so an
out-of-range value for the real input is a `TOOL_ERROR` from the delegate tool itself, never a guess made here
(`docs/decisions.md` ADR-16).

## Rendering

```
Graphics Document (typed, validated)
  -> render plan (elements sorted by (start, id))
  -> ffmpeg-skill invocation per element (graphics.py or overlay.py; typed argv only)
  -> output validation (exists, non-empty, probed: video stream, resolution unchanged, duration, sha256)
  -> structured response + provenance
```

Each stage's tool response may report `dropped_non_av_streams: true` (ffmpeg-skill 0.12.1) when that tool's own
attempt to preserve the source's subtitle/data stream(s) failed and it fell back to a video+audio-only re-encode
for that stage. This Skill reads it per stage (`operations[].dropped_non_av_streams`) and surfaces it as a
top-level `warnings[]` entry whenever it was `true` — never silently discarded (`docs/decisions.md` ADR-17).

This skill **never** accepts or builds a raw ffmpeg filter/`filter_complex`/command/argv/shell/executable/env from
a request — those field names are rejected recursively anywhere in the request document
(`FORBIDDEN_KEYS` in `model.py`).

## Contract / agent integration

`motion-graphics contract --json` (alias `skill --json`) is generated from the same tables the code runs on
(`model.ELEMENT_TYPES`, `model.ANIMATION_KINDS`, `errors.ERROR_TABLE`) — nothing in it is hand-maintained, so it
can never claim support the renderer doesn't actually have. A caller (`video-production-agent` or otherwise)
should:

1. Run `contract --json` once; check `element_types`/`animations` against what the request needs, and
   `unsupported_element_types`/`unsupported_animations` for anything it must not ask for.
2. Run `doctor --json` to confirm the environment: every capability is `supported` / `unsupported` / `unknown`
   (never a guess — `unknown` means "not detected either way, verified per run instead").
3. `validate` a request first (structural only, no file access) if the caller built it programmatically.
4. `plan` (or `run --dry-run`) to resolve and probe inputs without writing media, if it wants to catch a missing
   asset/font or an out-of-range timeline before committing to a render.
5. `run`, and read `error.code` + `error.retryable` on failure (`contract.errors`) rather than parsing messages —
   `TOOL_ERROR`/`CANCELLED` are safe to retry as-is; every other code means the request itself needs to change.

`validate`, `plan`, and `run` return different top-level keys (`validation`, `plan`, and `output`/`operations`
respectively) — `contract.response.success` documents all three shapes individually so a caller never has to
guess which one it's looking at.

`provides` lists this Skill's element types by their cross-repository Capability id: `title` ->
`motion_graphics.title_card`, `lower_third` -> `motion_graphics.lower_third`, `image_overlay` ->
`motion_graphics.overlay` (that matrix's id), `text_overlay` -> `motion_graphics.text_overlay` (one id per element
type; until 2026-10-30 it shared `motion_graphics.overlay`), `bug`/`chapter`/`progress`/`countdown` -> `motion_graphics.bug`
/ `motion_graphics.chapter` / `motion_graphics.progress` / `motion_graphics.countdown` respectively, and
`shape` -> `motion_graphics.shape` (ADR-22), `video_overlay` -> `motion_graphics.video_overlay` (its own id, not shared with `motion_graphics.overlay` — a
video-on-video PiP/chroma-key layer is a materially different capability, see `docs/decisions.md` ADR-15). All
five of these are this repository's own provisional ids — that matrix predates all five element types'
implementation here (see `docs/decisions.md` ADR-11/ADR-12/ADR-13/ADR-14/ADR-15). Each entry also carries its
`tool_id` (always `motion-graphics/run`, this Skill's one execution tool) and a `lifecycle`. This anticipates
`kajisho5/AI-video-production-OS`'s Capability registry (`docs/CAPABILITY_MATRIX.md`, `registry/contract.py`, as
of this writing on that repository's not-yet-merged architecture branch, not its `main`), so a registry can
eventually resolve "who provides `motion_graphics.title_card`" without hardcoding this repository. It is additive
and derived from `model.ELEMENT_TYPES`; see `docs/decisions.md` ADR-10.

`contract_version` (the contract document's shape, `1`) and `dependencies` follow the OS registry's shape:
`dependencies` names `ffmpeg-skill` with a `version_range` of its `contract_version` (`>=1.0 <3.0`: 1.0 up to ffmpeg-skill 2.x, 2.0 in 3.x,
`range_of: "contract_version"`) and the ffmpeg-skill tools used. It is a declaration for the registry;
compatibility is still decided by the flags present (ADR-18).

## Security

- No `shell=True`, no arbitrary executable, no arbitrary ffmpeg args/filters, no command injection, no
  JavaScript/HTML/CSS execution, no environment injection.
- `subprocess.Popen` is called from exactly one place (`adapter.py`), with an argv list, a minimal inherited
  environment, its own process group, and a timeout.
- Only `ffmpeg-skill/{probe,graphics,overlay}.py` may ever be started (`adapter.TOOLS_USED`).
- See [`docs/security.md`](docs/security.md) for the full boundary table.

## PathPolicy

Every path this skill touches — the input video, image/logo assets, custom font files, and the output — goes
through the same `PathPolicy` (`security.py`): workspace confinement, `allowed-input-roots`, symlink-escape
resolution, output-may-not-be-input, and cross-platform-safe file names (Windows reserved device names, trailing
dot/space, control characters, `-`-prefixed names).

## Provenance / determinism / reuse

Every response carries a `provenance` block: the source video's identity (path, sha256, duration, resolution),
per-element asset/font identities, the full operation chain (type, tool, parameters, input/output hashes), and the
final output's sha256. Identities are sha256 over canonical JSON and never include timestamps, UUIDs, or absolute
paths (`canonical.py`).

Non-final render stages are cached under `<workspace>/.motion-graphics/<document_id>/<identity[:16]>.<ext>` and
reused when a matching manifest and an unchanged sha256 are both found; the final requested output is always
(re)written and re-validated, whether or not any earlier stage was reused (`docs/decisions.md`).

## Testing

See [`docs/testing.md`](docs/testing.md). `python -m pytest -q` requires an `ffmpeg-skill` checkout (see
`MOTION_GRAPHICS_FFMPEG_SKILL_DIR` / `vendor/ffmpeg-skill`) and a working `ffmpeg`/`ffprobe` on `PATH`.
