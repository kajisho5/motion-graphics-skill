# motion-graphics-skill — maintainer state

Durable, repository-local notes for whichever Claude Code session picks this repo up next. Do not trust
conversation history; trust this file, the code, the tests, and live CI/PR state — in that order. Re-verify
anything here that looks stale before acting on it (dates are given so staleness is checkable).

Last verified: 2026-09-05, against `main` @ `5d7faed` (PRs #1-#4 all merged, no open PRs, no open issues).

## What this repo is

A deterministic motion-graphics **rendering execution** Skill (titles, lower thirds, text/image overlays, corner
"bug" watermarks) on top of `ffmpeg-skill`, for the `kajisho5` AI Video Production ecosystem. It is not an AI
agent and makes no design/timing/content decisions — see `README.md` and `SKILL.md` for the full boundary.
Contract version `0.1.0` (unchanged since #1 — every change so far has been additive, see "Known limitations"
below on when a version bump would actually be required).

## Current state (verified, not assumed)

- `main` (`5d7faed`) implements 5 element types (`title`, `lower_third`, `text_overlay`, `image_overlay`, `bug`),
  the full `contract`/`doctor`/`validate`/`plan`/`run` CLI, PathPolicy, deterministic provenance, a `provides`
  cross-repo Capability-id field, and 226 tests (unit/security/contract/integration, real-media E2E). CI green on
  Ubuntu/macOS/Windows × Python 3.9/3.11.
- **The actual downstream consumer already exists and was verified end-to-end, repeatedly**: `kajisho5/video-
  production-agent` ships `src/video_agent/tools/motion_graphics/adapter.py` with a **pinned** `contract_0.1.0.json`
  and a strict `check_contract()`/`contract_drift()` compatibility gate. Its full `tests/test_adapter_motion_graphics.py`
  (21 tests, including `RealSkillTests` which spins up real `ffmpeg` and drives this Skill's actual CLI, not a
  fake) passes 100% against this checkout, with **zero `check_contract()` errors**, both before and after adding
  `provides` (#2) and `bug` (#4) — the new element type/field show up only as informational `contract_drift()`
  entries (the agent's own pinned snapshot doesn't know about them yet), never a hard failure. This is the
  strongest available evidence the contract stays correct — re-run it after any contract-shaping change (see
  "How to re-verify OS/agent compatibility" below). **Do this before merging any future PR that touches
  `contract.py`, `model.ELEMENT_TYPES`, or the request/response shape** — it would have caught a real
  compatibility break during #4 (a new parameter type was tried first, found to break `check_contract()` outright,
  and replaced with a `string`+`enum` shape instead; see ADR-11).
- **`kajisho5/AI-video-production-OS`** (the "OS" repo named in ecosystem-wide prompts) still has, on its `main`
  branch, only a placeholder README — no real architecture merged there yet, as of this writing. The substantive
  architecture (Capability registry, `docs/CAPABILITY_MATRIX.md`, `docs/SPEC.md`, `registry/contract.py`
  conformance checker, per-Skill `.provides` fixtures for several sibling Skills) lives on an **unmerged** branch
  there, `claude/ai-video-production-os-arch-*` (branch suffix will change; find it with
  `git ls-remote --heads https://github.com/kajisho5/AI-video-production-OS`). Treat anything from that repo as
  provisional until it lands on `main` there — verify against the actual branch content, never assume a cited
  filename exists just because a PR description says so (this happened once already, in #2's original
  description; corrected in `f2b7a91` before merge). Re-check whether it has merged to `main` yet before trusting
  this paragraph.
- **#2** (`provides` field) and **#4** (`bug` element type) are both merged. `contract.CAPABILITY_IDS` now has:
  `title` -> `motion_graphics.title_card`, `lower_third` -> `motion_graphics.lower_third`, `text_overlay`/
  `image_overlay` -> `motion_graphics.overlay` (verified against the real, unmerged-branch `docs/CAPABILITY_MATRIX.md`),
  and `bug` -> `motion_graphics.bug` (this repository's **own provisional id** — that matrix predates `bug`'s
  implementation and has no entry for it; reconcile if the OS side ever assigns a different one once merged).
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

- `shape`/`chapter`/`progress`/`countdown` are still explicitly unsupported (`unsupported_element_types` in the
  contract) — `ffmpeg-skill/graphics.py` has templates for `chapter`/`progress`/`countdown` (see its own
  docstring/`TEMPLATES` list), and `shape` has no typed delegate at all anywhere upstream. STEP 9 of the original
  design brief forbids publishing an operation as supported before it has a working renderer *and tests* here.
  `bug` was in this same list until #4 implemented it the same way these three should be: model + contract +
  doctor (automatic, derived) + real-media tests + docs, one element type per PR. Do this in this repo, never by
  touching `ffmpeg-skill`.
- Only one configurable animation (`fade`), only for `text_overlay`/`image_overlay`. `title`/`lower_third`/`bug`
  each get a fixed, non-configurable built-in fade (or slide+fade for `lower_third`) instead. No slide/move/scale
  as a configurable `Animation` — see `docs/decisions.md` ADR-2 for why, and do not add this without a working,
  typed, parameterised delegate in `ffmpeg-skill` behind it (this Skill does not build its own filter expressions,
  ever — see ADR-1).
- The pinned agent-side contract is `0.1.0`. A **new element type or a new top-level key is safe** (verified
  additive per the compatibility recipe above). A **new parameter `type`** (something other than `string`,
  `integer`, `number`, `boolean`, `color`, `position`, `font`, `path`) is **not** safe — it breaks
  `check_contract()` outright (learned the hard way while designing `bug`'s `position`; solved by expressing it as
  `string` + `enum` instead — see ADR-11 before ever reaching for a new parameter type). Removing/renaming/
  retyping an existing field is also breaking and needs a version bump plus a coordinated pinned-contract update
  on the agent side (out of this repo's boundary — coordinate, don't just ship it here).

## Next highest-value task (as of last check)

1. Pick the next unsupported element type with an existing `ffmpeg-skill/graphics` template (`chapter`,
   `progress`, or `countdown` — read `vendor/ffmpeg-skill/scripts/graphics.py`'s docstring/`TEMPLATES` for what
   each actually needs) and implement it end-to-end the same way `bug` was in #4: model + contract (automatic) +
   real-media tests with empirically-measured pixel-check crop windows (don't guess coordinates from the filter
   expression alone -- render it and measure, as ADR-11's tests did) + docs + a new ADR. Small, additive, no new
   external dependency. `progress`/`countdown` may need a moment's thought about what parameters make sense as a
   typed `GraphicsElement` (e.g. `countdown`'s `--from` count) before assuming the shape mirrors `bug`'s.
2. Watch `kajisho5/AI-video-production-OS`'s architecture branch for a merge to `main`; when it lands, diff this
   repo's `provides`/`CAPABILITY_IDS` choices (especially the provisional `motion_graphics.bug`) against whatever
   actually merged and reconcile if it differs.
3. Do not invent OS integration machinery beyond what a *verified* sibling contract/registry actually reads (see
   the compatibility recipe above) — the OS layer is still mostly unbuilt; keep this Skill's standalone
   contract/doctor/CLI as the source of truth for its own behavior.
