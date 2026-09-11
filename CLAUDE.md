# motion-graphics-skill — maintainer state

Durable, repository-local notes for whichever Claude Code session picks this repo up next. Do not trust
conversation history; trust this file, the code, the tests, and live CI/PR state — in that order. Re-verify
anything here that looks stale before acting on it (dates are given so staleness is checkable).

Last verified: 2026-09-08, against `main` @ `a9e8d38` + an uncommitted worktree implementing issue #10 items 2-4
(`video_overlay`, `document.options.audio_stream`, `dropped_non_av_streams` surfacing) -- item 1 (`shape`)
deliberately deferred, see "Known limitations" below.

## What this repo is

A deterministic motion-graphics **rendering execution** Skill on top of `ffmpeg-skill`, for the `kajisho5` AI
Video Production ecosystem. It is not an AI agent and makes no design/timing/content decisions — see `README.md`
and `SKILL.md` for the full boundary. Contract version `0.1.0` (unchanged since #1 — every change so far has been
additive; see "Known limitations" for when a version bump would actually be required).

## Current state (verified, not assumed)

- `main` @ `a9e8d38` implements **8 element types**: `title`, `lower_third`, `text_overlay`, `image_overlay`,
  `bug`, `chapter`, `progress`, `countdown`. **An uncommitted worktree adds a 9th, `video_overlay`**, plus two
  cross-cutting fixes, closing issue #10 items 2-4 (item 1, re-investigating `shape`, is explicitly out of scope
  for that pass -- it needs a larger architectural decision about multi-tool-per-element pipelines that ADR-8
  already flags as its own design question):
  - **`video_overlay`** (`ffmpeg-skill/overlay --video`, ffmpeg-skill 0.11.0): video-on-video picture-in-picture,
    mirroring `image_overlay`'s position/margin/scale/opacity model, plus optional `chromakey`/
    `chromakey_similarity`/`chromakey_blend` (green-screen removal). Animation is `"none"` (`--video` never
    applies a fade); its own Capability id `motion_graphics.video_overlay` (not shared with `.overlay`). ADR-15.
  - **`document.options.audio_stream`** (ffmpeg-skill 0.12.0's `--audio-stream` on `graphics`/`overlay`):
    threaded through `executor._argv()` to every stage, included in stage identity. ADR-16.
  - **`dropped_non_av_streams`** (ffmpeg-skill 0.12.1): read off every tool response (`adapter.ToolRun`),
    surfaced per-operation (`StageResult.to_dict()`) and as a top-level `warnings[]` entry when `true`. ADR-17.
  - `adapter.SUPPORTED_MIN` raised `0.9.1` -> `0.12.1` (the floor now needed for all three of the above; see
    `adapter.py`'s comment and `docs/ffmpeg-skill.md`).
  - The full `contract`/`doctor`/`validate`/`plan`/`run` CLI, PathPolicy, deterministic provenance, a 9-entry
    `provides` cross-repo Capability-id field, and **313 tests** (unit/security/contract/integration, real-media
    E2E, up from 263 -- run against `ffmpeg-skill` 0.12.2 at `/home/user/ffmpeg-skill` in this environment).
    **Re-run the CI matrix (Ubuntu/macOS/Windows × Python 3.9/3.11) before merging** -- not done from this
    worktree.
  - **`shape` remains the only unimplemented element type**, and issue #10 item 1's re-investigation (chaining
    a hypothetical `background.py` + `overlay --video` into one composite "shape") was deliberately **not**
    attempted here: `executor._argv()` is a one-tool-per-element model, and a two-stage pipeline for one element
    is a real design question (per-element identity/caching currently assumes exactly one delegate tool call),
    not a small follow-up like `video_overlay` was. Needs its own decision before implementing, not a quick ADR.
- **The actual downstream consumer already exists and was verified end-to-end, repeatedly, after every single one
  of the 8 PRs above, and again for the `video_overlay` worktree above**: `kajisho5/video-production-agent` ships
  `src/video_agent/tools/motion_graphics/adapter.py` with a **pinned** `contract_0.1.0.json` and a strict
  `check_contract()`/`contract_drift()` compatibility gate. Its full `tests/test_adapter_motion_graphics.py`
  (21 tests, including `RealSkillTests`, which spins up real `ffmpeg` and drives this Skill's actual CLI, not a
  fake) passed 100% against every intermediate state of this checkout, with **zero `check_contract()` errors**
  throughout — new element types/fields show up only as informational `contract_drift()` entries (the agent's
  pinned snapshot doesn't know about them yet), never a hard failure. Re-verified directly against the live
  contract produced by this worktree (`video_overlay` added): `check_contract()` errors == `[]`, `contract_drift()`
  shows exactly one new entry (`element_types video_overlay: installed but not pinned`) — additive, as expected.
  This is the strongest available evidence the contract stays correct. **Re-run this before merging any future PR
  that touches `contract.py`, `model.ELEMENT_TYPES`, or the request/response shape** (see the recipe below) — it
  caught a real compatibility break during #4 (a new parameter type broke `check_contract()` outright; solved
  with `string`+`enum` instead, ADR-11) before it ever reached a PR.
- **`kajisho5/AI-video-production-OS`** (the "OS" repo named in ecosystem-wide prompts) still had, as of last
  check, only a placeholder README on its `main` branch — no real architecture merged there yet. The substantive
  architecture (Capability registry, `docs/CAPABILITY_MATRIX.md`, `registry/contract.py` conformance checker)
  lives on an **unmerged** branch there, `claude/ai-video-production-os-arch-*` (branch suffix will change; find
  it with `git ls-remote --heads https://github.com/kajisho5/AI-video-production-OS`). Treat anything from that
  repo as provisional until it lands on `main` — verify against the actual branch content, never assume a cited
  filename exists just because a PR description says so (this happened once already, in #2's original
  description; corrected in `f2b7a91` before merge). **Re-check whether it has merged to `main` yet** before
  trusting this paragraph or the provisional ids below.
- `contract.CAPABILITY_IDS` (all verified valid against the OS registry's `validate_provides_entry()`, all
  matching the real, unmerged `docs/CAPABILITY_MATRIX.md` for the 4 element types it actually covers):
  `title` -> `motion_graphics.title_card`, `lower_third` -> `motion_graphics.lower_third`, `text_overlay`/
  `image_overlay` -> `motion_graphics.overlay` (matrix-verified), and `bug`/`chapter`/`progress`/`countdown`/
  `video_overlay` -> `motion_graphics.{bug,chapter,progress,countdown,video_overlay}` (this repository's **own
  provisional ids** — that matrix predates all five; reconcile if the OS side ever assigns different ones once
  merged).
- Issue #10 (re-investigate `shape`; PiP/chroma-key ownership gap; missing `--audio-stream`;
  `dropped_non_av_streams` discarded) is open upstream as of last check; items 2-4 are addressed by the
  uncommitted worktree described above, item 1 deliberately deferred. No open pull requests as of last check.

## How to re-verify OS/agent compatibility

Don't take this file's word for it — regenerate the evidence:

```bash
# 1. Live contract from this checkout
PYTHONPATH=src python3 -m motion_graphics.cli skill --json > /tmp/live_contract.json

# 2. Check it against video-production-agent's pinned compatibility gate (clone it read-only if you don't have it)
git clone --depth 1 https://github.com/kajisho5/video-production-agent /tmp/vpa
cd /tmp/vpa && python3 -c "
import sys, json; sys.path.insert(0, 'src')
from video_agent.tools.motion_graphics.adapter import check_contract, contract_drift
live = json.load(open('/tmp/live_contract.json'))
print('errors:', check_contract(live)); print('drift:', contract_drift(live))
"
# `errors` must be an empty list -- that's the hard compatibility gate. `drift` may legitimately be non-empty
# (e.g. a new element type not yet in the agent's pinned snapshot) -- read each entry to confirm it's additive,
# not a removed/renamed/retyped field. If `errors` is non-empty, this is a real, breaking contract regression --
# fix it or bump the contract/skill version and coordinate a pinned-contract update on the agent side (never edit
# that repo directly from here; it is out of this repo's boundary).

# 3. (Slower, real end-to-end) Run the agent's own adapter test suite against this checkout, including the real
#    ffmpeg RealSkillTests class:
VIDEO_AGENT_MOTION_GRAPHICS_DIR=$(pwd)/../motion-graphics-skill \
VIDEO_AGENT_FFMPEG_SKILL_DIR=$(pwd)/../motion-graphics-skill/vendor/ffmpeg-skill \
PYTHONPATH=src python3 -m pytest tests/test_adapter_motion_graphics.py -v
# (adjust paths; SecurityTests::test_argv_and_request_hygiene will show a false failure if
# VIDEO_AGENT_MOTION_GRAPHICS_DIR is set globally in the shell env -- that's a self-inflicted test artifact from
# also enabling RealSkillTests, not a real bug; re-run just that one test without the env var to confirm.)

# 4. If checking a `provides`-shape change specifically, also validate against the OS registry's real conformance
#    checker (not just its own docs) -- fetch the architecture branch found via git ls-remote above:
git clone --depth 1 --branch <branch-from-ls-remote> https://github.com/kajisho5/AI-video-production-OS /tmp/os
python3 -c "
import sys, json; sys.path.insert(0, '/tmp/os')
from registry.contract import extract_provides, validate_provides_entry
doc = json.load(open('/tmp/live_contract.json'))
print([validate_provides_entry(p) for p in extract_provides(doc)])
"
# Every entry should be an empty list.
```

Re-run all of this whenever `contract.py`'s output shape changes (a new element type, a new/changed parameter
type, a new top-level key), before merging.

## Known limitations (intentional, documented, not gaps to silently "fix")

- `shape` is the only unsupported element type left (`unsupported_element_types` in the contract). No
  `ffmpeg-skill` tool draws an arbitrary shape (position/size/color) without this Skill building a raw filter
  string itself, which is forbidden outright (ADR-1). Implementing it requires `ffmpeg-skill` to gain a typed
  shape tool first — that is a change to a sibling repository, out of this repository's authority; do not attempt
  to work around it by constructing filter strings here.
- Every element type has a fixed, honestly-labeled `animation` value that is not `"configurable"`, except
  `text_overlay`/`image_overlay` (the only two with a real, configurable `fade`): `title`/`bug`/`chapter` get
  `"builtin_fade"`, `lower_third` gets `"builtin_slide_fade"`, `countdown` gets `"builtin_pulse"` (a genuinely
  different animation — a per-digit alpha dip, not a fade), and `progress`/`video_overlay` get `"none"` (both
  truly have no alpha effect at all — verified by reading their filter chains, not assumed; `video_overlay`'s in
  particular because `ffmpeg-skill/overlay --video` never reads `args.fade`, unlike its `--image`/`--text`
  branches, ADR-15). No slide/move/scale as a *configurable* `Animation` — see ADR-2 for why, and do not add one
  without a working, typed, parameterised delegate in `ffmpeg-skill` behind it (this Skill never builds its own
  filter expressions — ADR-1).
- `adapter.SUPPORTED_MIN` was raised from `0.9.1` to `0.12.1` when `video_overlay`/`audio_stream`/
  `dropped_non_av_streams` were added — that is the oldest `ffmpeg-skill` release carrying every flag/field this
  adapter now relies on. If a future change adds a dependency on some still-newer `ffmpeg-skill` flag or response
  field, raise `SUPPORTED_MIN` again the same way rather than leaving `FLAGS_USED`'s contract check as the only
  (partial — it only catches missing *flags*, not missing *response fields* like `dropped_non_av_streams`) guard.
- The pinned agent-side contract is `0.1.0`. A **new element type, a new top-level key, or a new `animation`
  string value is safe** (verified additive four times over, per the compatibility recipe above — `check_contract()`
  never inspects the `animation` field's value at all). A **new parameter `type`** (something other than
  `string`, `integer`, `number`, `boolean`, `color`, `position`, `font`, `path`) is **not** safe — it breaks
  `check_contract()` outright (hit this once designing `bug`'s `position`; solved with `string` + `enum` instead
  — read ADR-11 before ever reaching for a new parameter type). Removing/renaming/retyping an existing field is
  also breaking and needs a version bump plus a coordinated pinned-contract update on the agent side (out of this
  repository's boundary — coordinate, don't just ship it here).
- Numeric-count parameters that could drive an unbounded per-element filter chain (`countdown.count_from`) are
  bounded even where the underlying `ffmpeg-skill` tool itself places no limit (`[1, 60]`, ADR-14) — the same
  class of concern `MAX_ELEMENTS` bounds for the request as a whole, applied at the single-element level. Apply
  the same reasoning to any future element type with a similar "repeat N times" parameter.
- **Newly found, not yet fixed (2026-09-11)**: `tests/test_integration.py::test_video_overlay_chromakey_reveals_the_base_video_underneath`
  fails against the current `vendor/ffmpeg-skill` checkout (`v0.16.14`) — `ffmpeg-skill/overlay` now rejects a
  bare hex `--chromakey` value like `00ff00` ("must be a plain colour... a name, 0xRRGGBB[AA], or #RRGGBB[AA]"),
  where it previously accepted it. Confirmed by bisecting: the same test passes cleanly against `ffmpeg-skill`
  `v0.12.5` (the range `video_overlay` was implemented and tested against) and fails against `v0.16.14`, and the
  rest of the suite is unaffected (312 passed / 1 failed either way) — this is `ffmpeg-skill`'s `--chromakey`
  validation having tightened sometime between those two versions, not a regression in this repository's own
  code, and not something this GitHub-automation PR touches or introduced. Whoever picks this up next: either
  this Skill needs to start emitting a `0x`-prefixed/`#`-prefixed color for `chromakey_color` (check what format
  `ffmpeg-skill/overlay --help`/its current source actually requires first, don't guess), or `SUPPORTED_MIN`
  needs to move forward with a coordinated fix — don't just re-widen the test's expected color string without
  understanding which format is now actually correct.

## Next highest-value task (as of last check)

The "implement every `ffmpeg-skill/graphics` template" arc (ADR-8) is **done** — `bug`, `chapter`, `progress`,
`countdown` all shipped across #4/#6/#7/#8. Issue #10 (ecosystem-wide gap inventory against ffmpeg-skill 0.12.2)
identified four more gaps; items 2-4 are implemented in the uncommitted worktree described above ("Current state"),
item 1 deliberately deferred:

1. **`shape` (issue #10 item 1) still has no ADR update, and is the real next task.** The issue's own framing:
   `background.py` (ffmpeg-skill 0.11.0, generates an exact-size/duration solid-colour or gradient clip) chained
   with `overlay.py --video` (this worktree's `video_overlay`, just added) could compose into exactly ADR-8's
   definition of "shape" with zero raw filter construction. The blocker is real, not stale, though: this Skill's
   `executor._argv()` (and the identity/caching model built on it) assumes exactly one delegate-tool call per
   element — a two-stage `background`+`overlay --video` pipeline for a *single* element is a genuine design
   question (how does identity/caching work for a two-tool element? does it fit the existing `StageResult` shape
   at all, or does "shape" become two `StageResult`s per element?), not a quick follow-up ADR the way `bug`/
   `chapter`/`progress`/`countdown` were. Scope it as its own design pass before touching `model.py`/`executor.py`
   — do not build a raw filter string as a shortcut around the multi-tool question (ADR-1 still applies).
2. **Watch `kajisho5/AI-video-production-OS`'s architecture branch for a merge to `main`.** When it lands, diff
   this repo's `provides`/`CAPABILITY_IDS` choices (`motion_graphics.{bug,chapter,progress,countdown,video_overlay}`,
   all provisional) against whatever actually merged and reconcile if it differs — this is now 5 ids to reconcile,
   not 1.
3. **Done, not just flagged, for `video_overlay` specifically** (mirroring the #4/#6/#7/#8 audit style): it has one
   `path`-type parameter (`video_path`) unlike `bug`/`chapter`/`progress`/`countdown`, so — unlike those four — it
   *does* need explicit PathPolicy coverage; added (`test_missing_video_overlay_asset_fails`,
   `test_video_overlay_source_without_video_stream_rejected` in `tests/test_integration.py`, plus
   `video_overlay_element` added to `test_forbidden_field_rejected_for_every_element_type`'s parametrization in
   `tests/test_security.py`). This is the case CLAUDE.md's own prior note anticipated ("if a future element type
   ever gets a `path` parameter, add it to the PathPolicy tests explicitly then").
4. Do not invent OS integration machinery beyond what a *verified* sibling contract/registry actually reads (see
   the compatibility recipe above) — the OS layer is still mostly unbuilt; keep this Skill's standalone
   contract/doctor/CLI as the source of truth for its own behavior.
