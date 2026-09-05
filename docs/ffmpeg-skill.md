# ffmpeg-skill integration contract

`motion-graphics-skill` never talks to `ffmpeg`/`ffprobe` directly. It locates one `ffmpeg-skill` checkout
(`adapter.FfmpegSkill.locate`) and calls exactly three of its tools, always as
`[sys.executable, <dir>/scripts/<tool>.py, <typed argv...>, --json]` (never a shell, never a request-supplied
string as a flag):

| Tool | Used for |
|---|---|
| `probe` | Source video facts (resolution, duration, presence of a video stream) and output validation after every render. |
| `graphics` | `TITLE` (`--template title`) and `LOWER_THIRD` (`--template lower-third`) — built-in fade / slide + fade animation, brand-free (colours passed explicitly via `--text-color`/`--primary`, never through a `brand.json`). |
| `overlay` | `TEXT_OVERLAY` (`--text`) and `IMAGE_OVERLAY` (`--image`) — position, margin, static scale (image only), opacity, and a configurable `--fade`. |

## Version window

`adapter.SUPPORTED_MIN = 0.9.1`, `adapter.SUPPORTED_MAX_EXCLUSIVE = 1.0.0`, `adapter.SUPPORTED_CONTRACT_VERSION =
"1.0"`. `FfmpegSkill.info()` reads `scripts/_contract.py --json --static`, checks the version window and contract
version, and that `graphics`/`overlay`/`probe` still declare the flags in `adapter.FLAGS_USED`. Any mismatch is a
`TOOL_ERROR` (retryable) surfaced by `doctor` and refused by `run`/`plan` before anything renders.

## Discovery order (`FfmpegSkill.candidates`)

1. `--ffmpeg-skill <dir>` (explicit; never silently replaced by a fallback)
2. `MOTION_GRAPHICS_FFMPEG_SKILL_DIR` environment variable
3. `VIDEO_AGENT_FFMPEG_SKILL_DIR` environment variable (shared with sibling Skills in this ecosystem)
4. `~/.claude/skills/ffmpeg-skill`
5. `./vendor/ffmpeg-skill`
6. `../ffmpeg-skill`

## Flags used, by tool (`adapter.FLAGS_USED`)

- `probe`: `inputs` (positional)
- `graphics`: `input`, `output`, `template`, `name`, `title`, `subtitle`, `start`, `end`, `primary`, `text_color`,
  `font`, `font_file`, `json`
- `overlay`: `input`, `output`, `image`, `text`, `position`, `margin`, `start`, `end`, `fade`, `opacity`, `scale`,
  `scale_percent`, `font`, `font_file`, `font_size`, `font_color`, `border`, `border_color`, `box`, `box_color`,
  `json`

## Known gaps (observed, not assumed)

- `ffmpeg-skill/overlay --fade` always fades both in and out when `--start`/`--end` are both given: there is no
  in-only or out-only mode over a bounded window (ADR-2 in `decisions.md`).
- `ffmpeg-skill/graphics --template title|lower-third` bakes in a fixed, non-configurable 0.3s fade (title) or
  slide+fade (lower-third): no flag exists to change the timing (ADR-3).
- Filter-capability detection through `ffmpeg-skill doctor` is unreliable on FFmpeg builds whose `-filters` output
  format changed (observed on FFmpeg 8.0+, see `ffmpeg-skill`'s own `docs/contract.md`); `doctor.py` reports those
  capabilities as `unknown`, never `unsupported`, when `ffmpeg-skill` itself reports zero filters detected.
