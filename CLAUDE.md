# motion-graphics-skill — maintainer state

Durable, repository-local notes for whichever Claude Code session picks this repo up next. Do not trust
conversation history; trust this file, the code, the tests, and live CI/PR state — in that order. Re-verify
anything here that looks stale before acting on it (dates are given so staleness is checkable).

Last verified: 2026-10-29, against `main` @ `32d2806` (v0.1.2) plus the change that adds `sticker`/`hook`/`meme` and
`document.options.platform` (ADR-19). Corrected in the same pass: this file had still described a long-merged worktree as
"uncommitted", and the `shape` rationale below was wrong (ADR-8).

## What this repo is

A deterministic motion-graphics **rendering execution** Skill on top of `ffmpeg-skill`, for the `kajisho5` AI
Video Production ecosystem. It is not an AI agent and makes no design/timing/content decisions — see `README.md`
and `SKILL.md` for the full boundary. Contract version `0.1.0` (unchanged since #1 — every change so far has been
additive; see "Known limitations" for when a version bump would actually be required).

## Current state (verified, not assumed)

- **12 element types**: `title`, `lower_third`, `text_overlay`, `image_overlay`, `video_overlay`, `bug`, `chapter`, `progress`,
  `countdown`, `sticker`, `hook`, `meme`. `ffmpeg-skill/graphics` has 9 templates and all 9 are implemented; check that again
  whenever ffmpeg-skill releases (`python3 scripts/graphics.py --help` shows `--template {...}`): ADR-14 once declared "every
  template" done and ffmpeg-skill had already added three more. `sticker`/`hook`/`meme` and `--platform` came with ffmpeg-skill
  #224 and are **missing from 0.12.5**, the oldest checkout CI verifies: they are detected (`adapter.TEMPLATE_FLAGS`,
  `check_tools()`), `doctor` reports them `unsupported`, and a request for one is refused before rendering (ADR-19).
- The full `contract`/`doctor`/`validate`/`plan`/`run` CLI, PathPolicy, deterministic provenance, a 12-entry `provides` field,
  `document.options` `reuse_intermediates`/`crf`/`preset`/`audio_stream`/`platform`, and **473 tests** (unit, security, contract,
  compat, colours, real-media integration). 473 pass against ffmpeg-skill 2.21.0 and against 0.12.5 (`87c84f2`), as of 2026-10-30 (ADR-20).
- **Colours (ADR-20, 2026-10-30)**: `ffmpeg-skill/graphics` takes `--primary`/`--text-color` as opaque `RRGGBB` only (`color_hex`, every
  version). Template elements' colour names are converted from the fixed table in `colors.py` (FFmpeg's own 140 names; `test_colors.py`
  checks it against the installed `ffmpeg -colors`), `@alpha` on them is refused up front (schema `alpha: false`), and a name not in the
  table is refused for every element type. Do not "fix" a refused `@alpha` by stripping it; it needs an opacity flag on `graphics.py` first.
  The `alpha` keys changed eight pinned element types' `parameters`, which is **fatal drift** for video-production-agent's pin
  (`contract_0.2.3.json`): it shows motion-graphics `MISSING` until it re-pins.
- **ffmpeg-skill has no version window** (ADR-18): flags are read from the checkout's contract/`--help`; CI runs `main` on every
  platform (push and daily) and 0.12.5 on Linux. The Python 3.9 job runs on `ubuntu-24.04` because `ubuntu-latest` (26.04) has no
  3.9 build (#20). Do not re-add a version bound; add a flag to `adapter.REQUIRED_FLAGS`/`OPTIONAL_FLAGS` instead.
- **Release automation is live** (`.github/workflows/release.yml`, one job on every push to `main`: version, `pyproject.toml`,
  `CHANGELOG.md`, tag, GitHub Release, optional PyPI). Do not hand-edit `CHANGELOG.md` or the version. The autolabeler never assigns
  `major`/`breaking` from PR text, on purpose (a PR body that merely quotes "BREAKING CHANGE" made ffmpeg-skill publish 1.0.0-1.0.2 with no breaking change); a human applies that label when a major release is really meant.
- **`video-production-agent`** (`kajisho5/video-production-agent`, the downstream consumer) pins `contract_0.1.0.json` and gates on
  `check_contract()` (hard) / `contract_drift()` (informational). Re-verified for ADR-19: `check_contract()` returns `[]`, and the
  drift gains exactly `element_types hook/meme/sticker: installed but not pinned`. Its `tests/test_adapter_motion_graphics.py` has two
  failures that are **not** from this repository's changes and also occur on unmodified `main`: `SecurityTests::test_argv_and_request_hygiene`
  (only when `VIDEO_AGENT_MOTION_GRAPHICS_DIR` is set; passes without it) and `RealSkillTests::test_real`, which asserts zero
  `contract_drift()` and has 3 older entries (`tools`, `ffmpeg_skill`, `request`) since ADR-18. Judge a change by the *difference* in drift and
  by `check_contract()`, not by that test's pass/fail, until the agent re-pins.
- **`kajisho5/AI-video-production-OS`**: its architecture is on `main` now (v0.1.1, 2026-10-25: `docs/CAPABILITY_MATRIX.md`,
  `registry/contract.py`); the earlier note that only a placeholder README existed is obsolete. Its matrix section 5 lists only
  `motion_graphics.title_card`, `.lower_third` and `.overlay`. The other nine ids in `contract.CAPABILITY_IDS` (`bug`, `chapter`, `progress`,
  `countdown`, `video_overlay`, `sticker`, `hook`, `meme`) are this repository's **own provisional choices**; reconcile them if the OS side
  assigns others. All 12 `provides` entries validate against OS `main`'s `validate_provides_entry()` (and the older architecture branch's).
- Issue #10 (shape; PiP/chroma-key ownership; `--audio-stream`; `dropped_non_av_streams`) is still open: items 2-4 shipped, item 1 (`shape`) is
  the remaining one. Check the open PRs and issues before assuming anything about them.

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
# also enabling RealSkillTests, not a real bug; re-run just that one test without the env var to confirm.
# RealSkillTests::test_real asserts zero contract drift and fails on unmodified main too (see "Current state"):
# compare against a worktree of origin/main run the same way, and judge the difference.)

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

- `shape` is the only unsupported element type (`unsupported_element_types` in the contract). **It is not blocked on ffmpeg-skill** (the
  earlier claim here and in ADR-8 was wrong): `background.py` (a solid-colour clip of exact size) plus `overlay.py --video` (position,
  opacity, time window) draws a rectangle with no raw filter, verified on ffmpeg-skill 2.10.8. It is blocked on a design decision in this
  repository: the stage model, identity (ADR-5/6) and `StageResult` assume one delegate call per element, a rectangle is two. Decide that
  first (ADR-8). Rounded corners, borders and rotation were not tried. Never build a filter string to get around it (ADR-1).
- Every element type has a fixed, honestly-labeled `animation` value that is not `"configurable"`, except `text_overlay`/`image_overlay`
  (the only two with a real, configurable `fade`): `title`/`bug`/`chapter` get `"builtin_fade"`, `lower_third` `"builtin_slide_fade"`,
  `countdown` `"builtin_pulse"`, `sticker` `"builtin_pop"` (fade up while rising 0.25 s, fade out over the last 0.3 s), and `progress`, `hook`,
  `meme`, `video_overlay` get `"none"` (verified by reading each filter chain, not assumed). A configurable slide/move/scale/easing is not
  available because `overlay.py` still takes only a static position/scale and `--fade`; ffmpeg-skill's `caption.py --animate none|fade|pop|slide`
  shows typed animation exists upstream, for captions only. Do not add one without a typed, parameterised delegate behind it (ADR-1/ADR-2).
- **No transparent (alpha) output.** ffmpeg-skill has no `yuva`/ProRes 4444/PNG-with-alpha output anywhere (`--codec prores` is 422 HQ,
  `yuv422p10le`); this skill only burns graphics into a given video. Keyed overlay assets for a switcher/NLE need an upstream capability first.
- The pinned agent-side contract is `0.1.0`. A **new element type, a new top-level key (`platforms`, `constraints`), or a new `animation`
  string value is safe** (verified additive again for ADR-19). A **new parameter `type`** (anything other than `string`, `integer`,
  `number`, `boolean`, `color`, `position`, `font`, `path`) is **not** safe: it breaks `check_contract()` outright (ADR-11 explains the
  `string` + `enum` workaround). Removing/renaming/retyping an existing field is breaking and needs a version bump plus a coordinated
  pinned-contract update on the agent side (out of this repository's boundary: coordinate, don't just ship it here).
- Numeric-count parameters that could drive an unbounded per-element filter chain (`countdown.count_from`) are bounded even where
  ffmpeg-skill places no limit (`[1, 60]`, ADR-14). Apply the same reasoning to any future "repeat N times" parameter.
- `options.platform` and overlay margins: ffmpeg-skill/`overlay.py` tells "explicit margin" from "none" by comparing `--margin` with its own
  default (24), so an explicit `margin: 24` still gets the safe zone. Upstream behaviour, stated in the contract (`platforms.effect`), not worked around.
- `hook`: the template's own `--duration` (default 3 s) is set to the element's `end - start`, deliberately, so the card spans the window.

## Not wired yet (verified present in ffmpeg-skill 2.10.8, unused here; check the ecosystem owner before taking any)

- `--brand brand.json` on `graphics`/`overlay` and `overlay --logo`: colours, font, logo and safe margin from one file instead of per-element
  parameters (this skill passes colours explicitly and never a brand file, a choice recorded in `docs/ffmpeg-skill.md`; revisit with the agent's needs).
- `look.py` (contact sheet, `--at` frames, `--compare`, `--safe`): a preview/QA step. It would be a fourth tool on `adapter.TOOLS_USED`, an
  allowlist change that `test_tool_allowlist_is_closed` pins on purpose.
- `--lang`, `--wrap`, `--text-render` on `graphics`: line-breaking and the CJK/script choice for text. **Unverified**: how Japanese text
  behaves through this skill without `--lang ja` (the help says Han-only text needs it to pick Japanese vs Chinese glyphs). Check by rendering before relying on it.
- `--codec hevc|prores`, `--hw`; `caption.py` (subtitles; the OS lists a separate `subtitle-skill`, so likely not this repository's).

## Next highest-value task (as of last check)

1. **Decide `shape`** (ADR-8): the design question above. Write the decision as an ADR first; then implement it as a two-stage element, or
   decide it does not fit and say why in ADR-8 instead of leaving the stale "blocked upstream" wording.
2. **Re-check ffmpeg-skill's templates and flags after each of its releases**: the daily `main` job in CI will fail on a breaking change, but
   it will not announce a *new* template or flag. Diff `graphics.py --help` against `adapter.TEMPLATE_FLAGS` and `ELEMENT_TYPES`.
3. **Unwired capabilities above**, starting with whichever the downstream agent (or the user) actually needs; `--lang` first if Japanese
   text is in scope, since it may be a correctness issue rather than a feature.
4. Reconcile the nine provisional Capability ids with the OS matrix once it assigns them; do not invent OS integration machinery beyond what
   a verified sibling contract/registry reads (the compatibility recipe above).
5. When adding a future element type with a `path` parameter, add it to the PathPolicy tests explicitly: forbidden-field rejection is generic
   (`_reject_forbidden()` runs first, `test_forbidden_field_rejected_for_every_element_type`), path confinement is not.
