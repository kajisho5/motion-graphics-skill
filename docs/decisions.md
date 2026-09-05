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

**Scope note**: `document_id` (the `.motion-graphics/<document_id>/` folder name) is hashed from the *raw* request
(unresolved asset paths, no `crf`/`preset`) -- it only needs to be a stable folder name, not a trust boundary, so
two unrelated documents sharing one by coincidence is harmless: every file inside is still named by its own full
per-stage identity (which *does* include the resolved sha256, font, `crf`, and `preset`, per stage above), and
`_reusable()` re-verifies that file's actual sha256 against its manifest before ever trusting it as a cache hit.

## ADR-7: Unknown font / missing asset is a hard failure, never a silent substitution

**Decision**: an unrecognised `font_id` is `MISSING_INPUT`; a `font_file`/`image_path` that does not resolve is
`INVALID_INPUT`/`PATH_NOT_ALLOWED`. This Skill never falls back to a different, available font or asset and
reports success.

**Why**: STEP 12 explicitly forbids exactly this. A silent substitution would make a passing render silently wrong
(the wrong typeface, the wrong logo) with no signal to the caller.

## ADR-8: `shape` and the extra `graphics.py` templates are not implemented in this contract

**Decision**: `shape` and `countdown` are declared in
`model.UNSUPPORTED_ELEMENT_TYPES` and never appear as `supported` in `contract`/`doctor`. (`bug`, `chapter`, and
`progress` were in this list too, until ADR-11/ADR-12/ADR-13 implemented them — this ADR's rationale below still
applies to the one that remains unsupported.)

**Why**: `shape` has no typed delegate (drawing an arbitrary rectangle/shape without a raw filter string is not
exposed by any ffmpeg-skill tool today). `countdown` does exist as an `ffmpeg-skill/graphics` template, but
STEP 1's requested minimum for this first Skill is title, lower third, text overlay, and image/logo overlay;
STEP 9 forbids publishing an operation as supported before it has a working renderer and tests. It is a natural
candidate for a follow-up PR, the same way `bug`, `chapter`, and `progress` were (ADR-11/ADR-12/ADR-13).

## ADR-10: `provides` publishes cross-repository Capability ids, with `text_overlay` and `image_overlay` sharing one id

**Decision**: `contract.py` adds a top-level `provides` list, one entry per `model.ELEMENT_TYPES` key, each with a
Capability id, a `lifecycle`, the `tool_id` (always `motion-graphics/run`, this Skill's one execution tool) and the
`element_type` it maps to. The ids are `title` -> `motion_graphics.title_card`, `lower_third` ->
`motion_graphics.lower_third`, and both `text_overlay` and `image_overlay` -> `motion_graphics.overlay`.

**Why**: this anticipates `kajisho5/AI-video-production-OS`'s Capability registry, so it can eventually resolve
"who provides `motion_graphics.title_card`" without hardcoding this repository. `ELEMENT_TYPES` has no
capability-shaped id of its own, so these ids are a new naming decision, not a mechanical derivation — they match
the ids assigned to this Skill in that project's `docs/CAPABILITY_MATRIX.md` and the entry shape its
`registry/contract.py` actually validates (`id`/`tool_id`/`lifecycle`, extra fields ignored) — verified directly
against both files' real content, not assumed from a description. As of this writing that content lives on that
repository's not-yet-merged architecture branch, not its `main` (which carries only a placeholder README) — this
is one of several ecosystem Skills adding `provides` ahead of that branch landing, kept here in
`contract.CAPABILITY_IDS` as this repository's own source of truth in the meantime, to be reconciled if the
merged spec ever assigns different ids. `text_overlay` and `image_overlay` share `motion_graphics.overlay` because
that matrix already treats them as one capability ("free-form text, image/logo overlay"), not two — `title` and
`lower_third` each keep their own built-in-template id. Additive: a new top-level key derived from `ELEMENT_TYPES`,
saying nothing `element_types[]` doesn't already say, only indexed by Capability id instead of element type.

## ADR-11: `bug` is implemented; its `position` is a closed-vocabulary `string`, not a new parameter type

**Decision**: `bug` (a persistent text watermark in one of four corners, `ffmpeg-skill/graphics --template bug`) is
implemented and removed from `UNSUPPORTED_ELEMENT_TYPES`, per ADR-8's own "natural candidate for a follow-up PR"
note. It gets a `builtin_fade` animation (the same fixed 0.3s alpha fade `title` uses) and three parameters:
`title` (required), `position` (optional, default `top-right`), `text_color` (optional). Its capability id is
`motion_graphics.bug`, added to `contract.CAPABILITY_IDS` (ADR-10).

**Why not a full `position` type**: ffmpeg-skill/graphics's `--position` for `bug` is an argparse `choices` of
exactly four corner names (`top-left`/`top-right`/`bottom-left`/`bottom-right`) — no `{x, y}` support at all,
unlike `text_overlay`/`image_overlay`'s 9-way `position` type (which also accepts an explicit pixel offset).
Reusing the `position` type as-is would silently accept values (`"top"`, `"center"`, `{"x": .., "y": ..}`) that
`ffmpeg-skill/graphics` itself would then reject with an argparse error — a `TOOL_ERROR` surfaced from a
downstream process failure instead of a clean `INVALID_REQUEST` at the request boundary, and exactly the kind of
gap STEP 19/20's "verify, don't assume" principle warns against letting through unvalidated.

**Why not a new parameter type**: the obvious fix looked like a new `corner_position` parameter type, but
`video-production-agent`'s pinned `MotionGraphicsAdapter.check_contract()` hard-rejects any parameter whose
`type` is not in its own fixed `PARAMETER_TYPES` tuple (`string`, `integer`, `number`, `boolean`, `color`,
`position`, `font`, `path`) — introducing a new type would have made the *entire* contract incompatible with the
already-shipped agent-side integration (confirmed by actually running that repository's pinned compatibility
checks against a live contract with a `corner_position` type: `check_contract()` produced errors where before, and
after this ADR's approach, it produces none — see `CLAUDE.md`'s reproduction recipe). Instead, `position` here is
declared as `{"type": "string", "enum": CORNER_POSITION_NAMES}`: `model._element()`'s existing generic `string`
handling gained one new check (`enum` membership, mirroring the check `_animation()` already did for animation
parameters) rather than a new branch; the agent's own `_typed()` for `"string"` already checks `enum` the same
way, so this needed no changes on that side at all, and was verified compatible without editing
`video-production-agent`.

## ADR-12: `chapter` is implemented; it exposes `primary_color`, not `text_color`

**Decision**: `chapter` (a small chip in a corner, e.g. `"Part 2 -- Setup"`, `ffmpeg-skill/graphics --template
chapter`) is implemented and removed from `UNSUPPORTED_ELEMENT_TYPES`, the same follow-up ADR-8 anticipated. It
shares its argv construction with `bug` in `executor._argv()` (both are `ffmpeg-skill/graphics`'s
`elif args.template in ("chapter", "bug")` branch, taking the same `--title`/`--position` shape), gets the same
`builtin_fade` animation, and its own capability id `motion_graphics.chapter` (added to `contract.CAPABILITY_IDS`,
provisional for the same reason `motion_graphics.bug` is -- ADR-11).

**Why `primary_color`, not `text_color`, and why default position differs from `bug`**: reading
`ffmpeg-skill/graphics.py`'s shared `chapter`/`bug` branch line by line (not assumed to mirror `bug` by analogy)
shows the two templates are *not* symmetric in which color each accepts:

```python
pos = args.position or ("bottom-left" if args.template == "chapter" else "top-right")
box_color = ff_color(primary if args.template == "chapter" else bg, ...)
txt_color = ff_color(bg if args.template == "chapter" else text_c)
```

For `chapter`: the default position is `bottom-left` (not `bug`'s `top-right`); the chip's *box* color comes from
`--primary`/brand primary (`args.primary`); the chip's *text* color is always the brand background color,
completely unaffected by `--text-color` -- `args.text_color` is read into `text_c`, but `text_c` is only used in
the `else` (i.e. `bug`) branch. So `chapter`'s typed parameters are `title`/`position`/`primary_color`, deliberately
*not* `text_color` -- exposing a `text_color` parameter that silently has zero effect on the rendered output would
be exactly the kind of dishonest metadata this Skill's contract is designed never to publish (`model.py` module
docstring: "never guess, never silently repair, never claim support that isn't backed by a real renderer").
`test_chapter_has_no_text_color_parameter` in `tests/test_unit.py` pins this down as a regression test (a
`text_color` key on a `chapter` element must be rejected as an unknown parameter, not silently accepted and
ignored).

Verified end-to-end the same way as ADR-11: `video-production-agent`'s pinned `check_contract()` and
`RealSkillTests`, and the ecosystem registry's `validate_provides_entry()`, all still pass with `chapter` added
(see `CLAUDE.md`'s reproduction recipe).

## ADR-13: `progress` is implemented; it has no `title`, no `position`, and no animation at all

**Decision**: `progress` (a thin bar along the bottom of the frame that fills left-to-right over the element's
own `start`/`end` window, `ffmpeg-skill/graphics --template progress`) is implemented and removed from
`UNSUPPORTED_ELEMENT_TYPES`, the third follow-up ADR-8 anticipated. Its only parameter is `primary_color`
(optional); its `animation` field is `"none"` (not `"builtin_fade"`, and not `"configurable"`) -- `_animation()`'s
existing check (`spec.get("animation") != "configurable"` -> reject any `animation` field at all) already treats
any non-`"configurable"` value identically, so this needed no new validation code, only an honest value in
`model.ELEMENT_TYPES`. Its capability id is `motion_graphics.progress` (provisional, same reasoning as
`motion_graphics.bug`/`motion_graphics.chapter`, ADR-11).

**Why no `title`/`position`, and why `executor._argv()` needed no new branch beyond documenting that progress
falls through it**: reading `ffmpeg-skill/graphics.py`'s `progress` branch shows it never reads `--title`,
`--text-color`, or `--position` at all -- the bar is always full-width, always along the bottom row, driven only
by the shared `--start`/`--end`/`--crf`/`--preset` flags every `graphics` template gets plus `--primary` (already
generic in `executor._argv()`'s trailing color-passthrough block). So `progress` simply falls into the same
`if/elif` chain in `_argv()` with no type-specific branch body at all, rather than because one was written for
it; `test_progress_has_no_title_or_position_parameter` in `tests/test_unit.py` pins down that `title`/`position`/
`text_color` are rejected as unknown parameters for `progress` (none of them would have any effect if silently
accepted).

**Why the animation is objectively "none", not merely "not configurable"**: unlike `title`/`bug`/`chapter`
(each wrapped in a `fade_a` alpha expression, `"builtin_fade"`), `progress`'s filter chain
(`color`+`drawbox`+`overlay`) never references `fade_a` or any alpha expression at all -- the bar's own linear
fill (`-w + w*min(1,max(0,(t-s)/(e-s)))`) is its only motion, driven by the timeline itself, not an opacity curve.
Documenting it as `"builtin_fade"` would have been a factual error about what the renderer actually does, not
just an omission; `"none"` is the honest value. `test_progress_bar_fills_left_to_right_over_time` in
`tests/test_integration.py` verifies the fill actually progresses (an early x-region shows the bar well before a
late x-region does, at real, measured points in time on a real render) -- an objective check of the one thing
this element type is actually for, the same standard ADR-11/ADR-12's corner-placement checks hold `bug`/`chapter`
to.

Verified end-to-end the same way as ADR-11/ADR-12: `video-production-agent`'s pinned `check_contract()`, and the
ecosystem registry's `validate_provides_entry()`, both still pass with `progress` added (see `CLAUDE.md`'s
reproduction recipe).
