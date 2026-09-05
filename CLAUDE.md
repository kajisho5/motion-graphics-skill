# motion-graphics-skill — maintainer state

Durable, repository-local notes for whichever Claude Code session picks this repo up next. Do not trust
conversation history; trust this file, the code, the tests, and live CI/PR state — in that order. Re-verify
anything here that looks stale before acting on it (dates are given so staleness is checkable).

Last verified: 2026-09-05, against `main` @ `01614c7` (PRs #1-#8 all merged, no open PRs, no open issues).

## What this repo is

A deterministic motion-graphics **rendering execution** Skill on top of `ffmpeg-skill`, for the `kajisho5` AI
Video Production ecosystem. It is not an AI agent and makes no design/timing/content decisions — see `README.md`
and `SKILL.md` for the full boundary. Contract version `0.1.0` (unchanged since #1 — every change so far has been
additive; see "Known limitations" for when a version bump would actually be required).

## Current state (verified, not assumed)

- `main` (`01614c7`) implements **8 element types**: `title`, `lower_third`, `text_overlay`, `image_overlay`,
  `bug`, `chapter`, `progress`, `countdown` — every template `ffmpeg-skill/graphics` exposes, plus the two
  `ffmpeg-skill/overlay` element types. The full `contract`/`doctor`/`validate`/`plan`/`run` CLI, PathPolicy,
  deterministic provenance, an 8-entry `provides` cross-repo Capability-id field, and **258 tests**
  (unit/security/contract/integration, real-media E2E). CI green on Ubuntu/macOS/Windows × Python 3.9/3.11.
  **Only `shape` remains unimplemented** — and unlike the other four that were closed out this session
  (`bug` #4, `chapter` #6, `progress` #7, `countdown` #8), it isn't a small follow-up: no `ffmpeg-skill` tool
  exposes a typed shape-drawing delegate at all, so implementing it is blocked on `ffmpeg-skill` gaining one
  first — out of this repository's authority to add (see ADR-8, rewritten in #8 to reflect this).
- **The actual downstream consumer already exists and was verified end-to-end, repeatedly, after every single one
  of the 8 PRs above**: `kajisho5/video-production-agent` ships
  `src/video_agent/tools/motion_graphics/adapter.py` with a **pinned** `contract_0.1.0.json` and a strict
  `check_contract()`/`contract_drift()` compatibility gate. Its full `tests/test_adapter_motion_graphics.py`
  (21 tests, including `RealSkillTests`, which spins up real `ffmpeg` and drives this Skill's actual CLI, not a
  fake) passed 100% against every intermediate state of this checkout, with **zero `check_contract()` errors**
  throughout — new element types/fields show up only as informational `contract_drift()` entries (the agent's
  pinned snapshot doesn't know about them yet), never a hard failure. This is the strongest available evidence
  the contract stays correct. **Re-run this before merging any future PR that touches `contract.py`,
  `model.ELEMENT_TYPES`, or the request/response shape** (see the recipe below) — it caught a real compatibility
  break during #4 (a new parameter type broke `check_contract()` outright; solved with `string`+`enum` instead,
  ADR-11) before it ever reached a PR.
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
  `image_overlay` -> `motion_graphics.overlay` (matrix-verified), and `bug`/`chapter`/`progress`/`countdown` ->
  `motion_graphics.{bug,chapter,progress,countdown}` (this repository's **own provisional ids** — that matrix
  predates all four; reconcile if the OS side ever assigns different ones once merged).
- No open GitHub issues or pull requests as of last check.

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
  different animation — a per-digit alpha dip, not a fade), and `progress` gets `"none"` (it truly has no alpha
  effect at all — verified by reading its filter chain, not assumed). No slide/move/scale as a *configurable*
  `Animation` — see ADR-2 for why, and do not add one without a working, typed, parameterised delegate in
  `ffmpeg-skill` behind it (this Skill never builds its own filter expressions — ADR-1).
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

The "implement every `ffmpeg-skill/graphics` template" arc (ADR-8) is **done** — `bug`, `chapter`, `progress`,
`countdown` all shipped across #4/#6/#7/#8. There is no more low-hanging, purely-additive element-type work left
in this repository; the next real gaps are cross-cutting, not "pick the next template":

1. **`shape` is blocked here.** If a shape-drawing capability lands in `ffmpeg-skill` (check its
   `scripts/graphics.py`/contract for a new template or tool), implement it the same way as the other four —
   otherwise there is nothing to do on this front from this repository alone.
2. **Watch `kajisho5/AI-video-production-OS`'s architecture branch for a merge to `main`.** When it lands, diff
   this repo's `provides`/`CAPABILITY_IDS` choices (`motion_graphics.{bug,chapter,progress,countdown}`, all
   provisional) against whatever actually merged and reconcile if it differs — this is now 4 ids to reconcile,
   not 1.
3. **Done, not just flagged**: audited whether the newer types (`bug`/`chapter`/`progress`/`countdown`) get the
   same security coverage the original 4 do. Finding: `_reject_forbidden()` runs on the raw document before any
   type-specific parsing, so forbidden-field rejection was already generic across every type by construction, not
   something each new type needed its own copy of — confirmed by reading `model.parse_request()`, not assumed.
   None of the four new types has any `path`-type parameter either, so PathPolicy/traversal/symlink tests
   genuinely don't apply to them (nothing to add there). Added one small explicit regression test,
   `test_forbidden_field_rejected_for_every_element_type` in `tests/test_security.py`, parametrized across all 5
   element-factory helpers, so this generality is pinned down rather than an implicit assumption resting only on
   `title`'s coverage. If a *future* element type ever gets a `path` parameter, add it to the PathPolicy tests
   explicitly then — that part of the boundary is not automatically generic the way forbidden-field rejection is.
4. Do not invent OS integration machinery beyond what a *verified* sibling contract/registry actually reads (see
   the compatibility recipe above) — the OS layer is still mostly unbuilt; keep this Skill's standalone
   contract/doctor/CLI as the source of truth for its own behavior.
