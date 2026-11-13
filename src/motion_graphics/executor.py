"""Request -> validated Graphics Document -> render plan -> ffmpeg-skill execution -> validated output -> response.

Every element is rendered as one full-frame ffmpeg-skill invocation (a `shape` element first generates its rectangle
clip with ffmpeg-skill/background, a derived input with its own identity and cache entry: ADR-22), applied in timeline order (docs/decisions.md)
to the output of the previous element (or to the source video for the first element). All but the last element
write into an identity-named cache file under `<workspace>/.motion-graphics/<document_id>/` and are reused when an
existing file's manifest and sha256 match (STEP 18); the last element always (re)writes the requested output path,
which is fully re-validated afterwards regardless of reuse (STEP 8 / STEP 19). No step ever passes a raw ffmpeg
filter, command, or expression across the ffmpeg-skill boundary: every argv value is a validated number, a closed
vocabulary string (position/color/preset), or a path resolved by PathPolicy (security.py)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import GENERATOR_TOOLS, TEMPLATE_FLAGS, FfmpegSkill, fmt_number, fmt_seconds
from .canonical import sha256_file, stable_hash
from .colors import template_color
from .errors import MotionGraphicsError
from .fonts import ResolvedFont, resolve_font
from .layerexport import ALPHA_CODECS, MANIFEST_NAME, alpha_step, layer_file_name, manifest_document, retag_for
from .layers import has_alpha, layer_placements, place_args
from .regions import element_boxes, overlaps, resolve_regions
from .textfit import fit as fit_text
from .kit import load_kit, warnings_for_kit
from .themes import load_theme, warnings_for
from .titlejson import resolve_title_json
from .model import ELEMENT_TYPES, GraphicsElement, parse_request
from .security import PathPolicy

RESPONSE_SCHEMA_ID = f"{SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION}"
MANIFEST_SCHEMA = f"{SKILL_ID}/manifest@1"
WORK_DIR_NAME = ".motion-graphics"
DURATION_TOLERANCE = 0.25  # seconds; each stage re-encodes with a forced constant frame rate (ffmpeg-skill cfr_args)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
# ADR-30: the transparent canvas every layer is drawn on is ProRes 4444 (ffmpeg-skill's default alpha line): a YUV picture with alpha, so
# each element is drawn on a picture of the same kind as the video it is burned into; the layers themselves are in the requested codec
CANVAS_ALPHA_CODEC = "prores4444"


@dataclass
class StageResult:
    index: int
    element_id: str
    type: str
    tool: str
    status: str  # "rendered" | "reused"
    identity: str
    parameters: Dict[str, Any]
    input_hashes: List[str]
    output_hash: Optional[str]
    path: str
    seconds: float
    tool_commands_observed: List[str]
    # ffmpeg-skill 0.12.1's dropped_non_av_streams, read straight off that stage's own tool response: True only
    # when graphics/overlay's subtitle/data-stream preservation attempt failed and it silently fell back to a
    # video+audio-only re-encode for this stage. None for a "reused" stage (no tool ran) or an older ffmpeg-skill
    # that never reports the field at all -- never guessed, per ADR-12/ADR-16 ("never claim support that isn't
    # backed by a real renderer").
    dropped_non_av_streams: Optional[bool] = None
    # ADR-22: a shape element's generated clip -- {tool, operation_id, status, output_hash, parameters, seconds,
    # tool_commands_observed}; None for every other element type
    derived: Optional[List[Dict[str, Any]]] = None
    # ADR-29: the font ffmpeg-skill reports it drew with (its `font_used`: family, file name, how it was found), when it reports one
    font_used: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"index": self.index, "element_id": self.element_id, "type": self.type, "tool": f"ffmpeg-skill/{self.tool}",
             "status": self.status, "operation_id": self.identity, "parameters": self.parameters,
             "input_hashes": self.input_hashes, "output_hash": self.output_hash, "seconds": self.seconds,
             "tool_commands_observed": self.tool_commands_observed}
        if self.dropped_non_av_streams is not None:
            d["dropped_non_av_streams"] = self.dropped_non_av_streams
        if self.derived is not None:
            d["derived"] = self.derived
        if self.font_used is not None:
            d["font_used"] = self.font_used
        return d


_BARE_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}(@.*)?$")


def _overlay_color(value: str) -> str:
    """A model colour (COLOR_RE: a name or bare RRGGBB, optional @alpha) as ffmpeg-skill/overlay's colour flags take
    it. Newer ffmpeg-skill releases validate those flags and accept hex only as 0xRRGGBB / #RRGGBB; older ones pass
    the value straight to ffmpeg, which reads 0xRRGGBB the same way. So a bare hex value gets the 0x prefix, which
    every version accepts with the same meaning; a colour name is passed unchanged."""
    return "0x" + value if _BARE_HEX_RE.match(value) else value


def _font_used(data: Any) -> Optional[Dict[str, Any]]:
    """ffmpeg-skill's `font_used` (graphics, overlay --text) without its absolute path: family, file name, how it was resolved."""
    fu = data.get("font_used") if isinstance(data, dict) else None
    if not isinstance(fu, dict):
        return None
    out = {k: fu[k] for k in ("family", "asked", "how", "substituted", "via") if k in fu}
    if isinstance(fu.get("file"), str):
        out["file"] = os.path.basename(fu["file"])
    return out


class Executor:
    def __init__(self, policy: PathPolicy, skill: FfmpegSkill, *, dry_run: bool = False, reuse: bool = True,
                 timeout: Optional[float] = None, tool_versions: Optional[Dict[str, str]] = None,
                 capabilities: Optional[Dict[str, str]] = None):
        self.policy = policy
        self.skill = skill
        self.dry_run = dry_run
        self.reuse = reuse
        self.timeout = timeout
        self.tool_versions = dict(tool_versions or {})
        self.capabilities = dict(capabilities or {})

    # ---- public entry point
    def response(self, document: Any) -> Dict[str, Any]:
        doc = parse_request(document)
        theme: Optional[Dict[str, Any]] = None
        if doc.options.get("themes"):
            # ADR-29: the active theme file is read (PathPolicy) and the document parsed again with it applied
            opts = doc.options["themes"]
            theme = load_theme(self.policy, opts["themes"][opts["theme"]], f"document.options.themes.{opts['theme']}")
            theme["label"] = opts["theme"]
            doc = parse_request(document, theme)
        kit_record: Optional[Dict[str, Any]] = None
        if doc.options.get("kit"):
            # ADR-31: a kit@1 / brand.json file, converted by the Velisa mapping table onto a theme (all four colour roles) or the
            # brand_kit roles, then applied as that input is
            kit_record, theme = load_kit(self.policy, doc.options["kit"])
            if theme is not None:
                theme["label"] = "kit"
                doc = parse_request(document, theme)
            else:
                doc = parse_request(document, None, kit_record["brand_kit"])
        extra_warnings = (warnings_for(theme) if theme else []) + (warnings_for_kit(kit_record) if kit_record else [])
        video_path = self.policy.resolve_input(doc.video_path, "video")
        output_path = self.policy.resolve_write_path(doc.output_path, "output")
        if output_path == video_path:
            raise MotionGraphicsError("OUTPUT_ERROR", "document.output.path may not be the same file as document.video.path", {"reason": "output_is_input"})
        if output_path.exists() and not doc.overwrite:
            raise MotionGraphicsError("OUTPUT_ERROR", f"output already exists (set document.output.overwrite to replace it): {output_path}", {"reason": "exists", "path": str(output_path)})

        self._check_checkout(doc)
        layer_paths = self._layer_paths(doc, video_path, output_path)

        source_meta = self.skill.probe(str(video_path), self.timeout)
        video = source_meta.get("video") or {}
        if not video:
            raise MotionGraphicsError("INVALID_INPUT", "document.video.path has no video stream", {"path": str(video_path)})
        width, height = int(video["width"]), int(video["height"])
        duration = float(source_meta.get("duration") or 0.0)
        video_hash = sha256_file(str(video_path))

        ordered = doc.ordered_elements()
        for el in ordered:
            if el.end > duration + DURATION_TOLERANCE:
                raise MotionGraphicsError("INVALID_TIME_RANGE", f"elements[{el.element_id}].end ({el.end}s) is beyond the video duration ({duration:.3f}s)",
                                           {"element_id": el.element_id, "end": el.end, "duration": duration})

        # resolve every asset/font referenced by any element up front: a missing asset must fail before anything renders
        resolved_assets: Dict[str, Dict[str, Any]] = {}
        resolved_fonts: Dict[str, ResolvedFont] = {}
        title_records: Dict[str, Dict[str, Any]] = {}
        ordered = list(ordered)
        for idx, el in enumerate(ordered):
            if el.type == "image_overlay":
                image_path = self.policy.resolve_input(el.parameters["image_path"], f"elements[{el.element_id}].parameters.image_path")
                if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise MotionGraphicsError("UNSUPPORTED_FORMAT", f"elements[{el.element_id}].parameters.image_path must be one of {IMAGE_EXTENSIONS}: {image_path.suffix}",
                                               {"element_id": el.element_id, "extension": image_path.suffix})
                resolved_assets[el.element_id] = {"path": str(image_path), "sha256": sha256_file(str(image_path)), "size": image_path.stat().st_size}
            if el.type == "video_overlay":
                # Unlike image_overlay's fixed PNG/JPG whitelist, a PiP layer's own container can legitimately be
                # almost anything ffmpeg-skill/overlay itself accepts (mp4/mov/mkv/webm/...); this Skill has no
                # narrow, honest extension list to enforce the way it does for still images, so it defers to the
                # same real check ffmpeg-skill/overlay --video makes itself (probe it, require a video stream)
                # rather than guessing at a whitelist (ADR-12: never guess).
                video_overlay_path = self.policy.resolve_input(el.parameters["video_path"], f"elements[{el.element_id}].parameters.video_path")
                pip_meta = self.skill.probe(str(video_overlay_path), self.timeout)
                if not pip_meta.get("video"):
                    raise MotionGraphicsError("INVALID_INPUT", f"elements[{el.element_id}].parameters.video_path has no video stream: {video_overlay_path}",
                                               {"element_id": el.element_id, "path": str(video_overlay_path)})
                resolved_assets[el.element_id] = {"path": str(video_overlay_path), "sha256": sha256_file(str(video_overlay_path)), "size": video_overlay_path.stat().st_size,
                                                  "dims": (int(pip_meta["video"].get("width") or 0), int(pip_meta["video"].get("height") or 0))}
            if el.type == "logo":
                if theme is None or not theme.get("logo"):
                    raise MotionGraphicsError("INVALID_REQUEST", f"elements[{el.element_id}]: a logo element draws the active theme's logo, and "
                                              + ("no theme is set (options.themes / options.theme, or an options.kit with all four colour roles and a logo)" if theme is None else f"theme {theme['label']!r} has no logo"),
                                              {"element_id": el.element_id, "reason": "theme_without_logo"})
                logo_path = Path(theme["logo"]["path"])
                if logo_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise MotionGraphicsError("UNSUPPORTED_FORMAT", f"theme {theme['label']!r}: logo.path must be one of {IMAGE_EXTENSIONS}: {logo_path.suffix}",
                                              {"element_id": el.element_id, "extension": logo_path.suffix})
                resolved_assets[el.element_id] = {"path": str(logo_path), "sha256": sha256_file(str(logo_path)), "size": logo_path.stat().st_size}
            if theme is not None and theme.get("font") and ELEMENT_TYPES[el.type]["tool"] == "graphics":
                # the theme's font, for every graphics template (ffmpeg-skill/graphics --font / --font-file)
                resolved_fonts[el.element_id] = resolve_font(theme["font"], self.policy)
            if el.type == "rendered_layer":
                # ADR-25: the layer must decode with alpha; its duration decides where the IN / LOOP / OUT pieces go
                layer_path = self.policy.resolve_input(el.parameters["video_path"], f"elements[{el.element_id}].parameters.video_path")
                layer_meta = self.skill.probe(str(layer_path), self.timeout)
                lv = layer_meta.get("video") or {}
                if not lv:
                    raise MotionGraphicsError("INVALID_INPUT", f"elements[{el.element_id}].parameters.video_path has no video stream: {layer_path}",
                                              {"element_id": el.element_id, "path": str(layer_path)})
                if not has_alpha(lv.get("pix_fmt")):
                    raise MotionGraphicsError("INVALID_INPUT", f"elements[{el.element_id}].parameters.video_path does not decode with an alpha channel "
                                              f"(pixel format {lv.get('pix_fmt')!r}): give a ProRes 4444, QuickTime Animation or PNG-in-MOV layer "
                                              f"(a VP9 alpha WebM decodes without its alpha here); an opaque clip is a video_overlay",
                                              {"element_id": el.element_id, "path": str(layer_path), "pix_fmt": lv.get("pix_fmt"), "reason": "layer_without_alpha"})
                layer_duration = float(lv.get("duration") or layer_meta.get("duration") or lv.get("tagged_duration") or 0.0)
                layer_sha256 = sha256_file(str(layer_path))
                title_file: Optional[Dict[str, Any]] = None
                if "title_json" in el.parameters:
                    # ADR-33: the clip's title.json fills the in / out / loop the request left out (a stated value wins)
                    params, title_records[el.element_id], tj_warnings = resolve_title_json(
                        self.policy, el.element_id, el.parameters, layer_path, layer_sha256, layer_duration, float(lv.get("fps") or 0.0))
                    el = replace(el, parameters=params)
                    ordered[idx] = el
                    extra_warnings = extra_warnings + tj_warnings
                    title_file = title_records[el.element_id]["file"]
                pieces, summary = layer_placements(el.start, el.end, layer_duration, el.parameters["in_duration"], el.parameters["out_duration"],
                                                   el.parameters["loop"], f"elements[{el.element_id}]")
                resolved_assets[el.element_id] = {"path": str(layer_path), "sha256": layer_sha256, "size": layer_path.stat().st_size,
                                                  "pieces": pieces, "segments": summary, "dims": (int(lv.get("width") or 0), int(lv.get("height") or 0)),
                                                  **({"title_json": title_file} if title_file else {})}
            if el.type == "text_overlay" and el.parameters.get("font"):
                resolved_fonts[el.element_id] = resolve_font(el.parameters["font"], self.policy)
            elif el.type == "text_overlay":
                resolved_fonts[el.element_id] = resolve_font(None, self.policy)

        # ADR-27: a lower_third / title with `fit` is shrunk and broken for this frame size before anything renders
        fits: Dict[str, Dict[str, Any]] = {el.element_id: self._fit(el, width, height) for el in ordered if el.fit is not None}

        reserved = self._reserved_regions(doc, ordered, resolved_assets, fits, width, height, duration)

        document_id = stable_hash({"video_sha256": video_hash, "elements": [e.to_dict() for e in ordered]})[:16]
        fps = 0.0
        if layer_paths is not None:
            # ADR-30: the canvas is made at the source's frame rate, so each layer's frames are the programme's frames
            fps = float(video.get("fps") or 0.0)
            if not fps > 0:
                raise MotionGraphicsError("INVALID_INPUT", "document.options.layer_export: the frame rate of document.video.path could not be read, and the "
                                          "transparent canvas is made at that rate", {"reason": "no_frame_rate", "path": str(video_path)})

        if self.dry_run:
            timeline = [dict(self._plan_entry(i, el, resolved_assets, resolved_fonts), **({"fit_result": fits[el.element_id]} if el.element_id in fits else {}))
                        for i, el in enumerate(ordered)]
            return {"schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": True, "status": "ok", "dry_run": True,
                    "plan": {"document_id": document_id, "video": {"path": str(video_path), "sha256": video_hash, "duration": duration, "width": width, "height": height},
                             "output": {"path": str(output_path)}, "timeline": timeline},
                    **({"reserved_regions": reserved} if reserved else {}),
                    **({"theme": self._theme_record(theme)} if theme else {}),
                    **({"kit": kit_record} if kit_record else {}),
                    **({"title_json": title_records} if title_records else {}),
                    **({"layer_export": self._layer_plan(doc, ordered, layer_paths, resolved_assets, fits, width, height, duration, fps, video)}
                       if layer_paths is not None else {}),
                    "warnings": extra_warnings + self._region_warnings(reserved)}

        # A single-element document never has a non-final stage, so it never needs (or creates) the cache
        # directory at all -- it renders straight into the requested output (ADR-5, docs/decisions.md).
        # A shape element's generated clip is a derived input that also lives there (ADR-22), so a document with a shape
        # gets the directory even when it has one element.
        work_dir: Optional[Path] = None
        if len(ordered) > 1 or any(el.type == "shape" for el in ordered) or layer_paths is not None:
            work_dir = self.policy.resolve_work_dir(os.path.join(WORK_DIR_NAME, document_id))
            work_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)  # ffmpeg-skill does not create the output directory itself
        output_ext = output_path.suffix or ".mp4"

        stage_input = str(video_path)
        prev_identity = video_hash
        prev_duration = duration
        results: List[StageResult] = []
        any_reused = False
        crf, preset, audio_stream, platform = doc.options["crf"], doc.options["preset"], doc.options["audio_stream"], doc.options["platform"]

        for i, el in enumerate(ordered):
            is_last = i == len(ordered) - 1
            identity_input = self._identity_parameters(el, resolved_assets.get(el.element_id), resolved_fonts.get(el.element_id))
            if el.element_id in fits:
                identity_input = dict(identity_input, fit=fits[el.element_id])
            clip: Optional[Dict[str, Any]] = None
            if el.type == "shape":
                # the clip's identity is known before it exists, so the stage identity (and a reuse of the stage) never needs it rendered
                clip = {"identity": self._shape_clip_identity(el, crf, preset), "parameters": self._shape_clip_parameters(el)}
                identity_input = dict(identity_input, clip=clip["identity"])
            identity = stable_hash({"skill_version": VERSION, "tool_versions": self.tool_versions, "index": i, "previous": prev_identity,
                                     "type": el.type, "start": el.start, "end": el.end, "animation": el.animation.to_dict() if el.animation else None,
                                     "parameters": identity_input, "crf": crf, "preset": preset, "audio_stream": audio_stream, "platform": platform})
            if is_last:
                target = output_path
                manifest_path: Optional[Path] = None
            else:
                assert work_dir is not None
                target = work_dir / f"{identity[:16]}{output_ext}"
                manifest_path = target.with_name(target.stem + ".json")

            reused = False
            if not is_last and self.reuse and manifest_path is not None:
                reused = self._reusable(identity, target, manifest_path)
            if reused:
                assert manifest_path is not None
                any_reused = True
                out_hash = json.loads(manifest_path.read_text(encoding="utf-8"))["output_hash"]
                results.append(StageResult(i, el.element_id, el.type, ELEMENT_TYPES[el.type]["tool"], "reused", identity, identity_input, [prev_identity], out_hash, str(target), 0.0, []))
                stage_input, prev_identity = str(target), identity
                prev_duration = self._probe_duration_cached(target, manifest_path)
                continue

            for stale in (target, manifest_path) if manifest_path else (target,):
                if stale and stale.exists() and stale != output_path:
                    stale.unlink()
            derived: Optional[List[Dict[str, Any]]] = None
            asset = resolved_assets.get(el.element_id)
            if clip is not None:
                assert work_dir is not None
                made = self._shape_clip(el, clip, work_dir, output_ext, crf, preset)
                derived = [made["record"]]
                asset = {"path": made["path"]}
            tool, argv, tool_cwd = self._argv(el, stage_input, str(target), asset, resolved_fonts.get(el.element_id), crf, preset, audio_stream, platform,
                                              fit=fits.get(el.element_id))
            try:
                run = self.skill.run_tool(tool, argv, self.timeout, tool_cwd)
                artifact_meta = self._validate_stage_output(target, el, width, height, prev_duration)
            except MotionGraphicsError:
                self._remove_partial(target)
                if manifest_path:
                    self._remove_partial(manifest_path)
                raise
            out_hash = artifact_meta["sha256"]
            if manifest_path:
                manifest_path.write_text(json.dumps({"schema": MANIFEST_SCHEMA, "operation_id": identity, "type": el.type, "parameters": identity_input,
                                                      "input_hashes": [prev_identity], "output_hash": out_hash, "output_size": artifact_meta["size"],
                                                      "duration": artifact_meta["duration"], "tool": f"ffmpeg-skill/{tool}", "tool_versions": self.tool_versions,
                                                      "skill": SKILL_ID, "skill_version": VERSION}, indent=2, sort_keys=True), encoding="utf-8")
            results.append(StageResult(i, el.element_id, el.type, tool, "rendered", identity, identity_input, [prev_identity], out_hash, str(target),
                                        run.seconds, run.commands, run.dropped_non_av_streams, derived, _font_used(run.data)))
            stage_input, prev_identity, prev_duration = str(target), identity, artifact_meta["duration"]

        warnings: List[str] = extra_warnings + self._region_warnings(reserved)
        for eid, f in fits.items():
            if not f["fits"]:
                warnings.append(f"elements[{eid}]: the text needs more than fit.max_lines lines even at the smallest scale ({f['scale']:g}); "
                                f"it is drawn whole on more lines (fit.overflow)")
            if f["forced_break"]:
                warnings.append(f"elements[{eid}]: a run of text with no break opportunity was wider than the box at the smallest scale and was "
                                f"broken between characters (fit.forced_break)")
        for r in results:
            if r.dropped_non_av_streams:
                warnings.append(f"elements[{r.element_id}] ({r.type}): ffmpeg-skill/{r.tool} could not preserve the source's subtitle/data stream(s) for this "
                                 f"stage and fell back to a video+audio-only re-encode (dropped_non_av_streams)")

        final_meta = self._probe_and_check(output_path, width, height, prev_duration)
        layer_result: Optional[Dict[str, Any]] = None
        if layer_paths is not None:
            assert work_dir is not None
            layer_result = self._export_layers(doc, ordered, layer_paths, resolved_assets, resolved_fonts, fits, width, height, duration, fps,
                                               work_dir, output_ext, crf, preset, platform, output_path, final_meta)
            warnings += [f"elements[{layer['id']}]: ffmpeg-skill/{layer['tool'].split('/')[-1]} did not report its transparent layer verified "
                         f"(alpha step: {(layer.get('alpha') or {}).get('reason') or ('ok' if (layer.get('alpha') or {}).get('ok') else 'missing')})"
                         for layer in layer_result["layers"] if layer["verified"] is not True]
        return {
            "schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": True, "status": "ok", "dry_run": False,
            "output": {"path": str(output_path), "sha256": final_meta["sha256"], "size": final_meta["size"], "duration": final_meta["duration"],
                       "width": width, "height": height},
            "timeline": [dict({"id": e.element_id, "type": e.type, "start": e.start, "end": e.end}, **({"style": e.style} if e.style else {})) for e in ordered],
            "operations": [r.to_dict() for r in results],
            "reused": any_reused,
            "engine": dict(self.tool_versions),
            "provenance": {
                "document_id": document_id, "video": {"path": str(video_path), "sha256": video_hash, "duration": duration, "width": width, "height": height},
                "assets": {eid: {"sha256": a["sha256"], "size": a["size"]} for eid, a in resolved_assets.items()},
                "fonts": {eid: f.to_provenance() for eid, f in resolved_fonts.items()},
                "operations": [r.to_dict() for r in results],
                "output_hash": final_meta["sha256"],
            },
            **({"reserved_regions": reserved} if reserved else {}),
            **({"theme": self._theme_record(theme)} if theme else {}),
            **({"kit": kit_record} if kit_record else {}),
            **({"title_json": title_records} if title_records else {}),
            **({"layer_export": layer_result} if layer_result is not None else {}),
            "warnings": warnings,
        }

    def _check_checkout(self, doc: Any) -> None:
        """Refuse, before anything renders, a request the located ffmpeg-skill cannot carry out: an element type built on a
        graphics template the checkout lacks (sticker/hook/meme need ffmpeg-skill releases newer than 0.12.5), or a
        document.options.platform it has no --platform flag (or no such destination) for. These are facts about the
        checkout, detected from its own contract/--help (adapter.check_tools), never guessed from a version number --
        and never left to fail inside the tool after earlier stages already rendered (ADR-19)."""
        if self.skill is None:
            return
        info = self.skill.info()
        for el in doc.ordered_elements():
            generator = ELEMENT_TYPES[el.type].get("generator")
            if generator and info.tool_support(generator) is False:
                raise MotionGraphicsError("TOOL_ERROR", f"elements[{el.element_id}]: the {el.type!r} element type needs ffmpeg-skill/{generator} with the flags "
                                          f"{list(GENERATOR_TOOLS[generator])}, which the ffmpeg-skill at {info.directory} (version {info.version}) does not have",
                                          {"reason": "ffmpeg_skill_lacks_tool", "element_id": el.element_id, "type": el.type, "tool": f"ffmpeg-skill/{generator}",
                                           "ffmpeg_skill_version": info.version}, retryable=False)
            template = ELEMENT_TYPES[el.type].get("template")
            if template in TEMPLATE_FLAGS and info.template_support(template) is False:
                raise MotionGraphicsError("TOOL_ERROR", f"elements[{el.element_id}]: the {el.type!r} element type needs ffmpeg-skill's graphics template {template!r}, "
                                          f"which the ffmpeg-skill at {info.directory} (version {info.version}) does not have",
                                          {"reason": "ffmpeg_skill_lacks_template", "element_id": el.element_id, "type": el.type, "template": template,
                                           "ffmpeg_skill_version": info.version}, retryable=False)
        for el in doc.ordered_elements():
            if el.type == "rendered_layer" and not info.takes_place():
                raise MotionGraphicsError("TOOL_ERROR", f"elements[{el.element_id}]: the 'rendered_layer' element type needs ffmpeg-skill/overlay --place, which the "
                                          f"ffmpeg-skill at {info.directory} (version {info.version}) does not have",
                                          {"reason": "ffmpeg_skill_lacks_flag", "element_id": el.element_id, "type": el.type, "flag": "ffmpeg-skill/overlay --place",
                                           "ffmpeg_skill_version": info.version}, retryable=False)
        layer_opts = doc.options.get("layer_export")
        if layer_opts is not None:
            support = info.layer_export()
            if not support.get("supported"):
                raise MotionGraphicsError("TOOL_ERROR", f"document.options.layer_export needs {['ffmpeg-skill/' + f.replace('.', ' --', 1).replace('_', '-') for f in support.get('missing') or []]} "
                                          f"(a transparent canvas, and graphics / overlay keeping its alpha), which the ffmpeg-skill at {info.directory} "
                                          f"(version {info.version}) does not have", {"reason": "ffmpeg_skill_lacks_flag", "flags": list(support.get("missing") or []),
                                                                                       "ffmpeg_skill_version": info.version}, retryable=False)
            codecs = support.get("alpha_codecs")
            for codec in (layer_opts["alpha_codec"], CANVAS_ALPHA_CODEC):
                if codecs is not None and codec not in codecs:
                    raise MotionGraphicsError("TOOL_ERROR", f"document.options.layer_export: the ffmpeg-skill at {info.directory} (version {info.version}) offers the alpha "
                                              f"codecs {codecs}, not {codec!r}", {"reason": "ffmpeg_skill_lacks_alpha_codec", "alpha_codec": codec, "known": codecs},
                                              retryable=False)
        platform = doc.options.get("platform")
        if platform is None:
            return
        for tool in sorted({ELEMENT_TYPES[el.type]["tool"] for el in doc.elements}):
            if not info.takes_platform(tool):
                raise MotionGraphicsError("TOOL_ERROR", f"document.options.platform is set, but the ffmpeg-skill at {info.directory} (version {info.version}) has no "
                                          f"--platform flag on its {tool} tool", {"reason": "ffmpeg_skill_lacks_platform_flag", "tool": f"ffmpeg-skill/{tool}",
                                                                                   "ffmpeg_skill_version": info.version}, retryable=False)
            known = info.platform_names(tool)
            if known is not None and platform not in known:
                raise MotionGraphicsError("TOOL_ERROR", f"document.options.platform {platform!r} is not a destination the ffmpeg-skill at {info.directory} "
                                          f"(version {info.version}) knows on its {tool} tool: {known}",
                                          {"reason": "ffmpeg_skill_lacks_platform", "tool": f"ffmpeg-skill/{tool}", "platform": platform, "known": known}, retryable=False)

    # ---- planning (no filesystem writes beyond none; probe is read-only)
    def _plan_entry(self, index: int, el: GraphicsElement, assets: Dict[str, Dict[str, Any]], fonts_: Dict[str, ResolvedFont]) -> Dict[str, Any]:
        d = el.to_dict()
        d["index"] = index
        d["tool"] = f"ffmpeg-skill/{ELEMENT_TYPES[el.type]['tool']}"
        if ELEMENT_TYPES[el.type].get("generator"):
            d["generator"] = f"ffmpeg-skill/{ELEMENT_TYPES[el.type]['generator']}"
        if el.element_id in assets:
            d["asset"] = {"sha256": assets[el.element_id]["sha256"], "size": assets[el.element_id]["size"]}
            if "segments" in assets[el.element_id]:
                d["segments"] = assets[el.element_id]["segments"]
        if el.element_id in fonts_:
            d["font"] = fonts_[el.element_id].to_provenance()
        return d

    # ---- identity (never includes an absolute path or any machine-specific value, STEP 13)
    @staticmethod
    def _identity_parameters(el: GraphicsElement, asset: Optional[Dict[str, Any]], font: Optional[ResolvedFont]) -> Dict[str, Any]:
        params = dict(el.parameters)
        if "image_path" in params and asset is not None:
            params["image_path"] = {"sha256": asset["sha256"], "size": asset["size"]}
        if "video_path" in params and asset is not None:
            params["video_path"] = {"sha256": asset["sha256"], "size": asset["size"]}
        if el.type == "logo" and asset is not None:
            params["logo"] = {"sha256": asset["sha256"], "size": asset["size"]}
        if "title_json" in params and asset is not None and asset.get("title_json"):
            params["title_json"] = dict(asset["title_json"])  # ADR-33: the file's content, not its path; the values it gave are in params
        if asset is not None and "segments" in asset:
            params["segments"] = asset["segments"]  # rendered_layer: where its pieces go (ADR-25), part of the identity
        if font is not None:
            params["font"] = font.to_provenance()
        return params

    def _encode_flags(self, tool: str, crf: int, preset: str, alpha: Optional[Tuple[str, str]] = None) -> List[str]:
        """The encoder flags of ffmpeg-skill/<tool> as the located checkout spells them: `--quality` (1.10+; 2.0 removed
        `--crf`) or `--crf` (older), same CRF scale either way; plus `--overwrite` where the checkout takes it (1.10+
        refuses to replace an existing output without it; older releases reject the flag). Overwrite consent was
        already checked against document.output.overwrite before anything rendered, and a stale intermediate is
        removed before its stage runs, so passing the flag never replaces a file this skill was not allowed to."""
        info = self.skill.info() if self.skill is not None else None
        if alpha is not None:
            # a layer (ADR-30): written in an alpha line, which has no x264 settings; the alpha flags take their place
            flags = ["--keep-alpha", "--alpha-codec", alpha[0], "--alpha-mode", alpha[1]]
        else:
            flags = [info.quality_flag(tool) if info is not None else "--crf", str(crf), "--preset", preset]
        if info is not None and info.takes_overwrite(tool):
            flags.append("--overwrite")
        return flags

    # ---- argv construction: every value is a validated number, a closed-vocabulary string, or a resolved path.
    # Returns (tool, argv, cwd): cwd is None except for a custom font_file (see the comment below).
    def _argv(self, el: GraphicsElement, stage_input: str, stage_output: str, asset: Optional[Dict[str, Any]],
              font: Optional[ResolvedFont], crf: int, preset: str, audio_stream: Optional[int] = None,
              platform: Optional[str] = None, fit: Optional[Dict[str, Any]] = None,
              alpha: Optional[Tuple[str, str]] = None) -> Tuple[str, List[str], Optional[str]]:
        """`alpha` (alpha codec, alpha mode) draws the element on a transparent canvas as a layer (ADR-30): the same call, with the alpha
        flags in place of the x264 ones and no --audio-stream (the canvas has no audio)."""
        p = el.parameters
        if alpha is not None:
            audio_stream = None
        if fit is not None:
            # the fitted texts (line breaks put in at the chosen break opportunities) replace the given ones; nothing is cut
            p = dict(p, **{key: "\n".join(t["lines"]) for key, t in fit["texts"].items()})
        spec = ELEMENT_TYPES[el.type]
        if spec["tool"] == "graphics":
            argv = [stage_input, "-o", stage_output, "--template", spec["template"],
                    "--start", fmt_seconds(el.start), "--end", fmt_seconds(el.end), *self._encode_flags("graphics", crf, preset, alpha)]
            if audio_stream is not None:
                argv += ["--audio-stream", str(audio_stream)]
            if platform is not None:
                argv += ["--platform", platform]
            if el.type == "title":
                argv += ["--title", p["title"]]
                if p.get("subtitle"):
                    argv += ["--subtitle", p["subtitle"]]
            elif el.type == "lower_third":
                argv += ["--name", p["name"]]
                if p.get("title"):
                    argv += ["--title", p["title"]]
            elif el.type in ("bug", "chapter"):
                # Same argv shape for both; each type's own parameter schema decides which of text_color/
                # primary_color exists at all, so the trailing block below sends only the one that actually
                # affects that template (see model.ELEMENT_TYPES comments for why they differ)
                argv += ["--title", p["title"], "--position", p["position"]]
            elif el.type == "countdown":
                argv += ["--from", str(p["count_from"])]
            elif el.type == "sticker":
                argv += ["--text", p["text"], "--position", p["position"]]
            elif el.type == "hook":
                # the template's own --duration is how long the card stays up from --start (default 3s); the element's
                # window is the one length this skill knows, so the card and its emptying bar span exactly that window
                argv += ["--title", p["title"], "--duration", fmt_seconds(el.end - el.start)]
            elif el.type == "meme":
                for flag, key in (("--top", "top"), ("--bottom", "bottom")):
                    if p.get(key):
                        argv += [flag, p[key]]
            # else: progress -- no --title/--position/--from at all (see model.ELEMENT_TYPES); only the trailing
            # primary_color passthrough below applies to it
            # graphics.py takes these as RRGGBB only: a colour name is sent as its RRGGBB (colors.template_color, ADR-20);
            # @alpha never gets this far (refused by model._color: these parameters' schemas say alpha: false)
            if p.get("text_color"):
                argv += ["--text-color", template_color(p["text_color"])]
            if p.get("primary_color"):
                argv += ["--primary", template_color(p["primary_color"])]
            if fit is not None:
                argv += ["--scale", f"{fit['scale']:g}"]
                if el.fit and el.fit.get("lang"):
                    argv += ["--lang", el.fit["lang"]]
            cwd_g: Optional[str] = None
            if font is not None:  # a theme's font (ADR-29); the same cwd-relative font file as text_overlay's (see below)
                if font.kind == "system":
                    argv += ["--font", font.engine_arg["font"]]
                else:
                    assert font.font_file_path is not None
                    argv += ["--font-file", Path(font.font_file_path).name]
                    cwd_g = str(Path(font.font_file_path).parent)
            return "graphics", argv, cwd_g

        argv = [stage_input, "-o", stage_output, "--position", p["position"]]
        if "margin" in p:  # absent only under document.options.platform with no explicit margin: the safe zone applies
            argv += ["--margin", str(p["margin"])]
        if el.type != "rendered_layer":  # its --place pieces say when it shows (overlay refuses --start/--end with --place)
            argv += ["--start", fmt_seconds(el.start), "--end", fmt_seconds(el.end)]
        argv += ["--opacity", fmt_number(p["opacity"]), *self._encode_flags("overlay", crf, preset, alpha)]
        if audio_stream is not None:
            argv += ["--audio-stream", str(audio_stream)]
        if platform is not None:
            argv += ["--platform", platform]
        if el.animation is not None and el.animation.kind == "fade":
            argv += ["--fade", fmt_number(el.animation.parameters["duration"])]
        cwd: Optional[str] = None
        if el.type == "text_overlay":
            argv += ["--text", p["text"], "--font-size", str(p["font_size"]), "--font-color", _overlay_color(p["font_color"]),
                     "--border", str(p["border_width"]), "--border-color", _overlay_color(p["border_color"])]
            if p.get("box"):
                argv += ["--box", "--box-color", _overlay_color(p["box_color"])]
            if font is not None:
                if font.kind == "system":
                    argv += ["--font", font.engine_arg["font"]]
                else:
                    # ffmpeg-skill embeds --font-file into a `fontfile=...` *filter* option (drawtext) by
                    # backslash-escaping special characters; a Windows drive letter's colon still trips up some
                    # ffmpeg builds there regardless of slash style (observed: "No option name near ...",
                    # "Invalid argument" -- the identical failure whether the colon reaches it escaped or not).
                    # Sidestepping the drive letter entirely is the fix that actually generalises: run this one
                    # invocation with its cwd set to the font's own directory and pass just the bare file name,
                    # which needs no escaping at all. stage_input/stage_output/--image are always absolute paths
                    # elsewhere in this argv, so they are unaffected by the cwd change.
                    assert font.font_file_path is not None
                    font_path = Path(font.font_file_path)
                    argv += ["--font-file", font_path.name]
                    cwd = str(font_path.parent)
        elif el.type == "shape":
            # the generated rectangle clip (ADR-22), composited at its own size: no --scale, no chroma key
            assert asset is not None
            argv += ["--video", asset["path"]]
        elif el.type in ("image_overlay", "logo"):
            assert asset is not None
            argv += ["--image", asset["path"]]
            if p.get("scale_width"):
                argv += ["--scale", str(p["scale_width"])]
            if p.get("scale_percent") is not None:
                argv += ["--scale-percent", fmt_number(p["scale_percent"])]
        elif el.type == "rendered_layer":
            assert asset is not None
            argv += ["--video", asset["path"], *place_args(asset["pieces"])]
            if p.get("scale_width"):
                argv += ["--scale", str(p["scale_width"])]
            if p.get("scale_percent") is not None:
                argv += ["--scale-percent", fmt_number(p["scale_percent"])]
        else:  # video_overlay
            assert asset is not None
            argv += ["--video", asset["path"]]
            if p.get("scale_width"):
                argv += ["--scale", str(p["scale_width"])]
            if p.get("scale_percent") is not None:
                argv += ["--scale-percent", fmt_number(p["scale_percent"])]
            if p.get("chromakey"):
                argv += ["--chromakey", _overlay_color(p["chromakey"]), "--chromakey-similarity", fmt_number(p["chromakey_similarity"]),
                         "--chromakey-blend", fmt_number(p["chromakey_blend"])]
        return "overlay", argv, cwd

    # ---- reserved regions (ADR-28)
    def _reserved_regions(self, doc: Any, ordered: Tuple[GraphicsElement, ...], assets: Dict[str, Dict[str, Any]], fits: Dict[str, Dict[str, Any]],
                          width: int, height: int, duration: float) -> Optional[Dict[str, Any]]:
        """Every element's box against every reserved region while both are on screen. Reported; with on_overlap "refuse", an
        overlap refuses the request before anything renders."""
        spec = doc.options.get("reserved_regions")
        if not spec:
            return None
        regions = resolve_regions(spec, width, height, duration)
        boxes = []
        for el in ordered:
            b, precision = self._element_boxes(el, assets, fits, width, height)
            boxes.append({"id": el.element_id, "type": el.type, "start": el.start, "end": el.end, "boxes": b, "precision": precision})
        found = overlaps(boxes, regions)
        report = {"on_overlap": spec["on_overlap"], "regions": [dict(r, box=list(r["box"])) for r in regions], "overlaps": found,
                  "element_boxes": [{"id": b["id"], "boxes": [list(x) for x in b["boxes"]], "precision": b["precision"]} for b in boxes],
                  "unmeasured": [b["id"] for b in boxes if b["precision"] == "unmeasured"]}
        if found and spec["on_overlap"] == "refuse":
            raise MotionGraphicsError("INVALID_REQUEST", f"{len(found)} element(s) overlap a reserved YouTube region while it is on screen: "
                                      + ", ".join(f"{o['element_id']} under {o['region']['kind']} {o['region']['id']} at {o['time'][0]:g}-{o['time'][1]:g}s" for o in found),
                                      {"reason": "reserved_region_overlap", "overlaps": found, "regions": report["regions"]})
        return report

    def _element_boxes(self, el: GraphicsElement, assets: Dict[str, Dict[str, Any]], fits: Dict[str, Dict[str, Any]], width: int, height: int) -> Tuple[List[Any], str]:
        """The boxes an element draws in at rest and their precision (regions.element_boxes); an image's size is probed once and kept."""
        asset = assets.get(el.element_id, {})
        dims = asset.get("dims")
        if el.type in ("image_overlay", "logo") and dims is None:
            meta = (self.skill.probe(asset["path"], self.timeout) or {}).get("video") or {}
            dims = (int(meta.get("width") or 0), int(meta.get("height") or 0)) if meta else None
            asset["dims"] = dims
        return element_boxes(el, width, height, dims if dims and all(dims) else None, fits.get(el.element_id))

    # ---- layer export (ADR-30)
    def _layer_paths(self, doc: Any, video_path: Path, output_path: Path) -> Optional[Dict[str, Any]]:
        """Where the layers and their manifest go, checked before anything renders: inside the workspace (PathPolicy), not the input or the
        programme, and an existing file replaced only with document.output.overwrite. None when no layer export is asked for."""
        opts = doc.options.get("layer_export")
        if opts is None:
            return None
        where = "document.options.layer_export.directory"
        directory = self.policy.resolve_write_path(opts["directory"], where, allow_dir=True)
        files: Dict[str, Path] = {}
        for el in doc.ordered_elements():
            files[el.element_id] = self.policy.resolve_write_path(os.path.join(str(directory), layer_file_name(el.element_id, opts["alpha_codec"])), f"layer of elements[{el.element_id}]")
        manifest = self.policy.resolve_write_path(os.path.join(str(directory), MANIFEST_NAME), "layer manifest")
        for path in list(files.values()) + [manifest]:
            if path in (video_path, output_path):
                raise MotionGraphicsError("OUTPUT_ERROR", f"a layer export file would be {'document.video.path' if path == video_path else 'document.output.path'}: {path}",
                                          {"reason": "layer_is_input" if path == video_path else "layer_is_output", "path": str(path)})
            if path.exists() and not doc.overwrite:
                raise MotionGraphicsError("OUTPUT_ERROR", f"layer export file already exists (set document.output.overwrite to replace it): {path}",
                                          {"reason": "exists", "path": str(path)})
        return {"directory": directory, "files": files, "manifest": manifest, "settings": dict(opts)}

    @staticmethod
    def _canvas_parameters(width: int, height: int, duration: float, fps: float, color_tags: Optional[str]) -> Dict[str, Any]:
        """What ffmpeg-skill/background --transparent is asked for: the source's size, rate and length, in the canvas's alpha line; and the
        colour tags ffmpeg-skill/color --retag then gives it (the programme's), which every layer drawn on it keeps."""
        return {"width": width, "height": height, "duration": fmt_seconds(duration), "fps": fmt_number(fps), "alpha_codec": CANVAS_ALPHA_CODEC,
                "color_tags": color_tags}

    def _layer_box(self, el: GraphicsElement, assets: Dict[str, Dict[str, Any]], fits: Dict[str, Dict[str, Any]], width: int, height: int) -> Dict[str, Any]:
        boxes, precision = self._element_boxes(el, assets, fits, width, height)
        if not boxes:
            return {"box": None, "box_precision": precision}
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
        return {"box": {"x": int(x0), "y": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)}, "box_precision": precision}

    def _layer_plan(self, doc: Any, ordered: Tuple[GraphicsElement, ...], paths: Dict[str, Any], assets: Dict[str, Dict[str, Any]],
                    fits: Dict[str, Dict[str, Any]], width: int, height: int, duration: float, fps: float, source_video: Dict[str, Any]) -> Dict[str, Any]:
        # the programme's tags are known only once it is written; ffmpeg-skill tags an SDR programme BT.709 and keeps an HDR source's
        source_video = source_video if source_video.get("bt2020_or_hdr") else {"color_space": "bt709"}
        settings = paths["settings"]
        return {"directory": str(paths["directory"]), "alpha_codec": settings["alpha_codec"], "alpha_mode": settings["alpha_mode"],
                "manifest": {"path": str(paths["manifest"])},
                "canvas": {"tool": "ffmpeg-skill/background", "retag": "ffmpeg-skill/color",
                           "parameters": self._canvas_parameters(width, height, duration, fps, retag_for(source_video))},
                "layers": [dict({"id": el.element_id, "type": el.type, "start": el.start, "end": el.end, "file": paths["files"][el.element_id].name,
                                 "tool": f"ffmpeg-skill/{ELEMENT_TYPES[el.type]['tool']}"}, **self._layer_box(el, assets, fits, width, height))
                           for el in ordered]}

    def _canvas(self, work_dir: Path, params: Dict[str, Any]) -> Dict[str, Any]:
        """Make (or reuse) the transparent canvas: ffmpeg-skill/background --transparent, then a probe that it has the programme's size and length."""
        identity = stable_hash({"skill_version": VERSION, "tool_versions": self.tool_versions, "generator": "background", "transparent": True, "parameters": params})
        path = work_dir / f"canvas-{identity[:16]}{ALPHA_CODECS[CANVAS_ALPHA_CODEC]}"
        manifest = path.with_name(path.stem + ".json")
        record: Dict[str, Any] = {"tool": "ffmpeg-skill/background", "operation_id": identity, "parameters": params}
        if self.reuse and self._reusable(identity, path, manifest):
            record.update(status="reused", output_hash=json.loads(manifest.read_text(encoding="utf-8"))["output_hash"], seconds=0.0, tool_commands_observed=[])
            return {"path": str(path), "record": record}
        for stale in (path, manifest):
            self._remove_partial(stale)
        info = self.skill.info() if self.skill is not None else None
        flags = ((info.features.get("generator_flags") or {}).get("background") or {}) if info is not None else {}
        argv = ["-o", str(path), "--transparent", "--duration", params["duration"], "--width", str(params["width"]), "--height", str(params["height"]),
                "--fps", params["fps"], "--alpha-codec", params["alpha_codec"]]
        if flags.get("overwrite"):
            argv.append("--overwrite")
        raw = path.with_name(path.stem + "-untagged" + path.suffix) if params.get("color_tags") else path
        retag_run = None
        try:
            run = self.skill.run_tool("background", argv[:1] + [str(raw)] + argv[2:], self.timeout)
            if raw != path:
                # the canvas gets the programme's colour tags (metadata only, a stream copy): graphics / overlay --keep-alpha keep the
                # tags of the picture they draw on, so every layer says the matrix it was drawn in, as the programme does
                retag_run = self.skill.run_tool("color", [str(raw), "--retag", params["color_tags"], "-o", str(path)], self.timeout)
                self._remove_partial(raw)
            meta = self._validate_layer_file(path, "the transparent canvas", params["width"], params["height"], float(params["duration"]))
        except MotionGraphicsError:
            self._remove_partial(raw)
            self._remove_partial(path)
            raise
        manifest.write_text(json.dumps({"schema": MANIFEST_SCHEMA, "operation_id": identity, "type": "transparent-canvas", "parameters": params, "input_hashes": [],
                                        "output_hash": meta["sha256"], "output_size": meta["size"], "duration": meta["duration"], "tool": "ffmpeg-skill/background",
                                        "tool_versions": self.tool_versions, "skill": SKILL_ID, "skill_version": VERSION}, indent=2, sort_keys=True), encoding="utf-8")
        record.update(status="rendered", output_hash=meta["sha256"], seconds=round(run.seconds + (retag_run.seconds if retag_run else 0.0), 3),
                      tool_commands_observed=run.commands + (retag_run.commands if retag_run else []),
                      verified=run.data.get("verified") if isinstance(run.data, dict) else None)
        if retag_run is not None:
            record["retag"] = {"tool": "ffmpeg-skill/color", "color_tags": params["color_tags"],
                               "verified": retag_run.data.get("verified") if isinstance(retag_run.data, dict) else None}
        return {"path": str(path), "record": record}

    def _validate_layer_file(self, path: Path, what: str, width: int, height: int, duration: float) -> Dict[str, Any]:
        if not path.is_file() or path.stat().st_size <= 0:
            raise MotionGraphicsError("OUTPUT_ERROR", f"{what}: ffmpeg-skill reported success but wrote no file", {"reason": "missing_output", "path": str(path)})
        meta = self.skill.probe(str(path), self.timeout)
        video = meta.get("video") or {}
        if (int(video.get("width") or 0), int(video.get("height") or 0)) != (width, height):
            raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: {video.get('width')}x{video.get('height')}, not the programme's {width}x{height}",
                                      {"reason": "resolution_mismatch", "path": str(path)})
        got = float(meta.get("duration") or 0.0)
        if abs(got - duration) > DURATION_TOLERANCE:
            raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: lasts {got:.3f}s, not the programme's {duration:.3f}s",
                                      {"reason": "duration_mismatch", "duration": got, "expected": duration, "path": str(path)})
        return {"sha256": sha256_file(str(path)), "size": path.stat().st_size, "duration": got}

    def _export_layers(self, doc: Any, ordered: Tuple[GraphicsElement, ...], paths: Dict[str, Any], assets: Dict[str, Dict[str, Any]],
                       fonts_: Dict[str, ResolvedFont], fits: Dict[str, Dict[str, Any]], width: int, height: int, duration: float, fps: float,
                       work_dir: Path, output_ext: str, crf: int, preset: str, platform: Optional[str], output_path: Path,
                       programme: Dict[str, Any]) -> Dict[str, Any]:
        """Every element drawn alone on the transparent canvas, with the call that burned it into the programme (ADR-30), then the manifest."""
        settings = paths["settings"]
        alpha = (settings["alpha_codec"], settings["alpha_mode"])
        # a manifest from an earlier run (replacing it was consented to) goes first: if a layer fails below, no manifest is left describing
        # files that have since been removed or rewritten
        self._remove_partial(paths["manifest"])
        canvas = self._canvas(work_dir, self._canvas_parameters(width, height, duration, fps, retag_for(programme.get("video") or {})))
        paths["directory"].mkdir(parents=True, exist_ok=True)
        layers: List[Dict[str, Any]] = []
        for el in ordered:
            target: Path = paths["files"][el.element_id]
            self._remove_partial(target)  # replacing it was consented to (document.output.overwrite), checked before anything rendered
            asset = assets.get(el.element_id)
            if el.type == "shape":
                clip = {"identity": self._shape_clip_identity(el, crf, preset), "parameters": self._shape_clip_parameters(el)}
                asset = {"path": self._shape_clip(el, clip, work_dir, output_ext, crf, preset)["path"]}
            tool, argv, cwd = self._argv(el, canvas["path"], str(target), asset, fonts_.get(el.element_id), crf, preset, None, platform,
                                         fit=fits.get(el.element_id), alpha=alpha)
            try:
                run = self.skill.run_tool(tool, argv, self.timeout, cwd)
                meta = self._validate_layer_file(target, f"elements[{el.element_id}] ({el.type}) layer", width, height, duration)
            except MotionGraphicsError:
                self._remove_partial(target)
                raise
            data = run.data if isinstance(run.data, dict) else {}
            layers.append(dict({"id": el.element_id, "type": el.type, "start": el.start, "end": el.end}, **self._layer_box(el, assets, fits, width, height),
                               file=target.name, path=str(target), sha256=meta["sha256"], size=meta["size"], duration=meta["duration"],
                               tool=f"ffmpeg-skill/{tool}", alpha_codec=data.get("alpha_codec"), alpha_mode=data.get("alpha_mode"),
                               verified=data.get("verified") if isinstance(data.get("verified"), bool) else None, alpha=alpha_step(data),
                               seconds=run.seconds, tool_commands_observed=run.commands))
        prog = {"file": output_path.name, "sha256": programme["sha256"], "width": width, "height": height, "duration": programme["duration"], "fps": fmt_number(fps)}
        doc_manifest = manifest_document(prog, settings, {k: v for k, v in canvas["record"]["parameters"].items()}, layers)
        manifest_path: Path = paths["manifest"]
        manifest_path.write_text(json.dumps(doc_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"directory": str(paths["directory"]), "manifest": {"path": str(manifest_path), "sha256": sha256_file(str(manifest_path))},
                "alpha_codec": settings["alpha_codec"], "alpha_mode": settings["alpha_mode"], "canvas": canvas["record"], "layers": layers,
                "verified": doc_manifest["verified"]}

    @staticmethod
    def _theme_record(theme: Dict[str, Any]) -> Dict[str, Any]:
        """The active theme as applied: no absolute path (a font file and the logo are named by their sha256 in provenance)."""
        font = theme.get("font") or None
        return {"label": theme["label"], "name": theme.get("name"), "file": theme["file"], "colors": dict(theme["colors"]),
                "font": ({"font_id": font["font_id"]} if font and "font_id" in font else ({"font_file": Path(font["font_file"]).name} if font else None)),
                "logo": ({k: v for k, v in theme["logo"].items() if k != "path"} if theme.get("logo") else None),
                "margin": theme.get("margin"), "base_style": theme.get("base_style"), "contrast": theme["contrast"]}

    @staticmethod
    def _region_warnings(reserved: Optional[Dict[str, Any]]) -> List[str]:
        if not reserved:
            return []
        return ([f"elements[{o['element_id']}] ({o['type']}) overlaps the {o['region']['kind'].replace('_', ' ')} region {o['region']['id']!r} "
                 f"from {o['time'][0]:g}s to {o['time'][1]:g}s (box {o['precision']})" for o in reserved["overlaps"]]
                + [f"elements[{eid}]: its size could not be measured, so it was not checked against the reserved regions" for eid in reserved.get("unmeasured", [])])

    # ---- text fit (ADR-27)
    def _fit(self, el: GraphicsElement, width: int, height: int) -> Dict[str, Any]:
        """Shrink and break a lower_third / title's texts to its fit box for this frame. Needs graphics --scale and --wrap (1.16's
        multi-line label layout) in the checkout, and --lang when the fit names one; refused before rendering otherwise."""
        assert el.fit is not None
        info = self.skill.info() if self.skill is not None else None
        if info is not None:
            needed = ["scale", "wrap"] + (["lang"] if el.fit.get("lang") else [])
            missing = [f for f in needed if not info.takes_flag("graphics", f)]
            if missing:
                raise MotionGraphicsError("TOOL_ERROR", f"elements[{el.element_id}]: a text fit needs ffmpeg-skill/graphics {['--' + f for f in missing]}, which the "
                                          f"ffmpeg-skill at {info.directory} (version {info.version}) does not have",
                                          {"reason": "ffmpeg_skill_lacks_flag", "element_id": el.element_id, "flags": missing, "ffmpeg_skill_version": info.version},
                                          retryable=False)
        keys = ("name", "title") if el.type == "lower_third" else ("title", "subtitle")
        texts = {k: el.parameters[k] for k in keys if el.parameters.get(k)}
        f = el.fit
        result = fit_text(el.type, texts, (width, height), f["width"], f["max_lines"], f["min_scale"], f.get("profile"))
        result.update(box_width=f["width"], profile=f.get("profile"), lang=f.get("lang"))
        return result

    # ---- shape: the generated clip (ADR-22)
    @staticmethod
    def _shape_clip_parameters(el: GraphicsElement) -> Dict[str, Any]:
        """What ffmpeg-skill/background is asked for: the rectangle's size and opaque colour (a name sent as its RRGGBB),
        lasting until the element's end so the clip covers the whole window from the video's start."""
        p = el.parameters
        return {"width": p["width"], "height": p["height"], "color": template_color(p["color"]), "duration": fmt_seconds(el.end)}

    def _shape_clip_identity(self, el: GraphicsElement, crf: int, preset: str) -> str:
        return stable_hash({"skill_version": VERSION, "tool_versions": self.tool_versions, "generator": "background",
                            "parameters": self._shape_clip_parameters(el), "crf": crf, "preset": preset})

    def _shape_clip(self, el: GraphicsElement, clip: Dict[str, Any], work_dir: Path, ext: str, crf: int, preset: str) -> Dict[str, Any]:
        """Generate (or reuse) the rectangle clip: ffmpeg-skill/background, then a probe that it is exactly the size asked for
        and lasts the element's window. Reused from the work dir when its manifest and sha256 match, like a non-final stage."""
        identity, cp = clip["identity"], clip["parameters"]
        path = work_dir / f"shape-{identity[:16]}{ext}"
        manifest = path.with_name(path.stem + ".json")
        record: Dict[str, Any] = {"tool": "ffmpeg-skill/background", "operation_id": identity, "parameters": cp}
        if self.reuse and self._reusable(identity, path, manifest):
            record.update(status="reused", output_hash=json.loads(manifest.read_text(encoding="utf-8"))["output_hash"], seconds=0.0, tool_commands_observed=[])
            return {"path": str(path), "record": record}
        for stale in (path, manifest):
            self._remove_partial(stale)
        info = self.skill.info() if self.skill is not None else None
        flags = ((info.features.get("generator_flags") or {}).get("background") or {}) if info is not None else {}
        argv = ["-o", str(path), "--duration", cp["duration"], "--width", str(cp["width"]), "--height", str(cp["height"]), "--color", "0x" + cp["color"]]
        if flags.get("encoder_quality"):
            argv += [flags["encoder_quality"], str(crf)]
        if flags.get("preset"):
            argv += ["--preset", preset]
        if flags.get("overwrite"):
            argv.append("--overwrite")
        what = f"elements[{el.element_id}] (shape clip)"
        try:
            run = self.skill.run_tool("background", argv, self.timeout)
            if not path.is_file() or path.stat().st_size <= 0:
                raise MotionGraphicsError("OUTPUT_ERROR", f"{what}: ffmpeg-skill/background reported success but wrote no clip", {"reason": "missing_output", "path": str(path)})
            meta = self.skill.probe(str(path), self.timeout)
            video = meta.get("video") or {}
            if (int(video.get("width") or 0), int(video.get("height") or 0)) != (cp["width"], cp["height"]):
                raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: the clip is {video.get('width')}x{video.get('height')}, not {cp['width']}x{cp['height']}",
                                           {"reason": "resolution_mismatch", "path": str(path)})
            duration = float(meta.get("duration") or 0.0)
            if duration + DURATION_TOLERANCE < el.end:
                raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: the clip lasts {duration:.3f}s, shorter than the element's end ({el.end}s)",
                                           {"reason": "duration_mismatch", "duration": duration, "expected": el.end, "path": str(path)})
        except MotionGraphicsError:
            self._remove_partial(path)
            raise
        out_hash = sha256_file(str(path))
        manifest.write_text(json.dumps({"schema": MANIFEST_SCHEMA, "operation_id": identity, "type": "shape-clip", "parameters": cp, "input_hashes": [],
                                        "output_hash": out_hash, "output_size": path.stat().st_size, "duration": duration, "tool": "ffmpeg-skill/background",
                                        "tool_versions": self.tool_versions, "skill": SKILL_ID, "skill_version": VERSION}, indent=2, sort_keys=True), encoding="utf-8")
        record.update(status="rendered", output_hash=out_hash, seconds=run.seconds, tool_commands_observed=run.commands)
        return {"path": str(path), "record": record}

    # ---- reuse
    def _reusable(self, identity: str, out_path: Path, manifest_path: Path) -> bool:
        if not (out_path.is_file() and manifest_path.is_file()):
            return False
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if m.get("schema") != MANIFEST_SCHEMA or m.get("operation_id") != identity:
            return False
        if out_path.stat().st_size != m.get("output_size") or sha256_file(str(out_path)) != m.get("output_hash"):
            return False
        return True

    def _probe_duration_cached(self, out_path: Path, manifest_path: Path) -> float:
        try:
            return float(json.loads(manifest_path.read_text(encoding="utf-8"))["duration"])
        except (OSError, ValueError, KeyError):
            return float((self.skill.probe(str(out_path), self.timeout) or {}).get("duration") or 0.0)

    @staticmethod
    def _remove_partial(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    # ---- output validation (STEP 8 / STEP 14 / STEP 19-20): existence, non-empty, probe-verified, sha256
    def _validate_stage_output(self, path: Path, el: GraphicsElement, width: int, height: int, prev_duration: float) -> Dict[str, Any]:
        what = f"elements[{el.element_id}] ({el.type})"
        if not path.is_file():
            raise MotionGraphicsError("OUTPUT_ERROR", f"{what}: tool reported success but wrote no file", {"reason": "missing_output", "path": str(path)})
        size = path.stat().st_size
        if size <= 0:
            raise MotionGraphicsError("OUTPUT_ERROR", f"{what}: output is empty", {"reason": "empty_output", "path": str(path)})
        try:
            meta = self.skill.probe(str(path), self.timeout)
        except MotionGraphicsError as e:
            raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: output is not readable media: {e.message}", {"reason": "corrupt_output", "path": str(path)})
        video = meta.get("video") or {}
        if not video:
            raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: output has no video stream", {"reason": "no_video_stream", "path": str(path)})
        if int(video["width"]) != width or int(video["height"]) != height:
            raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: output resolution {video['width']}x{video['height']} differs from source {width}x{height}",
                                       {"reason": "resolution_mismatch", "path": str(path)})
        duration = float(meta.get("duration") or 0.0)
        if abs(duration - prev_duration) > DURATION_TOLERANCE:
            raise MotionGraphicsError("VALIDATION_ERROR", f"{what}: output duration {duration:.3f}s differs from expected {prev_duration:.3f}s by more than {DURATION_TOLERANCE}s",
                                       {"reason": "duration_mismatch", "duration": duration, "expected": prev_duration, "path": str(path)})
        return {"sha256": sha256_file(str(path)), "size": size, "duration": duration}

    def _probe_and_check(self, path: Path, width: int, height: int, expected_duration: float) -> Dict[str, Any]:
        if not path.is_file():
            raise MotionGraphicsError("OUTPUT_ERROR", "final output was not written", {"reason": "missing_output", "path": str(path)})
        size = path.stat().st_size
        if size <= 0:
            raise MotionGraphicsError("OUTPUT_ERROR", "final output is empty", {"reason": "empty_output", "path": str(path)})
        meta = self.skill.probe(str(path), self.timeout)
        video = meta.get("video") or {}
        if not video:
            raise MotionGraphicsError("VALIDATION_ERROR", "final output has no video stream", {"reason": "no_video_stream", "path": str(path)})
        if int(video["width"]) != width or int(video["height"]) != height:
            raise MotionGraphicsError("VALIDATION_ERROR", f"final output resolution {video['width']}x{video['height']} differs from source {width}x{height}",
                                       {"reason": "resolution_mismatch", "path": str(path)})
        duration = float(meta.get("duration") or 0.0)
        if abs(duration - expected_duration) > DURATION_TOLERANCE:
            raise MotionGraphicsError("VALIDATION_ERROR", f"final output duration {duration:.3f}s differs from expected {expected_duration:.3f}s",
                                       {"reason": "duration_mismatch", "duration": duration, "expected": expected_duration, "path": str(path)})
        return {"sha256": sha256_file(str(path)), "size": size, "duration": duration, "video": video}
