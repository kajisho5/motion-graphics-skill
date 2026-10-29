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

## ADR-8: `shape` is not implemented in this contract; it needs a design decision here, not a new ffmpeg-skill tool

**Decision**: `shape` is declared in `model.UNSUPPORTED_ELEMENT_TYPES` and never appears as `supported` in
`contract`/`doctor`. (`chapter`, `bug`, `progress`, and `countdown` were in this list too, at various points,
until ADR-11/ADR-12/ADR-13/ADR-14 implemented them one PR at a time. ADR-19 later implemented `sticker`, `hook` and
`meme`, graphics templates ffmpeg-skill added after this ADR was written, so "every template `ffmpeg-skill/graphics`
exposes" was true when ADR-14 landed and has to be re-checked against the checkout whenever ffmpeg-skill releases.)

**Corrected 2026-10-29 -- the original reason was wrong.** This ADR first said `shape` has "no typed delegate anywhere
upstream" and "is blocked on `ffmpeg-skill` gaining a typed shape tool first". That is not so. `ffmpeg-skill/background.py`
(0.11.0: a silent, exact-size solid-colour or gradient clip generated from no input) plus `ffmpeg-skill/overlay.py
--video` (a positioned, scaled, opacity-controlled, time-bounded layer) draw a rectangle with no raw filter
construction, so ADR-1 holds. Checked on ffmpeg-skill 2.10.8: a 300x40 `background.py` clip composited with
`overlay.py --video --position 40,300 --opacity 0.85 --start 1 --end 3` gives a semi-transparent rectangle at that
position in exactly that window. Issue #10 item 1 made the same observation. Not checked: rounded corners, a border,
rotation -- a rectangle is all that composition is known to give.

**What actually blocks `shape`**: `executor._argv()`, the stage identity (ADR-5/ADR-6) and the per-element `StageResult`
all assume one delegate call per element, and a rectangle is two (generate the clip, then composite it). Whether it
becomes two stages for one element, or one stage with a derived intermediate, and how the generated clip is identified
and cached, is a design decision to make before touching `model.py`/`executor.py`. It is not a missing upstream
capability, and building a filter string here to avoid the question is still ruled out.

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

## ADR-14: `countdown` is implemented; its built-in animation is `"builtin_pulse"`, a new, honest label

**Decision**: `countdown` (big centered numbers counting down from `count_from` to 0 across the element's own
`start`/`end` window, `ffmpeg-skill/graphics --template countdown`) is implemented and removed from
`UNSUPPORTED_ELEMENT_TYPES` -- the fourth and, per ADR-8, last of `ffmpeg-skill/graphics`'s extra templates to be
implemented here. Parameters: `count_from` (optional, default 5, bounded `[1, 60]`) and `primary_color`
(optional); no `title`, no `position` (always centered) -- `graphics.py`'s countdown branch never reads
`--title`/`--text-color`/`--position` at all. Its capability id is `motion_graphics.countdown` (provisional, same
reasoning as the other three, ADR-11).

**Why `count_from` is bounded `[1, 60]`**: `graphics.py` itself places no bound on `--from` (`type=int` only);
`n=0` degenerates to a single static digit for the whole window (not a crash, but not really a "countdown"
either), and an unbounded `n` would let a request make this Skill build an arbitrarily long chain of per-digit
`drawtext` filters -- the same class of concern `MAX_ELEMENTS` already bounds for the request as a whole, just
at the single-element level here. `60` is a generous ceiling for any real countdown use (a full minute, one
digit per second) while still being a fixed, auditable bound rather than "whatever an argparse `int` allows".

**Why the animation is `"builtin_pulse"`, a value that didn't exist before this PR**: `graphics.py` gives each
digit its own alpha curve, `1-0.15*min(1,(t-ks)/(seg*0.5))` -- a brief opacity dip at the start of its own
segment, fixed and not exposed by any CLI flag. This is a real, distinct built-in animation: not `title`/`bug`/
`chapter`'s `fade` (a smooth in/out over the whole element), and not `progress`'s total absence of one (`"none"`,
ADR-13). Folding it into either existing label would have been factually wrong about what the renderer actually
does; introducing `"builtin_pulse"` costs nothing in `check_contract()` terms (that check never inspects an
element type's `animation` value at all, only `_animation()`'s own `!= "configurable"` gate, which treats every
non-configurable value identically) but keeps the contract honest about what `doctor`/an agent would actually see
if it played the output back.

Verified end-to-end the same way as ADR-11/ADR-12/ADR-13: `video-production-agent`'s pinned `check_contract()`,
and the ecosystem registry's `validate_provides_entry()`, both still pass with `countdown` added (see
`CLAUDE.md`'s reproduction recipe). After this PR, `shape` (ADR-8) was the only element type from the original
design brief's full list still unimplemented (ADR-8 was later corrected: that is a design question here, not an
upstream gap).

## ADR-15: `video_overlay` is implemented; its animation is `"none"`, not `"configurable"`, and it gets its own Capability id

**Decision**: `video_overlay` (a second video composited as a picture-in-picture layer, with optional chroma-key
(green-screen) removal, `ffmpeg-skill/overlay --video`/`--chromakey*`) is implemented as a new element type. Its
typed parameters mirror `image_overlay`'s position/margin/scale/opacity model almost exactly (`video_path` instead
of `image_path`; the same 9-way `position` type, not `bug`/`chapter`'s corner-only enum) plus three new ones:
`chromakey` (a `color`, optional), `chromakey_similarity` and `chromakey_blend` (both `number`, optional, bounded
to the same ranges `ffmpeg-skill/overlay` itself validates). Its `animation` is `"none"`, and its Capability id is
its own, `motion_graphics.video_overlay` (not shared with `text_overlay`/`image_overlay`'s `motion_graphics.overlay`).

**Why this closes issue #10 item 2**: `ffmpeg-skill` 0.11.0 shipped `overlay.py --video` (position/scale/opacity,
same knobs as `--image`) and `--chromakey`/`--chromakey-similarity`/`--chromakey-blend`, but this Skill's adapter
only ever called the `--image`/`--text` branches — a capability ownership gap this Skill's own README already
claims as its territory (titles, lower-thirds, overlays, watermarks; webcam/screen-recording PiP and green-screen
title sequences are motion-graphics work, not video-editing's cut/trim job).

**Why the animation is `"none"`, not `"configurable"`**: reading `ffmpeg-skill/overlay.py`'s `--video` branch line
by line (not assumed to mirror `--image` by analogy) shows it never references `args.fade`, `alpha_expr`, or any
fade filter term at all — only the `--image` and `--text` branches do. Exposing a configurable `fade` here would be
exactly the kind of dishonest metadata ADR-12 already ruled out for `chapter`'s `text_color`: a parameter that
silently has zero effect on the rendered output. `test_argv_for_video_overlay_never_emits_fade` and
`test_video_overlay_does_not_accept_configurable_animation` in `tests/test_unit.py` pin this down.

**Why its own Capability id, not `motion_graphics.overlay`**: that matrix already treats `text_overlay`/
`image_overlay` as one capability ("free-form text, image/logo overlay") because they are the same kind of thing
composited two different ways. A video-on-video PiP/chroma-key layer is a materially different capability, not a
third variant of "free-form text, image/logo overlay" — same reasoning `bug`/`chapter`/`progress`/`countdown`
already applied when each got its own id (ADR-11). `motion_graphics.video_overlay` is this repository's own
provisional choice, same status as those four (ADR-10).

**Why no fixed image-extension-style whitelist for `video_path`**: `image_overlay.parameters.image_path` is
checked against a narrow, honest extension list (`executor.IMAGE_EXTENSIONS`, PNG/JPG only) because that
genuinely covers the vast majority of still-image overlay assets. A PiP layer's own container can legitimately be
almost anything `ffmpeg-skill/overlay --video` itself accepts (mp4/mov/mkv/webm/...) — there is no similarly
narrow, honest list to enforce here without either rejecting valid inputs or inventing an arbitrary one (ADR-12:
never guess). Instead `Executor.response()` resolves and probes `video_path` up front, alongside every other
asset/font (the existing "a missing asset must fail before anything renders" pass), and requires the probed result
to actually have a video stream — the same real check `ffmpeg-skill/overlay --video` makes itself, just performed
early enough to fail before any stage renders rather than mid-pipeline.

**Required capabilities include `filter:chromakey`, unconditionally**: `chromakey` is optional per element
instance, but `required_capabilities` is the union of everything any parameter combination of a type could need
(the same convention `filter:colorchannelmixer` already follows for `image_overlay`, needed only when
`opacity < 1`), not "always exercised". Unlike `filter:overlay`/`filter:scale`/`filter:colorchannelmixer`
(never named by any `ffmpeg-skill` tool's own required/optional capability list, so `doctor` can only ever report
them `"unknown"` — see the regression test for this in `test_unit.py`), `ffmpeg-skill/overlay`'s own contract
*does* declare `filter:chromakey` as an optional capability (`"when": "--chromakey"`), so `ffmpeg-skill doctor`
actually probes for it — `doctor.CORE_CAPABILITIES` gained `filter:chromakey` so this Skill's own `doctor` reports
real `supported`/`unsupported` for it instead of falling through to `"unknown"` by omission.

## ADR-16: `document.options.audio_stream` selects the source's audio track for every stage

**Decision**: `document.options.audio_stream` (optional integer, bounded `[0, 63]`) is threaded through
`executor._argv()` to `--audio-stream` on every `graphics`/`overlay` invocation in the pipeline, and included in
each stage's identity hash. Omitted entirely (no `--audio-stream` flag at all) when not given, which is exactly
the previous behaviour (both tools default to track 0).

**Why closes issue #10 item 3**: `ffmpeg-skill` 0.12.0 added `--audio-stream N` to exactly the two tools this
Skill delegates to, but neither `FLAGS_USED` nor `executor._argv()` ever emitted it — a multi-audio-track source
(dubbed languages, M&E stems) silently got track 0 with no way to select otherwise.

**Why a document-level option, not a per-element parameter**: there is only ever one source video per document
(`document.video.path`) — every stage in the pipeline reads from either that source or the previous stage's own
output, and `ffmpeg-skill/graphics`/`overlay` both apply `--audio-stream` against whatever their own `-i` input
actually is. A per-element `audio_stream` would let a caller ask for two different tracks of the *same* source
across two elements, which has no coherent meaning (the final output has exactly one audio track); a single,
document-wide choice matches `crf`/`preset`'s existing shape and the real constraint.

**Why bounded `[0, 63]` here and not validated against the actual track count**: unlike `crf`/`preset` (whose
valid range is fixed and known in advance), whether a given value is valid depends on how many audio streams the
*real* input actually has — knowable only by probing it, which `model.parse_request()` deliberately never does
(no file-system access at the request-parsing layer). The bound here only catches an integer typo structurally;
`ffmpeg-skill/{graphics,overlay}` both already `die` with a clear message when `--audio-stream` exceeds the real
input's track count, so an out-of-range value for the real input surfaces as a real `TOOL_ERROR` from the delegate
tool itself, not a guess made here (ADR-12). `63` is a generous ceiling in the same spirit as `countdown.count_from`'s
`[1, 60]` (ADR-14) — no real container carries anywhere near this many audio tracks.

## ADR-17: `dropped_non_av_streams` is read per stage and surfaced, never silently discarded

**Decision**: `adapter.ToolRun` gains a `dropped_non_av_streams: Optional[bool]` field, populated from each tool
response when the field is present and a real `bool` (`None` otherwise — an older `ffmpeg-skill` or a tool that
never reports it is not misrepresented as `false`). `executor.StageResult` carries the same field through to
`to_dict()`, included only when not `None`; `Executor.response()` adds one `warnings[]` entry per stage where it
was `true`.

**Why**: `ffmpeg-skill` 0.12.1 added `dropped_non_av_streams` to `graphics.py`/`overlay.py`'s `--json` response —
`true` only when that tool's own subtitle/data-stream preservation attempt failed and it fell back to a
video+audio-only re-encode for that invocation, silently losing the source's subtitle/data stream(s) for that
stage. `adapter.FfmpegSkill.run_tool()` already captured the full response dict, but `executor.py` only ever read
`run.commands` from it — the signal reached this process and was then thrown away. Contradicts this Skill's own
stated philosophy (ADR-12: "never guess, never silently repair, never claim support that isn't backed by a real
renderer") in the specific direction of never claiming a *loss* did not happen, just as much as never claiming a
capability exists that doesn't.

**Why per-operation AND top-level, not just one or the other**: `operations[].dropped_non_av_streams` answers
"which stage, specifically" (useful for a multi-element pipeline where only one stage's own subtitle preservation
failed); the top-level `warnings[]` entry is what a caller reading only the top level of the response — the same
level `ok`/`status`/`output` live at — would actually see without inspecting every operation. Neither one alone
matches how the rest of this response is already shaped (`operations[]` is per-stage detail; `warnings[]` already
existed as the top-level catch-all, previously always empty).

**Why `false` is never itself a warning**: only an actual, observed loss (`true`) is worth surfacing — reporting
`dropped_non_av_streams: false` as a warning on every single render would train a caller to ignore `warnings[]`
altogether, the opposite of what a warning is for. `test_dropped_non_av_streams_false_is_not_reported_as_a_warning`
in `tests/test_unit.py` pins this down.

## ADR-18: no ffmpeg-skill version window; what the located checkout can do is detected

**Decision**: `adapter.FfmpegSkill.info()` no longer compares ffmpeg-skill's version number with a window
(`SUPPORTED_MIN = 0.12.1`, `SUPPORTED_MAX_EXCLUSIVE = 1.0.0` are gone) and no longer refuses a `contract_version`
other than `"1.0"` (it is reported). Instead it reads the flags each tool takes — from `scripts/_contract.py --json
--static`, then `_contract.py --json`, then `scripts/contract.py --json`, and finally each tool's own `--help` — and
`adapter.check_tools()` decides:

- `adapter.REQUIRED_FLAGS` must all be present (they are what `executor._argv()` emits unconditionally); a missing
  one is still a `TOOL_ERROR` naming the flag. Together they imply ffmpeg-skill >= 0.12.0, so that is the effective
  floor, but it is detected, never computed from a version string.
- `adapter.FLAG_ALTERNATIVES`: the encoder-quality flag is `--quality` when offered (ffmpeg-skill 1.10+; 2.0 removed
  `--crf` from `graphics`/`overlay`), otherwise `--crf`. Same CRF scale and default either way, so
  `document.options.crf` keeps its meaning.
- `adapter.OPTIONAL_FLAGS`: `--overwrite` is passed when offered (ffmpeg-skill 1.10+ refuses to replace an existing
  output without it; older releases reject the flag). This skill's own guard (`document.output.overwrite`, checked
  before anything renders) is the consent; stale intermediates are deleted before their stage runs.
- `dropped_non_av_streams` (0.12.1) is a response field: read when present, omitted when absent (ADR-17), so it
  needs no gate.
- `overlay`'s colour flags get a `0x` prefix on a bare `RRGGBB` (`executor._overlay_color`): ffmpeg-skill 2.x
  validates them and accepts hex only as `0xRRGGBB`/`#RRGGBB`; older releases hand the value to ffmpeg, which reads
  `0xRRGGBB` as the same colour.
- A checkout without `_contract.py doctor` gets `unknown` (not `unsupported`) for ffmpeg/filter capabilities.

`doctor --json` reports what was detected under `checks.ffmpeg_skill.detected` (`source`, `encoder_quality_flag`,
`overwrite_flag`, `common_layout`, `removed`). The contract keeps `ffmpeg_skill.version_window` as an object (a
downstream adapter checks its shape) with both bounds `null`, and adds `compatibility`, `required_flags`,
`flag_alternatives` and `optional_flags`.

**Why**: the upper bound refused every ffmpeg-skill release from 1.0.0 on ("ffmpeg-skill 2.10.3 is outside the
supported window [0.12.1, 1.0.0)") even though the only real breaking changes for this skill were the `--crf`
rename, the `--overwrite` requirement and the stricter colour validation, all handled above. A version number says
nothing about which flags exist; the contract does. CI runs the suite against ffmpeg-skill `main` on every push and
daily, and against the oldest verified commit (0.12.5), so both ends of the detected range stay exercised.
`tests/test_compat.py` pins the detection against fake 0.x, 1.10-1.x, 2.x, future and contract-less checkouts.

## ADR-19: `sticker`, `hook`, `meme` and `document.options.platform`; templates and flags newer than 0.12.5 are detected, not assumed

**Decision**: three more `ffmpeg-skill/graphics` templates become element types, and one new document option is added.

- **`sticker`** (`--template sticker`): `text` (required), `position` (the same four-corner closed vocabulary as `bug`/`chapter`,
  default `top-right`) and `primary_color` (the chip). No `text_color`: graphics.py draws the chip's text in the brand
  background colour and never reads `--text-color` for this template (the ADR-12 reasoning for `chapter`). Animation
  `"builtin_pop"`: the chip fades up while rising 0.25 s, and fades out over the last 0.3 s.
- **`hook`** (`--template hook`): `title` (required), `text_color`, `primary_color`. A full-width band with the title, and a
  thin bar along the top that empties across the card's window. The template has its own `--duration` (how long the card
  stays up from `--start`, default 3 s); this skill sends `end - start`, the one length it knows, so the card and its bar
  span exactly the element's window and a 5 s element is not silently cut to 3 s. Animation `"none"`, as `progress` (ADR-13): the
  bar is driven by the timeline, and the band and title never fade.
- **`meme`** (`--template meme`): `top` and `bottom`, **at least one** required. That cross-field rule is a new
  `at_least_one_of` entry in `ELEMENT_TYPES`, enforced in `_element()` and published in the contract as `constraints`.
  Colours, size and position are fixed by the template, so none are exposed. Animation `"none"`.
- **`document.options.platform`**: one of `PLATFORM_NAMES` (`broadcast`, `facebook`, `instagram`, `linkedin`, `podcast`,
  `reels`, `shorts`, `tiktok`, `twitter`, `youtube`, `youtube-shorts`), sent as `--platform` to every `graphics` and `overlay`
  stage. ffmpeg-skill then takes each edge's margin from that destination's safe zone (`broadcast` is 5 % on every edge;
  a top-left logo clears TikTok's status bar). One value per document, like `audio_stream`: there is one output, so one
  destination. Aliases (`fb`, `ig`, `x`, `yt`, `yt-shorts`) are not accepted, so a destination has one spelling, and nor is
  `custom` ("no safe zone"), which is the same as leaving the option out. Included in the stage identity.

**Margins under `platform`**: ffmpeg-skill applies the safe zone only when no margin is given. `graphics` templates never
receive a `--margin` from this skill, so they follow the platform directly. The overlay element types had a materialised
default (`margin: 24`) that was always sent, which would have made the option do nothing for them; so under
`options.platform` an omitted `margin` is left absent (`"platform_replaces_default"` in the parameter table) and no
`--margin` is sent, while a `margin` the request gives is sent. Without `options.platform` nothing changes: the default is still
materialised, so existing documents keep their parameters and identities. Observed on ffmpeg-skill 2.10.8: `overlay.py` tells
"given" from "not given" by comparing `--margin` with its own default (24), so an explicit `margin: 24` still gets the safe
zone. That is upstream behaviour and is stated in the contract (`platforms.effect`) rather than hidden or pinned by a test.

**These need ffmpeg-skill newer than 0.12.5** (they arrived together in ffmpeg-skill's delivery-templates change, #224), and 0.12.5 is the
oldest checkout this skill is verified against, so they are *detected*, as ADR-18 does for `--quality` and `--overwrite`:

- `adapter.TEMPLATE_FLAGS` names the extra flags each template needs (`text`; `duration`; `top`+`bottom`).
  `check_tools()` records `template_support[name]`: `True` when the name is in the checkout's `--template` choices and its flags
  exist, `False` when either is missing, `None` when the choices cannot be read. The choices come from the contract's `enum`, or
  from `--help` (`flags_from_help` now reads `--template {a,b}` and `--platform {a,b}`).
- `--platform` is an `OPTIONAL_FLAGS` entry; `check_tools()` records `platform_flag` and `platforms` per tool.
- None of this is a compatibility *problem*: an old checkout stays usable for every other element type, so `info.supported` is unchanged.
- Each new element type lists a `graphics-template:<name>` required capability, which `doctor` derives from `template_support`, so on an
  old checkout `doctor` reports `sticker`/`hook`/`meme` `unsupported` and the overall status `degraded`.
- `Executor._check_checkout()` runs for `run`, `plan` and `run --dry-run`, before anything renders. A request for an element type whose
  template is `False`, or for `options.platform` when the checkout has no `--platform` flag (or not that destination), is a `TOOL_ERROR`
  with `retryable: false` and `details.reason` `ffmpeg_skill_lacks_template` / `ffmpeg_skill_lacks_platform_flag` /
  `ffmpeg_skill_lacks_platform`. A template whose support is `None` is attempted, not refused. Refusing up front matters because
  the alternative is a stage failing inside ffmpeg-skill after earlier stages already rendered, with an argparse message.

**Capability ids**: `motion_graphics.{sticker,hook,meme}`, this repository's own provisional choices like ADR-11..15's, to reconcile
if the OS registry assigns others.

**Verification**:

- `tests/test_templates_platform.py` (request validation, argv, detection against synthetic contracts/`--help`, the up-front
  refusal with a fake checkout that records every call, doctor and contract) and real-media tests in `tests/test_integration.py`:
  crop windows measured on the fixture, drawn windows 40-116 mean |luma| from the source and control windows under 1. They assert what
  the checkout under test can do, so the 0.12.5 CI job checks the refusal instead of skipping.
- Full suite 397 passed against ffmpeg-skill 2.10.8 and against 0.12.5 (`87c84f2`); on 0.12.5 `doctor` reports `hook`, `meme`, `sticker`
  unsupported and `degraded`.
- `video-production-agent`'s pinned `check_contract()`: `[]`. `contract_drift()` gains exactly `element_types hook/meme/sticker: installed
  but not pinned`, additive. (Its `RealSkillTests::test_real` asserts zero drift and already failed on unmodified `main` for three older
  drift entries, `tools`, `ffmpeg_skill` and `request`; this change does not add a failure.)

