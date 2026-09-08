# Testing

```
tests/
├── conftest.py           -- fails the whole session loudly if no usable ffmpeg-skill checkout is found
├── fixtures/
│   └── generate.py        -- synthesizes test media with real ffmpeg (testsrc video incl. a dual-audio-track
│                              variant, a short PiP clip, a PNG logo, sample text)
├── test_unit.py           -- model validation, timeline rules, canonical JSON / identity determinism, error table, PathPolicy, fonts, dropped_non_av_streams plumbing (fake FfmpegSkill, no real ffmpeg)
├── test_security.py       -- shell non-use, tool allowlist, forbidden-field rejection (incl. nested), path traversal / symlink escape, env allowlist, injection payloads as *content*
├── test_contract.py       -- contract <-> implementation consistency, contract/doctor are deterministic and valid JSON, skill == contract alias
└── test_integration.py    -- real ffmpeg-skill + real media: title, lower_third, text_overlay, image_overlay, video_overlay (PiP + chroma-key), bug, chapter, progress, countdown, fade, audio_stream track selection, multi-element pipelines, reuse + tamper detection, Unicode text, cancellation, CLI exit codes
```

Run everything:

```bash
pip install -e .
python -m pytest -q
```

Integration and contract tests need a real `ffmpeg-skill` checkout with a working `ffmpeg`/`ffprobe` on `PATH`.
Point at one with `MOTION_GRAPHICS_FFMPEG_SKILL_DIR=/path/to/ffmpeg-skill`, or clone it to `vendor/ffmpeg-skill`
next to this repository. `conftest.py` fails the session (not skips) if none is found, mirroring
`audio-production-skill`'s policy: a missing dependency should be loud, not a quietly-green suite.

## What "real media E2E" means here (STEP 19-20)

`test_integration.py` renders onto an actual synthesized video (`ffmpeg -f lavfi -i testsrc=...`) through the real
`ffmpeg-skill` scripts and a real `ffmpeg`/`ffprobe`, then asserts, never just an exit code:

- the output file exists and is non-empty
- its sha256 is stable across two identical runs when nothing changed (determinism)
- `ffprobe` (via `ffmpeg-skill/probe`) reports a video stream, the source resolution, and the expected duration
- for `image_overlay`/`video_overlay`: comparing pixel statistics (luma) between a region sampled *with* the
  overlay and the same region on an unmodified render (frame sampling / pixel-statistics check, not a subjective
  "looks right" judgement) — `video_overlay`'s chroma-key test compares an opaque PiP layer against a keyed one
  the same way, proving the keyed-out region reveals the base video's own content rather than just asserting no
  exception was raised
- for `document.options.audio_stream`: rendering against a fixture with two distinguishable audio tracks (one
  silent, one an audible tone) and comparing `mean_volume` (ffmpeg's own `volumedetect` filter) on the *rendered
  output* between the two selections — the only way to tell which source track actually survived, since `ffprobe`
  on the output alone only ever shows the one audio stream the tool mapped
- reuse: a second run with an unchanged specification hits the intermediate cache (`status: "reused"`) for every
  non-final stage; corrupting a cached intermediate's bytes forces a re-render (tamper detection)

This is not a subjective visual-quality check (STEP 20 explicitly rules that out) — it is objective, comparable
evidence that the pixels actually changed where and when the specification said they should.

## Security tests

`test_security.py` sends shell/command/argv/filter-injection **payloads as element `text` content** (e.g.
`"; rm -rf / #"`, `"$(reboot)"`, `"<script>alert(1)</script>"`) and asserts the render still succeeds and produces
a video — because these are just characters ffmpeg-skill's `drawtext` will escape and draw, not something that
should be rejected as unsafe. It separately asserts that the *field names* that could carry an actual command
(`FORBIDDEN_KEYS`) are rejected regardless of where they appear in the document.
