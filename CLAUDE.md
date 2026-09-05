# motion-graphics-skill — maintainer state

Durable, repository-local notes for whichever Claude Code session picks this repo up next. Do not trust
conversation history; trust this file, the code, the tests, and live CI/PR state — in that order. Re-verify
anything here that looks stale before acting on it (dates are given so staleness is checkable).

Last verified: 2026-09-05, against `main` @ `b86a224` and open PR #2 (`claude/add-capability-provides-field`).

## What this repo is

A deterministic motion-graphics **rendering execution** Skill (titles, lower thirds, text/image overlays) on top
of `ffmpeg-skill`, for the `kajisho5` AI Video Production ecosystem. It is not an AI agent and makes no
design/timing/content decisions — see `README.md` and `SKILL.md` for the full boundary. Contract version `0.1.0`.

## Current state (verified, not assumed)

- `main` (`b86a224`) implements 4 element types (`title`, `lower_third`, `text_overlay`, `image_overlay`), the
  full `contract`/`doctor`/`validate`/`plan`/`run` CLI, PathPolicy, deterministic provenance, and 215 tests
  (unit/security/contract/integration, real-media E2E). CI green on Ubuntu/macOS/Windows × Python 3.9/3.11.
- **The actual downstream consumer already exists and was verified end-to-end**: `kajisho5/video-production-agent`
  ships `src/video_agent/tools/motion_graphics/adapter.py` with a **pinned** `contract_0.1.0.json` and a strict
  `check_contract()`/`contract_drift()` compatibility gate. Running that repo's full
  `tests/test_adapter_motion_graphics.py` (21 tests, including `RealSkillTests` which spins up real `ffmpeg` and
  drives this Skill's actual CLI, not a fake) against this repo's `main` passes 100%, with **zero contract drift**.
  This is the strongest available evidence the contract is correct — re-run it after any contract-shaping change
  (see "How to re-verify OS/agent compatibility" below).
- **`kajisho5/AI-video-production-OS`** (the "OS" repo named in ecosystem-wide prompts) has, on its `main` branch,
  only a placeholder README — no real architecture there yet. The substantive architecture (Capability registry,
  `docs/CAPABILITY_MATRIX.md`, `docs/SPEC.md`, `registry/contract.py` conformance checker, per-Skill `.provides`
  fixtures for several sibling Skills) lives on an **unmerged** branch there,
  `claude/ai-video-production-os-arch-*` (branch suffix will change; find it with
  `git ls-remote --heads https://github.com/kajisho5/AI-video-production-OS`). Treat anything from that repo as
  provisional until it lands on `main` there — verify against the actual branch content, never assume a cited
  filename exists just because a PR description says so (this bit PR #2 once; see below).
- **PR #2** (`claude/add-capability-provides-field`, open, draft): adds a `provides` top-level field to the
  contract, publishing this Skill's 4 element types under 3 cross-repo Capability ids
  (`motion_graphics.title_card`, `motion_graphics.lower_third`, `motion_graphics.overlay` — the latter shared by
  `text_overlay`/`image_overlay`). Verified directly against the real (unmerged-branch) `registry/contract.py` and
  `docs/CAPABILITY_MATRIX.md` — the ids and entry shape (`id`/`tool_id`/`lifecycle`, extra fields ignored) match
  exactly. The PR's original description overclaimed this spec as settled/merged; wording was corrected
  (`f2b7a91`) and a missing contract test was added (`test_contract_provides_one_capability_entry_per_element_type`
  in `tests/test_contract.py`) before merging. If this PR is already merged by the time you read this, this whole
  paragraph is historical — check `git log`/`gh`/the repo's PR list rather than trusting it.
- No open GitHub issues as of last check.

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
# Both must print empty lists. If not, this is a real, breaking contract regression -- fix it or bump the
# contract/skill version and coordinate a pinned-contract update on the agent side (never edit that repo directly
# from here; it is out of this repo's boundary).

# 3. (Slower, real end-to-end) Run the agent's own adapter test suite against this checkout, including the real
#    ffmpeg RealSkillTests class:
VIDEO_AGENT_MOTION_GRAPHICS_DIR=$(pwd)/../motion-graphics-skill \
VIDEO_AGENT_FFMPEG_SKILL_DIR=$(pwd)/../motion-graphics-skill/vendor/ffmpeg-skill \
PYTHONPATH=src python3 -m pytest tests/test_adapter_motion_graphics.py -v
# (adjust paths; SecurityTests::test_argv_and_request_hygiene will show a false failure if
# VIDEO_AGENT_MOTION_GRAPHICS_DIR is set globally in the shell env -- that's a self-inflicted test artifact from
# also enabling RealSkillTests, not a real bug; re-run just that one test without the env var to confirm.)
```

Re-run this whenever `contract.py`'s output shape changes, before merging.

## Known limitations (intentional, documented, not gaps to silently "fix")

- Only `title`/`lower_third`/`text_overlay`/`image_overlay` are implemented. `shape`/`chapter`/`bug`/`progress`/
  `countdown` are explicitly unsupported (`unsupported_element_types` in the contract) — ffmpeg-skill's
  `graphics.py` has templates for some of these, but STEP 9 of the original design brief forbids publishing an
  operation as supported before it has a working renderer *and tests* here. Natural next-PR candidates, each
  independently small: implement one, add real-media tests, publish it as supported. Do this in this repo, never
  by touching `ffmpeg-skill`.
- Only one configurable animation (`fade`), only for `text_overlay`/`image_overlay`. No slide/move/scale — see
  `docs/decisions.md` for why, and do not add this without a working renderer + tests behind it (same rule as
  above; slide/scale would need new ffmpeg-skill filter chains this Skill would still just delegate to, not
  build itself).
- The pinned agent-side contract is `0.1.0`. Any change to `contract.py`'s output shape that isn't purely additive
  (removing/renaming a field, changing a type, changing an enum) needs a version bump and is a breaking change for
  `video-production-agent`'s `check_contract()` — coordinate, don't just ship it.

## Next highest-value task (as of last check)

1. If PR #2 is still open: get it green and merge it (see PR #2 section above).
2. After that, the two most valuable independent next steps, roughly in order:
   - Pick one unsupported element type with an existing `ffmpeg-skill/graphics` template (check
     `vendor/ffmpeg-skill/scripts/graphics.py --help` or its contract for what templates actually exist today)
     and implement it end-to-end: model + contract + doctor + real-media tests + docs. Small, additive, no new
     external dependency.
   - Watch `kajisho5/AI-video-production-OS`'s architecture branch for a merge to `main`; when it lands, diff this
     repo's `provides`/Capability-id choices against whatever actually merged (ids/shape may have changed during
     that repo's own review) and reconcile if needed.
3. Do not invent OS integration machinery beyond what a *verified* sibling contract/registry actually reads (see
   "verify against real source" note above) — the OS layer is still mostly unbuilt; keep this Skill's standalone
   contract/doctor/CLI as the source of truth for its own behavior.
