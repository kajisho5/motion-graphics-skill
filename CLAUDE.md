# motion-graphics-skill — maintainer state

Durable, repository-local notes for whichever Claude Code session picks this repo up next. Do not trust
conversation history; trust this file, the code, the tests, and live CI/PR state — in that order. Re-verify
anything here that looks stale before acting on it (dates are given so staleness is checkable).

Last verified: 2026-09-11, against `main` @ `94ba430` (PRs #1-#13 merged) + an open branch
`fix/ffmpeg-skill-1x-compat` (not yet a PR at last edit) fixing a real, currently-broken CI issue described below.
PR #14 (GitHub automation) is open, draft, unrelated to the fix below.

## What this repo is

A deterministic motion-graphics **rendering execution** Skill on top of `ffmpeg-skill`, for the `kajisho5` AI
Video Production ecosystem. It is not an AI agent and makes no design/timing/content decisions — see `README.md`
and `SKILL.md` for the full boundary. Contract version `0.1.0` (unchanged since #1 — every change so far has been
additive; see "Known limitations" for when a version bump would actually be required).

## Current state (verified, not assumed)

- `main` @ `94ba430` implements **9 element types**: `title`, `lower_third`, `text_overlay`, `image_overlay`,
  `bug`, `chapter`, `progress`, `countdown`, `video_overlay` (PiP/chroma-key, `ffmpeg-skill/overlay --video`,
  ADR-15, #11). `document.options.audio_stream` (ADR-16) and `dropped_non_av_streams` surfacing (ADR-17) are also
  merged (#11). `#13` reconciled `adapter.FLAGS_USED` with what `executor.py` actually emits. The full
  `contract`/`doctor`/`validate`/`plan`/`run` CLI, PathPolicy, deterministic provenance, a 9-entry `provides`
  cross-repo Capability-id field. **`shape` remains the only unimplemented element type** — issue #10 item 1's
  re-investigation (chaining a hypothetical `background.py` + `overlay --video` into one composite "shape") is
  still open and deliberately unattempted: `executor._argv()` is a one-tool-per-element model, and a two-stage
  pipeline for one element is a real design question (how does per-element identity/caching work for two tool
  calls?), not a quick follow-up. Scope it as its own design pass before touching `model.py`/`executor.py` — do
  not build a raw filter string as a shortcut (ADR-1 still applies).
- **A real, currently-broken CI issue was found and fixed on `fix/ffmpeg-skill-1x-compat` (2026-09-11) — check
  whether that branch/its PR merged before assuming `SUPPORTED_MAX_EXCLUSIVE = (1, 0, 0)` and bare-hex colour
  flags still work**: `ffmpeg-skill`'s own release automation had, that same day, the *exact* class of bug this
  repo's `.github/release-drafter.yml` was independently found and fixed for (see PR #14's history) — an
  autolabeler rule applied `major` to any PR whose body contained the literal string "BREAKING CHANGE", firing on
  three routine Dependabot PRs whose bodies quoted upstream release notes verbatim. That published `ffmpeg-skill`
  `1.0.0`/`1.0.1`/`1.0.2` with zero actual compatibility change (`ffmpeg-skill`'s own `CHANGELOG.md`: "treat 1.0.x
  as 0.16.x under a different name" — verified independently here too, by diffing `scripts/` between `0.16.14`
  and `1.1.0`: zero differences), then fixed its own pipeline and published a **formal, tested** 1.x stability
  guarantee in its `docs/contract.md` (tool ids/CLI args/`--json` keys/exit codes never removed or retyped for
  the whole 1.x line, pinned by a snapshot test on that side). Two fixes, on `fix/ffmpeg-skill-1x-compat`:
  1. `adapter.SUPPORTED_MAX_EXCLUSIVE` raised `(1, 0, 0)` -> `(2, 0, 0)`, trusting that documented/tested
     guarantee (ADR-18) — CI now clones `ffmpeg-skill`'s current `main` (`1.1.0` as of this writing) and was
     failing the version-gate outright before this.
  2. **A real, previously-latent bug found while investigating**: `ffmpeg-skill/overlay`'s `validate_color()` (a
     filter-graph-injection guard, not a formatting whim) now rejects a bare `RRGGBB` colour value with no `0x`/
     `#` prefix. This affects `--font-color`/`--border-color`/`--box-color` (`text_overlay`) and `--chromakey`
     (`video_overlay`) identically, but only `chromakey` had a real-media test exercising a bare-hex value, so
     only that one test's failure was visible before this fix. `executor._color_arg()` now prefixes a bare
     6-hex-digit value with `0x` before it reaches either `overlay.py` or `graphics.py` (safe for `graphics.py`
     too — its own `color_hex()`/`ff_color()` already strip and re-add `0x` internally, verified as a no-op
     there). `model._color()` is unchanged — it validates request *shape*, not engine wire format. ADR-18;
     `test_color_arg_prefixes_bare_hex_but_passes_named_colors_and_prefixed_hex_through` (unit),
     `test_text_overlay_renders_with_bare_hex_colors` (real-media, closes the previously-untested gap). **315
     tests pass** against both `ffmpeg-skill` `0.16.14` and `1.1.0` (verified directly against both, not assumed)
     — up from 313.
- **The actual downstream consumer already exists and was verified end-to-end, repeatedly, across every PR
  including this fix**: `kajisho5/video-production-agent` ships
  `src/video_agent/tools/motion_graphics/adapter.py` with a **pinned** `contract_0.1.0.json` and a strict
  `check_contract()`/`contract_drift()` compatibility gate. `check_contract()` errors remain `[]` after this fix
  (the version-window/colour-flag change doesn't touch the request/response *shape* at all, only `adapter.py`'s
  internal version gate and `executor.py`'s argv formatting) — `contract_drift()` shows the accumulated,
  expected, purely-additive drift from every element type/field added since the agent's snapshot was pinned
  (five new element types, `provides`, `audio_stream`, `dropped_non_av_streams` — none of it a hard failure).
  **Re-run the compatibility recipe below before merging any future PR that touches `contract.py`,
  `model.ELEMENT_TYPES`, or the request/response shape** — it caught a real compatibility break during #4 (a new
  parameter type broke `check_contract()` outright; solved with `string`+`enum` instead, ADR-11) before it ever
  reached a PR.
- **`kajisho5/AI-video-production-OS`** (the "OS" repo named in ecosystem-wide prompts) still had, as of last
  check, only a placeholder README on its `main` branch — no real architecture merged there yet. The substantive
  architecture (Capability registry, `docs/CAPABILITY_MATRIX.md`, `registry/contract.py` conformance checker)
  lives on an **unmerged** branch there, `claude/ai-video-production-os-arch-*` (branch suffix will change; find
  it with `git ls-remote --heads https://github.com/kajisho5/AI-video-production-OS`). Treat anything from that
  repo as provisional until it lands on `main` — verify against the actual branch content, never assume a cited
  filename exists just because a PR description says so. **Re-check whether it has merged to `main` yet** before
  trusting this paragraph or the provisional ids below.
- `contract.CAPABILITY_IDS` (all `bug`/`chapter`/`progress`/`countdown`/`video_overlay` -> `motion_graphics.*`
  ids are this repository's **own provisional choices** — the OS matrix predates all five; reconcile if the OS
  side ever assigns different ones once merged). `title`/`lower_third`/`text_overlay`/`image_overlay` ids are
  matrix-verified against that unmerged branch's `docs/CAPABILITY_MATRIX.md`.
- Issue #10 (re-investigate `shape`; PiP/chroma-key ownership gap; missing `--audio-stream`;
  `dropped_non_av_streams` discarded) is **still open upstream** even though items 2-4 are merged (#11/#13) — item
  1 (`shape`) is the only thing keeping it open; close it (or narrow its scope to just item 1) once that's
  resolved or explicitly re-deferred with a documented reason. PR #14 (GitHub automation: release-drafter,
  autolabel, Dependabot, CodeQL, PR template, SECURITY.md) is open and draft, unrelated to the fix above — see
  its own PR comments for what's blocking it (was: the `chromakey`/version-window CI failure this fix addresses;
  check whether it's since been rebased onto the fix).

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
  string itself, which is forbidden outright (ADR-1). Issue #10 item 1 argues `background.py` + `overlay --video`
  could compose into "shape" without a raw filter — real, but blocked on a genuine design question (two-tool
  pipeline for one element; see "Current state" above), not on ffmpeg-skill lacking a capability. Scope it as its
  own design pass, do not build a raw filter string as a shortcut.
- Every element type has a fixed, honestly-labeled `animation` value that is not `"configurable"`, except
  `text_overlay`/`image_overlay` (the only two with a real, configurable `fade`): `title`/`bug`/`chapter` get
  `"builtin_fade"`, `lower_third` gets `"builtin_slide_fade"`, `countdown` gets `"builtin_pulse"` (a genuinely
  different animation — a per-digit alpha dip, not a fade), and `progress`/`video_overlay` get `"none"` (both
  truly have no alpha effect at all — verified by reading their filter chains, not assumed; `video_overlay`'s in
  particular because `ffmpeg-skill/overlay --video` never reads `args.fade`, unlike its `--image`/`--text`
  branches, ADR-15). No slide/move/scale as a *configurable* `Animation` — see ADR-2 for why, and do not add one
  without a working, typed, parameterised delegate in `ffmpeg-skill` behind it (this Skill never builds its own
  filter expressions — ADR-1).
- `adapter.SUPPORTED_MIN = (0, 12, 1)`; `adapter.SUPPORTED_MAX_EXCLUSIVE = (2, 0, 0)` as of ADR-18 (was `(1, 0,
  0)`). If a future `ffmpeg-skill` release actually breaks its own documented 1.x stability guarantee (check its
  `docs/contract.md` and `CHANGELOG.md` first — a real break should be announced as a `2.0.0`), lower the ceiling
  back or pin more narrowly rather than assuming the guarantee still holds. If a future change adds a dependency
  on some still-newer `ffmpeg-skill` flag or response field, raise `SUPPORTED_MIN` the same way `0.12.1` was
  chosen — the oldest release carrying everything relied on — rather than leaving `FLAGS_USED`'s contract check
  as the only (partial — it only catches missing *flags*, not missing *response fields*) guard.
- **Colour values passed to `ffmpeg-skill` must be engine-formatted, not just request-shaped.** `model._color()`
  accepts a bare 6-hex-digit string (by design — that's this Skill's own `color` type, unrelated to what any
  particular engine flag requires); `executor._color_arg()` is the one place that becomes the literal token
  `ffmpeg-skill` needs (`0x`-prefixed for bare hex, passed through unchanged for a named colour or an
  already-prefixed value) — see ADR-18. If a future element type or parameter ever bypasses `_color_arg()`
  (builds its own argv without going through the existing colour-flag call sites), it will silently reintroduce
  this exact bug the next time `ffmpeg-skill` tightens `validate_color()` further (e.g. to also reject `@`-alpha
  without a prefix, or something not yet anticipated) — route every colour value through `_color_arg()`, always.
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

## Next highest-value task (as of last check)

1. **Check whether `fix/ffmpeg-skill-1x-compat` has been turned into a PR and merged yet.** If not, that's the
   most urgent thing: CI is currently broken against `ffmpeg-skill`'s real `main` (the version-gate rejection
   affects ~40 tests, not just the one `chromakey` failure PR #14's history documents). Verify the fix still
   applies cleanly and the evidence in ADR-18 still holds (re-diff `ffmpeg-skill`'s current `scripts/` against
   `0.16.14` — if it's no longer zero differences, the "safe to trust the 1.x line" reasoning needs re-checking,
   not just re-asserting).
2. **`shape` (issue #10 item 1)** is the real remaining feature gap — scope the two-tool-pipeline design question
   (per-element identity/caching for a `background`+`overlay --video` composite) before implementing anything.
3. **Watch `kajisho5/AI-video-production-OS`'s architecture branch for a merge to `main`.** When it lands, diff
   this repo's `provides`/`CAPABILITY_IDS` choices against whatever actually merged and reconcile if it differs.
4. Do not invent OS integration machinery beyond what a *verified* sibling contract/registry actually reads (see
   the compatibility recipe above) — the OS layer is still mostly unbuilt; keep this Skill's standalone
   contract/doctor/CLI as the source of truth for its own behavior.
