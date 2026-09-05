# motion-graphics-skill

Deterministic motion-graphics **rendering execution** Skill for the AI Video Production Ecosystem. It renders a
typed, already-decided Graphics Document — titles, lower thirds, free-form text overlays, and image/logo overlays
— onto a video, through [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill), and reports provenance.

It is **not** an AI agent. It never decides what to show, when to show it, or how it should look; it never
accepts or constructs an arbitrary ffmpeg filter, shell command, or expression.

## Quick start

Requires Python 3.9+ and a working `ffmpeg`/`ffprobe` on `PATH`, plus a checkout of
[`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill) (see [`docs/ffmpeg-skill.md`](docs/ffmpeg-skill.md) for
how it's located).

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
`graphics.py` (title / lower-third templates) or `overlay.py` (text / image overlays). See
[`docs/ffmpeg-skill.md`](docs/ffmpeg-skill.md).

## Graphics model

- **GraphicsDocument**: one input video, one output path, a list of `GraphicsElement`, and render options
  (`reuse_intermediates`, `crf`, `preset`).
- **GraphicsElement**: `id`, `type`, `start`/`end` (seconds, half-open, both finite), type-specific typed
  `parameters`, and an optional `animation`.
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

`shape`, `chapter`, `bug`, `progress`, and `countdown` are intentionally **not implemented** in this contract (see
`unsupported_element_types` in the contract, and STEP 24 of the design brief) — they are not published as
supported by `doctor`/`contract` because there is no working renderer behind them yet.

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

## Rendering

```
Graphics Document (typed, validated)
  -> render plan (elements sorted by (start, id))
  -> ffmpeg-skill invocation per element (graphics.py or overlay.py; typed argv only)
  -> output validation (exists, non-empty, probed: video stream, resolution unchanged, duration, sha256)
  -> structured response + provenance
```

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
