"""Request -> validated Graphics Document -> render plan -> ffmpeg-skill execution -> validated output -> response.

Every element is rendered as one full-frame ffmpeg-skill invocation, applied in timeline order (docs/decisions.md)
to the output of the previous element (or to the source video for the first element). All but the last element
write into an identity-named cache file under `<workspace>/.motion-graphics/<document_id>/` and are reused when an
existing file's manifest and sha256 match (STEP 18); the last element always (re)writes the requested output path,
which is fully re-validated afterwards regardless of reuse (STEP 8 / STEP 19). No step ever passes a raw ffmpeg
filter, command, or expression across the ffmpeg-skill boundary: every argv value is a validated number, a closed
vocabulary string (position/color/preset), or a path resolved by PathPolicy (security.py)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FfmpegSkill, fmt_number, fmt_seconds
from .canonical import sha256_file, stable_hash
from .errors import MotionGraphicsError
from .fonts import ResolvedFont, resolve_font
from .model import ELEMENT_TYPES, GraphicsElement, parse_request
from .security import PathPolicy

RESPONSE_SCHEMA_ID = f"{SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION}"
MANIFEST_SCHEMA = f"{SKILL_ID}/manifest@1"
WORK_DIR_NAME = ".motion-graphics"
DURATION_TOLERANCE = 0.25  # seconds; each stage re-encodes with a forced constant frame rate (ffmpeg-skill cfr_args)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


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

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "element_id": self.element_id, "type": self.type, "tool": f"ffmpeg-skill/{self.tool}",
                "status": self.status, "operation_id": self.identity, "parameters": self.parameters,
                "input_hashes": self.input_hashes, "output_hash": self.output_hash, "seconds": self.seconds,
                "tool_commands_observed": self.tool_commands_observed}


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
        video_path = self.policy.resolve_input(doc.video_path, "video")
        output_path = self.policy.resolve_write_path(doc.output_path, "output")
        if output_path == video_path:
            raise MotionGraphicsError("OUTPUT_ERROR", "document.output.path may not be the same file as document.video.path", {"reason": "output_is_input"})
        if output_path.exists() and not doc.overwrite:
            raise MotionGraphicsError("OUTPUT_ERROR", f"output already exists (set document.output.overwrite to replace it): {output_path}", {"reason": "exists", "path": str(output_path)})

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
        for el in ordered:
            if el.type == "image_overlay":
                image_path = self.policy.resolve_input(el.parameters["image_path"], f"elements[{el.element_id}].parameters.image_path")
                if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise MotionGraphicsError("UNSUPPORTED_FORMAT", f"elements[{el.element_id}].parameters.image_path must be one of {IMAGE_EXTENSIONS}: {image_path.suffix}",
                                               {"element_id": el.element_id, "extension": image_path.suffix})
                resolved_assets[el.element_id] = {"path": str(image_path), "sha256": sha256_file(str(image_path)), "size": image_path.stat().st_size}
            if el.type == "text_overlay" and el.parameters.get("font"):
                resolved_fonts[el.element_id] = resolve_font(el.parameters["font"], self.policy)
            elif el.type == "text_overlay":
                resolved_fonts[el.element_id] = resolve_font(None, self.policy)

        document_id = stable_hash({"video_sha256": video_hash, "elements": [e.to_dict() for e in ordered]})[:16]

        if self.dry_run:
            timeline = [self._plan_entry(i, el, resolved_assets, resolved_fonts) for i, el in enumerate(ordered)]
            return {"schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": True, "status": "ok", "dry_run": True,
                    "plan": {"document_id": document_id, "video": {"path": str(video_path), "sha256": video_hash, "duration": duration, "width": width, "height": height},
                             "output": {"path": str(output_path)}, "timeline": timeline},
                    "warnings": []}

        # A single-element document never has a non-final stage, so it never needs (or creates) the cache
        # directory at all -- it renders straight into the requested output (ADR-5, docs/decisions.md).
        work_dir: Optional[Path] = None
        if len(ordered) > 1:
            work_dir = self.policy.resolve_work_dir(os.path.join(WORK_DIR_NAME, document_id))
            work_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)  # ffmpeg-skill does not create the output directory itself
        output_ext = output_path.suffix or ".mp4"

        stage_input = str(video_path)
        prev_identity = video_hash
        prev_duration = duration
        results: List[StageResult] = []
        any_reused = False
        crf, preset = doc.options["crf"], doc.options["preset"]

        for i, el in enumerate(ordered):
            is_last = i == len(ordered) - 1
            identity_input = self._identity_parameters(el, resolved_assets.get(el.element_id), resolved_fonts.get(el.element_id))
            identity = stable_hash({"skill_version": VERSION, "tool_versions": self.tool_versions, "index": i, "previous": prev_identity,
                                     "type": el.type, "start": el.start, "end": el.end, "animation": el.animation.to_dict() if el.animation else None,
                                     "parameters": identity_input, "crf": crf, "preset": preset})
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
            tool, argv, tool_cwd = self._argv(el, stage_input, str(target), resolved_assets.get(el.element_id), resolved_fonts.get(el.element_id), crf, preset)
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
            results.append(StageResult(i, el.element_id, el.type, tool, "rendered", identity, identity_input, [prev_identity], out_hash, str(target), run.seconds, run.commands))
            stage_input, prev_identity, prev_duration = str(target), identity, artifact_meta["duration"]

        final_meta = self._probe_and_check(output_path, width, height, prev_duration)
        return {
            "schema": RESPONSE_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "ok": True, "status": "ok", "dry_run": False,
            "output": {"path": str(output_path), "sha256": final_meta["sha256"], "size": final_meta["size"], "duration": final_meta["duration"],
                       "width": width, "height": height},
            "timeline": [{"id": e.element_id, "type": e.type, "start": e.start, "end": e.end} for e in ordered],
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
            "warnings": [],
        }

    # ---- planning (no filesystem writes beyond none; probe is read-only)
    def _plan_entry(self, index: int, el: GraphicsElement, assets: Dict[str, Dict[str, Any]], fonts_: Dict[str, ResolvedFont]) -> Dict[str, Any]:
        d = el.to_dict()
        d["index"] = index
        d["tool"] = f"ffmpeg-skill/{ELEMENT_TYPES[el.type]['tool']}"
        if el.element_id in assets:
            d["asset"] = {"sha256": assets[el.element_id]["sha256"], "size": assets[el.element_id]["size"]}
        if el.element_id in fonts_:
            d["font"] = fonts_[el.element_id].to_provenance()
        return d

    # ---- identity (never includes an absolute path or any machine-specific value, STEP 13)
    @staticmethod
    def _identity_parameters(el: GraphicsElement, asset: Optional[Dict[str, Any]], font: Optional[ResolvedFont]) -> Dict[str, Any]:
        params = dict(el.parameters)
        if "image_path" in params and asset is not None:
            params["image_path"] = {"sha256": asset["sha256"], "size": asset["size"]}
        if font is not None:
            params["font"] = font.to_provenance()
        return params

    # ---- argv construction: every value is a validated number, a closed-vocabulary string, or a resolved path.
    # Returns (tool, argv, cwd): cwd is None except for a custom font_file (see the comment below).
    def _argv(self, el: GraphicsElement, stage_input: str, stage_output: str, asset: Optional[Dict[str, Any]],
              font: Optional[ResolvedFont], crf: int, preset: str) -> Tuple[str, List[str], Optional[str]]:
        p = el.parameters
        spec = ELEMENT_TYPES[el.type]
        if spec["tool"] == "graphics":
            argv = [stage_input, "-o", stage_output, "--template", spec["template"],
                    "--start", fmt_seconds(el.start), "--end", fmt_seconds(el.end), "--crf", str(crf), "--preset", preset]
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
            # else: progress -- no --title/--position/--from at all (see model.ELEMENT_TYPES); only the trailing
            # primary_color passthrough below applies to it
            if p.get("text_color"):
                argv += ["--text-color", p["text_color"]]
            if p.get("primary_color"):
                argv += ["--primary", p["primary_color"]]
            return "graphics", argv, None

        argv = [stage_input, "-o", stage_output, "--position", p["position"], "--margin", str(p["margin"]),
                "--start", fmt_seconds(el.start), "--end", fmt_seconds(el.end), "--opacity", fmt_number(p["opacity"]),
                "--crf", str(crf), "--preset", preset]
        if el.animation is not None and el.animation.kind == "fade":
            argv += ["--fade", fmt_number(el.animation.parameters["duration"])]
        cwd: Optional[str] = None
        if el.type == "text_overlay":
            argv += ["--text", p["text"], "--font-size", str(p["font_size"]), "--font-color", p["font_color"],
                     "--border", str(p["border_width"]), "--border-color", p["border_color"]]
            if p.get("box"):
                argv += ["--box", "--box-color", p["box_color"]]
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
        else:  # image_overlay
            assert asset is not None
            argv += ["--image", asset["path"]]
            if p.get("scale_width"):
                argv += ["--scale", str(p["scale_width"])]
            if p.get("scale_percent") is not None:
                argv += ["--scale-percent", fmt_number(p["scale_percent"])]
        return "overlay", argv, cwd

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
        return {"sha256": sha256_file(str(path)), "size": size, "duration": duration}
