# Architecture Decision Records

## ADR-9: a custom `font_file` render runs with its own working directory, not a full path

**Decision**: when an element uses a custom `font_file`, `executor._argv()` passes ffmpeg-skill's `--font-file`
a bare file name (e.g. `font.ttf`, no path at all) and `adapter.FfmpegSkill.run_tool()` runs that one invocation
with `cwd` set to the font file's own directory, instead of ffmpeg-skill's usual working directory. Every other
path in the same command (`stage_input`, `stage_output`, `--image`) stays a full absolute path, unaffected by the
`cwd` change, and `load_brand(None)` (the only other thing these scripts might read relative to `cwd`) never
touches the file system when `--brand` is not given, which this skill never passes.

**Why**: `ffmpeg-skill/graphics` and `ffmpeg-skill/overlay` embed `--font-file` into a `fontfile=...` *filter*
option (drawtext), escaping it themselves (backslash-escape the colon, forward-slash the separators). On some
Windows ffmpeg builds this still fails to parse a drive-letter path there ("No option name near ...", "Invalid
argument") -- confirmed by reproducing the exact same failure regardless of which slash convention or escaping
this skill fed it, since ffmpeg-skill's own escaping function round-trips any input through `pathlib.Path` and
always re-normalises it to the same canonical form before escaping. Since a relative, cwd-based file name needs
no escaping at all, giving the invocation a working directory that already contains the font sidesteps the
Windows-specific parsing failure entirely, without this skill building the filter string itself (ADR-1) and
without touching ffmpeg-skill. It generalises to any future ffmpeg build with the same quirk, not just the one
observed in CI.

## ADR-1: No raw ffmpeg filters, ever, at the request boundary

**Decision**: `filter`, `filters`, `filter_complex`, `vf`, `af`, `command`, `commands`, `argv`, `args`, `cmd`,
`shell`, `exec`, `executable`, `script`, `env`, `cwd`, `eval`, `expression` are rejected recursively anywhere in a
request document (`model.FORBIDDEN_KEYS`).

**Why**: this Skill executes a typed specification produced by another (possibly LLM-driven) caller. A field that
could carry a raw filter or command is a direct path to arbitrary code/behavior on the render host. Every visual
capability this Skill offers is instead a named, range-checked parameter mapped by `executor.py` onto exactly one
ffmpeg-skill CLI flag.

## ADR-2: Only `fade`, with fixed both-direction semantics, is a configurable Animation

**Decision**: the only Animation kind a request may specify is `{"kind": "fade", "parameters": {"duration": ...}}`,
and it always fades in at the element's `start` and out at its `end` over the same `duration`.

**Why**: `ffmpeg-skill/overlay --fade` (the only delegate that supports timed opacity) always applies both the
in-fade and the out-fade together whenever both `--start` and `--end` are given — its `alpha_expr` multiplies both
terms whenever `end` is not `None`. There is no flag to select "in only" or "out only" over a bounded window.
Approximating one by omitting `--end` would also cancel the element's own timeline end (the overlay would then
persist to the end of the video), which is a correctness bug, not a feature. Rather than build a custom filter
expression to get partial-fade semantics (forbidden by ADR-1), this Skill exposes exactly what the engine can do.

`slide`, `move`, `scale`-over-time, and arbitrary opacity keyframes are **not implemented** for the same reason:
`ffmpeg-skill/overlay` takes a static position and a static `--scale`/`--scale-percent`; `lower_third`'s slide is a
fixed, non-configurable built-in of the `graphics.py` template. See `model.UNSUPPORTED_ANIMATIONS`.

## ADR-3: `title`/`lower_third` do not accept a configurable `animation` field at all

**Decision**: passing `animation` on a `title` or `lower_third` element is `UNSUPPORTED_OPERATION`, not a silently
ignored field.

**Why**: `ffmpeg-skill/graphics --template title|lower-third` bakes in a fixed 0.3s fade (title) or slide+fade
(lower-third) with hard-coded timing inside the template itself — there is no flag to change it. Accepting an
`animation` field we cannot honor and rendering anyway would misrepresent what the output actually contains.

## ADR-4: Elements render in `(start, id)` order, not request-list order

**Decision**: `GraphicsDocument.ordered_elements()` always sorts by `(start, id)` before rendering, regardless of
the order elements appeared in `document.elements`.

**Why**: each element is a full-frame ffmpeg-skill invocation applied to the previous stage's output. For the
composited result to be deterministic and independent of how a caller happened to list elements (map iteration
order, agent output order, etc.), the execution order itself must be canonical. This is an execution-order
decision only — it does not alter any element's own validated `start`/`end`, and it is not a "fix" to an invalid
timeline (an invalid timeline is still rejected, never repaired).

## ADR-5: Only non-final stages are cached/reused; the requested output always (re)renders

**Decision**: for a document with N elements, stages `0..N-2` write into
`<workspace>/.motion-graphics/<document_id>/<identity[:16]>.<ext>` and are reused when a manifest + sha256 match.
Stage `N-1` always renders directly into `document.output.path` and is always fully re-validated.

**Why**: mirrors `audio-production-skill`'s pattern (its exports are not reused, only intermediates are). The
artifact a caller asked for should always reflect exactly what render just did, including reprobing it, even when
earlier stages were skipped. This also means a single-element document (the common case) never touches the cache
directory at all — it renders directly into the requested output.

## ADR-6: Identity never includes an absolute path or anything machine-specific

**Decision**: `executor._identity_parameters()` replaces `image_path` with `{sha256, size}` and `font` with its
resolved provenance (`font_id` or `font_file_hash`) before hashing. Absolute paths, timestamps, and UUIDs never
enter an identity.

**Why**: STEP 13 requires that identical input + specification + skill/engine version render identically, and
that machine-specific data not leak into artifact identity. Two workspaces with the same video content and the
same specification, but different absolute paths, must produce the same identity and the same cache hits.

## ADR-7: Unknown font / missing asset is a hard failure, never a silent substitution

**Decision**: an unrecognised `font_id` is `MISSING_INPUT`; a `font_file`/`image_path` that does not resolve is
`INVALID_INPUT`/`PATH_NOT_ALLOWED`. This Skill never falls back to a different, available font or asset and
reports success.

**Why**: STEP 12 explicitly forbids exactly this. A silent substitution would make a passing render silently wrong
(the wrong typeface, the wrong logo) with no signal to the caller.

## ADR-8: `shape` and the extra `graphics.py` templates are not implemented in this contract

**Decision**: `shape`, `chapter`, `bug`, `progress`, `countdown` are declared in
`model.UNSUPPORTED_ELEMENT_TYPES` and never appear as `supported` in `contract`/`doctor`.

**Why**: `shape` has no typed delegate (drawing an arbitrary rectangle/shape without a raw filter string is not
exposed by any ffmpeg-skill tool today). `chapter`/`bug`/`progress`/`countdown` do exist as `ffmpeg-skill/graphics`
templates, but STEP 1's requested minimum for this first Skill is title, lower third, text overlay, and
image/logo overlay; STEP 9 forbids publishing an operation as supported before it has a working renderer and
tests. They are natural candidates for a follow-up PR (see README "next PR candidates" in the final report).
