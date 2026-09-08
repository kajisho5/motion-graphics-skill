# ffmpeg-skill integration contract

`motion-graphics-skill` never talks to `ffmpeg`/`ffprobe` directly. It locates one `ffmpeg-skill` checkout
(`adapter.FfmpegSkill.locate`) and calls exactly three of its tools, always as
`[sys.executable, <dir>/scripts/<tool>.py, <typed argv...>, --json]` (never a shell, never a request-supplied
string as a flag):

| Tool | Used for |
|---|---|
| `probe` | Source video facts (resolution, duration, presence of a video stream) and output validation after every render. |
| `graphics` | `TITLE` (`--template title`) and `LOWER_THIRD` (`--template lower-third`) — built-in fade / slide + fade animation, brand-free (colours passed explicitly via `--text-color`/`--primary`, never through a `brand.json`). |
| `overlay` | `TEXT_OVERLAY` (`--text`), `IMAGE_OVERLAY` (`--image`), and `VIDEO_OVERLAY` (`--video`, with optional `--chromakey`/`--chromakey-similarity`/`--chromakey-blend` green-screen removal) — position, margin, static scale, opacity, and (text/image only) a configurable `--fade`. |

Every `graphics`/`overlay` invocation may also carry `--audio-stream` (`document.options.audio_stream`) to select
which audio track of the source survives.

## Version window

`adapter.SUPPORTED_MIN = 0.12.1`, `adapter.SUPPORTED_MAX_EXCLUSIVE = 1.0.0`, `adapter.SUPPORTED_CONTRACT_VERSION =
"1.0"`. `FfmpegSkill.info()` reads `scripts/_contract.py --json --static`, checks the version window and contract
version, and that `graphics`/`overlay`/`probe` still declare the flags in `adapter.FLAGS_USED`. Any mismatch is a
`TOOL_ERROR` (retryable) surfaced by `doctor` and refused by `run`/`plan` before anything renders. `0.12.1` is the
floor (raised from `0.9.1`) because it is the oldest release carrying every flag/field this adapter now relies on:
`--video`/`--chromakey*` on `overlay` (0.11.0), `--audio-stream` on `graphics`/`overlay` (0.12.0), and
`dropped_non_av_streams` in both tools' `--json` response (0.12.1) — the last of which is not itself a CLI flag,
so `FLAGS_USED`'s contract check alone would not have caught a stale checkout missing it.

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
  `font`, `font_file`, `audio_stream`, `json`
- `overlay`: `input`, `output`, `image`, `text`, `video`, `position`, `margin`, `start`, `end`, `fade`, `opacity`,
  `scale`, `scale_percent`, `font`, `font_file`, `font_size`, `font_color`, `border`, `border_color`, `box`,
  `box_color`, `chromakey`, `chromakey_similarity`, `chromakey_blend`, `audio_stream`, `json`

## `dropped_non_av_streams` (ffmpeg-skill 0.12.1)

`graphics`/`overlay`'s `--json` response carries `dropped_non_av_streams: bool` — `true` only when that tool's own
attempt to preserve the source's subtitle/data stream(s) failed and it fell back to a video+audio-only re-encode
for that one invocation. `adapter.ToolRun` reads it off every tool response; `executor.StageResult.to_dict()`
includes it per operation only when the field was actually present and truthy-or-falsy as a real `bool` (an older
ffmpeg-skill response that never carries the field at all leaves it out entirely, never guessed as `false`), and
`Executor.response()` adds one `warnings[]` entry per stage where it was `true`. Matches this Skill's own stated
philosophy (`docs/decisions.md` ADR-12/ADR-17): never claim support — or non-loss — that isn't backed by what the
real renderer actually reported.

## Known gaps (observed, not assumed)

- `ffmpeg-skill/overlay --fade` always fades both in and out when `--start`/`--end` are both given: there is no
  in-only or out-only mode over a bounded window (ADR-2 in `decisions.md`).
- `ffmpeg-skill/overlay`'s `--video` branch never reads `args.fade` at all (only `--image`/`--text` do): a
  `video_overlay` element cannot have a configurable `fade` animation for the same reason `title`/`lower_third`
  cannot (ADR-15 in `decisions.md`).
- `ffmpeg-skill/graphics --template title|lower-third` bakes in a fixed, non-configurable 0.3s fade (title) or
  slide+fade (lower-third): no flag exists to change the timing (ADR-3).
- Filter-capability detection through `ffmpeg-skill doctor` is unreliable on FFmpeg builds whose `-filters` output
  format changed (observed on FFmpeg 8.0+, see `ffmpeg-skill`'s own `docs/contract.md`); `doctor.py` reports those
  capabilities as `unknown`, never `unsupported`, when `ffmpeg-skill` itself reports zero filters detected.
- On some Windows FFmpeg builds, `ffmpeg-skill/overlay --font-file <absolute Windows path>` fails to parse
  (`No option name near ...`, `Invalid argument`) regardless of slash style or escaping, because ffmpeg-skill's
  own filter-path escaping round-trips the value through `pathlib.Path` and always re-normalises it to the same
  form before escaping the drive letter's colon. Worked around entirely on this skill's side (ADR-9 in
  `decisions.md`): that one invocation runs with `cwd` set to the font file's directory and `--font-file` gets
  just the bare file name, which needs no escaping.
