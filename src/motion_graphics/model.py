"""Typed Graphics Document model and request validation.

Concepts (docs/architecture.md):
  GraphicsDocument  one video input, one output, a list of GraphicsElement (GraphicsDocument.ordered_elements()
                    is the timeline: the (start, id)-ordered sequence this skill actually renders in)
  GraphicsElement   id, type, timeline range [start, end), type-specific typed parameters, optional Animation
  Animation         kind ("fade" only, today), typed parameters (duration) -- no keyframe expressions, no eval

Validation here is structural and semantic but never touches the file system or a font/asset registry that needs
it: PathPolicy (security.py), the font registry (fonts.py) for font_id only, and the executor do that. Unknown
fields are rejected everywhere; fields that could carry a command, a shell, or a raw ffmpeg filter are rejected by
name, recursively, no matter how deep they appear in the document."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from . import REQUEST_SCHEMA_VERSION, SKILL_ID
from .errors import MotionGraphicsError

REQUEST_SCHEMA_ID = f"{SKILL_ID}/request@{REQUEST_SCHEMA_VERSION}"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COLOR_RE = re.compile(r"^([A-Za-z]{2,24}|[0-9A-Fa-f]{6})(@(0(\.[0-9]+)?|1(\.0+)?))?$")

# Fields that could carry a command, a shell invocation, an argv fragment, or a raw ffmpeg filter/expression.
# Rejected wherever they appear in the request, at any nesting depth (see _reject_forbidden).
FORBIDDEN_KEYS = frozenset({
    "command", "commands", "argv", "args", "cmd", "shell", "exec", "executable", "script",
    "filter", "filters", "filter_complex", "vf", "af", "env", "cwd", "eval", "expression",
})

POSITION_NAMES = ("top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right")

MAX_TEXT_LENGTH = 500
MAX_TITLE_LENGTH = 200
MAX_ELEMENTS = 64
MAX_DURATION = 24 * 3600.0

_STR, _INT, _NUM, _BOOL, _POS, _FONT, _COLOR, _ANIM, _PATH = (
    "string", "integer", "number", "boolean", "position", "font", "color", "animation", "path",
)

# ---- element type table: the only types this skill will ever render. Adding a type here without a working
# renderer in executor.py is exactly the mistake STEP 9 forbids -- contract.py and doctor.py both derive from
# this table, never hand-list types, so "supported" always matches what render actually does.
ELEMENT_TYPES: Dict[str, Dict[str, Any]] = {
    "title": {
        "tool": "graphics", "template": "title",
        "description": "Centered title card with an optional subtitle. Built-in 0.3s alpha fade in/out (ffmpeg-skill/graphics --template title); not configurable per element.",
        "animation": "builtin_fade",
        "parameters": {
            "title": {"type": _STR, "required": True, "max_length": MAX_TITLE_LENGTH},
            "subtitle": {"type": _STR, "required": False, "max_length": MAX_TITLE_LENGTH},
            "text_color": {"type": _COLOR, "required": False},
            "primary_color": {"type": _COLOR, "required": False},
        },
        "required_capabilities": ["ffmpeg-skill", "ffmpeg", "ffprobe", "filter:drawtext", "filter:drawbox", "encoder:libx264"],
    },
    "lower_third": {
        "tool": "graphics", "template": "lower-third",
        "description": "Name + title bar sliding in from the left. Built-in slide-in/out and fade (ffmpeg-skill/graphics --template lower-third); not configurable per element.",
        "animation": "builtin_slide_fade",
        "parameters": {
            "name": {"type": _STR, "required": True, "max_length": MAX_TITLE_LENGTH},
            "title": {"type": _STR, "required": False, "max_length": MAX_TITLE_LENGTH},
            "text_color": {"type": _COLOR, "required": False},
            "primary_color": {"type": _COLOR, "required": False},
        },
        "required_capabilities": ["ffmpeg-skill", "ffmpeg", "ffprobe", "filter:drawtext", "filter:overlay", "filter:color", "encoder:libx264"],
    },
    "text_overlay": {
        "tool": "overlay", "mode": "text",
        "description": "Free-form text drawn at a position, with an optional configurable fade in/out (ffmpeg-skill/overlay --text).",
        "animation": "configurable",
        "parameters": {
            "text": {"type": _STR, "required": True, "max_length": MAX_TEXT_LENGTH},
            "position": {"type": _POS, "required": False, "default": "bottom"},
            "margin": {"type": _INT, "required": False, "min": 0, "max": 2000, "default": 24},
            "font": {"type": _FONT, "required": False},
            "font_size": {"type": _INT, "required": False, "min": 8, "max": 300, "default": 42},
            "font_color": {"type": _COLOR, "required": False, "default": "white"},
            "border_width": {"type": _INT, "required": False, "min": 0, "max": 20, "default": 2},
            "border_color": {"type": _COLOR, "required": False, "default": "black"},
            "box": {"type": _BOOL, "required": False, "default": False},
            "box_color": {"type": _COLOR, "required": False, "default": "black@0.5"},
            "opacity": {"type": _NUM, "required": False, "min": 0.0, "max": 1.0, "default": 1.0},
        },
        "required_capabilities": ["ffmpeg-skill", "ffmpeg", "ffprobe", "filter:drawtext", "encoder:libx264"],
    },
    "image_overlay": {
        "tool": "overlay", "mode": "image",
        "description": "Composite a PNG/JPG image or logo at a position, with optional static scale and a configurable fade in/out (ffmpeg-skill/overlay --image).",
        "animation": "configurable",
        "parameters": {
            "image_path": {"type": _PATH, "required": True},
            "position": {"type": _POS, "required": False, "default": "top-right"},
            "margin": {"type": _INT, "required": False, "min": 0, "max": 2000, "default": 24},
            "scale_width": {"type": _INT, "required": False, "min": 1, "max": 8192},
            "scale_percent": {"type": _NUM, "required": False, "min": 0.1, "max": 100.0},
            "opacity": {"type": _NUM, "required": False, "min": 0.0, "max": 1.0, "default": 1.0},
        },
        "required_capabilities": ["ffmpeg-skill", "ffmpeg", "ffprobe", "filter:overlay", "filter:scale", "filter:colorchannelmixer", "encoder:libx264"],
    },
}
# Requested in STEP 1 / STEP 24 but not implemented in this contract: no delegate tool renders them without this
# skill building a raw filter string itself, which STEP 7 / STEP 10 forbid. Listed so contract/doctor never claim
# support they cannot back with a renderer (STEP 9).
UNSUPPORTED_ELEMENT_TYPES: Dict[str, str] = {
    "shape": "no typed delegate tool draws an arbitrary shape (position/size/color) without a raw ffmpeg filter string; needs a typed shape tool in ffmpeg-skill first",
    "chapter": "ffmpeg-skill/graphics supports this template, but it is outside this contract's first minimum (title, lower_third, text_overlay, image_overlay only)",
    "bug": "same as chapter: template exists upstream, not exposed by this contract yet",
    "progress": "same as chapter: template exists upstream, not exposed by this contract yet",
    "countdown": "same as chapter: template exists upstream, not exposed by this contract yet",
}

ANIMATION_KINDS: Dict[str, Dict[str, Any]] = {
    "fade": {
        # ffmpeg-skill/overlay's --fade always fades in at `start` AND out at `end` when both are given (its
        # alpha_expr multiplies both terms whenever `end` is not None); it has no flag to select only one
        # direction over a bounded window. Rather than approximate an "in-only" or "out-only" fade by omitting
        # --end (which would also cancel this element's own timeline end), this skill exposes exactly the
        # semantics the engine has: always both directions, one shared duration.
        "description": "Linear alpha fade in at start and out at end, over the same duration (ffmpeg-skill/overlay --fade). Only for text_overlay and image_overlay.",
        "parameters": {
            "duration": {"type": _NUM, "required": True, "min": 0.01, "max": 30.0},
        },
    },
}
# Requested in STEP 1 but not backed by a typed, parameterised delegate today: overlay.py takes a static position
# and a static --scale / --scale-percent, and graphics.py's lower-third slide is a fixed, non-configurable
# built-in of the template (see ELEMENT_TYPES[...]["animation"] == "builtin_slide_fade"). Exposing a generic
# "slide"/"move"/"scale" Animation would mean this skill inventing its own per-frame filter expression, which is
# exactly the raw-filter authority STEP 5 / STEP 10 withhold from it.
UNSUPPORTED_ANIMATIONS: Dict[str, str] = {
    "slide": "position animation is not exposed as a typed, per-element parameter by ffmpeg-skill/overlay; lower_third has a fixed, non-configurable built-in slide instead",
    "move": "arbitrary position keyframes would require a per-frame expression, which this skill does not construct (no raw filters, STEP 10)",
    "scale": "scale animation over time is not exposed as a typed parameter by ffmpeg-skill/overlay (static --scale / --scale-percent only)",
    "opacity_keyframes": "arbitrary opacity keyframe curves are not supported; use a fade (2-point in/out/in_out) instead",
}


@dataclass(frozen=True)
class Animation:
    kind: str
    parameters: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "parameters": dict(self.parameters)}


@dataclass(frozen=True)
class GraphicsElement:
    element_id: str
    type: str
    start: float
    end: float
    parameters: Dict[str, Any]
    animation: Optional[Animation] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"id": self.element_id, "type": self.type, "start": self.start, "end": self.end, "parameters": dict(self.parameters)}
        if self.animation is not None:
            d["animation"] = self.animation.to_dict()
        return d


@dataclass(frozen=True)
class GraphicsDocument:
    video_path: str
    output_path: str
    overwrite: bool
    elements: Tuple[GraphicsElement, ...]
    options: Dict[str, Any] = field(default_factory=dict)

    def ordered_elements(self) -> Tuple[GraphicsElement, ...]:
        """Deterministic rendering order: by (start time, element id). This is an execution-order decision, not a
        correction of the caller's timeline -- the validated start/end of every element are rendered unchanged;
        only the sequence in which independent full-frame overlays are composited is canonicalised so the same
        document always renders the same way regardless of the order elements were listed in (docs/decisions.md)."""
        return tuple(sorted(self.elements, key=lambda e: (e.start, e.element_id)))


# ---- validation helpers
def _reject_forbidden(obj: Any, where: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise MotionGraphicsError("INVALID_REQUEST", f"{where}: object keys must be strings")
            if k.lower() in FORBIDDEN_KEYS:
                raise MotionGraphicsError("INVALID_REQUEST", f"{where}: field {k!r} is not accepted (this skill never takes commands, argv, filters or executables)",
                                           {"field": k, "reason": "forbidden_field"})
            _reject_forbidden(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject_forbidden(v, f"{where}[{i}]")


def _obj(value: Any, where: str, allowed: Tuple[str, ...], required: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    unknown = sorted(k for k in value if k not in allowed)
    if unknown:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: unknown field(s) {unknown}", {"field": where, "unknown": unknown, "allowed": list(allowed)})
    missing = [k for k in required if k not in value]
    if missing:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: missing required field(s) {missing}", {"field": where, "missing": missing})
    return value


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must match {ID_RE.pattern}", {"field": where})
    return value


def _finite_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be a number", {"field": where})
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where} must be finite (NaN/Infinity are not allowed)", {"field": where, "value": str(value)})
    return v


def _string(value: Any, where: str, max_length: Optional[int] = None, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be a string", {"field": where})
    if "\x00" in value:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} may not contain a NUL byte", {"field": where})
    if not allow_empty and not value.strip():
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must not be empty", {"field": where})
    if max_length is not None and len(value) > max_length:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} is longer than {max_length} characters", {"field": where, "length": len(value), "max_length": max_length})
    return value


def _color(value: Any, where: str) -> str:
    text = _string(value, where, max_length=40)
    if not COLOR_RE.match(text):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be a named color or RRGGBB hex, optionally with @alpha: {value!r}", {"field": where})
    return text


def _position(value: Any, where: str) -> str:
    if isinstance(value, str):
        if value not in POSITION_NAMES:
            raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be one of {POSITION_NAMES} or an {{x,y}} object", {"field": where, "value": value})
        return value
    if isinstance(value, dict):
        obj = _obj(value, where, ("x", "y"), ("x", "y"))
        x, y = obj["x"], obj["y"]
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise MotionGraphicsError("INVALID_REQUEST", f"{where}.x and {where}.y must be integers", {"field": where})
        if not (-100000 <= x <= 100000) or not (-100000 <= y <= 100000):
            raise MotionGraphicsError("INVALID_REQUEST", f"{where}.x and {where}.y must be within +/-100000", {"field": where})
        return f"{x},{y}"
    raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be a string or an {{x,y}} object", {"field": where})


def _font(value: Any, where: str) -> Dict[str, Any]:
    obj = _obj(value, where, ("font_id", "font_file"), ())
    if "font_id" in obj and "font_file" in obj:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: give font_id or font_file, not both", {"field": where})
    if "font_id" in obj:
        _string(obj["font_id"], f"{where}.font_id", max_length=64)
    elif "font_file" in obj:
        _string(obj["font_file"], f"{where}.font_file", max_length=4096)
    else:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: give font_id or font_file", {"field": where})
    return obj


def _animation(value: Any, element_type: str, where: str) -> Animation:
    spec = ELEMENT_TYPES[element_type]
    if spec.get("animation") != "configurable":
        raise MotionGraphicsError("UNSUPPORTED_OPERATION", f"{where}: element type {element_type!r} does not accept a configurable animation "
                                   f"({spec.get('animation')} is built into the template and is not a request field)",
                                   {"field": where, "element_type": element_type})
    obj = _obj(value, where, ("kind", "parameters"), ("kind", "parameters"))
    kind = obj["kind"]
    if not isinstance(kind, str) or kind not in ANIMATION_KINDS:
        if isinstance(kind, str) and kind in UNSUPPORTED_ANIMATIONS:
            raise MotionGraphicsError("UNSUPPORTED_OPERATION", f"{where}.kind {kind!r} is not implemented: {UNSUPPORTED_ANIMATIONS[kind]}",
                                       {"field": f"{where}.kind", "kind": kind})
        raise MotionGraphicsError("UNSUPPORTED_OPERATION", f"{where}.kind must be one of {sorted(ANIMATION_KINDS)}: {kind!r}", {"field": f"{where}.kind"})
    aspec = ANIMATION_KINDS[kind]["parameters"]
    params_raw = _obj(obj["parameters"], f"{where}.parameters", tuple(aspec), tuple(k for k, v in aspec.items() if v.get("required")))
    params: Dict[str, Any] = {}
    for name, pspec in aspec.items():
        if name not in params_raw:
            if "default" in pspec:
                params[name] = pspec["default"]
            continue
        raw = params_raw[name]
        if pspec["type"] == _STR:
            text = _string(raw, f"{where}.parameters.{name}", max_length=32)
            if "enum" in pspec and text not in pspec["enum"]:
                raise MotionGraphicsError("INVALID_REQUEST", f"{where}.parameters.{name} must be one of {pspec['enum']}: {text!r}", {"field": f"{where}.parameters.{name}"})
            params[name] = text
        elif pspec["type"] == _NUM:
            num = _finite_number(raw, f"{where}.parameters.{name}")
            if ("min" in pspec and num < pspec["min"]) or ("max" in pspec and num > pspec["max"]):
                raise MotionGraphicsError("INVALID_REQUEST", f"{where}.parameters.{name} must be within [{pspec.get('min')}, {pspec.get('max')}]: {num}", {"field": f"{where}.parameters.{name}"})
            params[name] = num
        else:  # pragma: no cover - no other animation parameter types exist today
            raise MotionGraphicsError("INTERNAL_ERROR", f"unhandled animation parameter type {pspec['type']!r}")
    if kind == "fade" and "duration" in params and (2 * params["duration"]) > MAX_DURATION:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}.parameters.duration is unreasonably large: {params['duration']}", {"field": f"{where}.parameters.duration"})
    return Animation(kind, params)


def _element(value: Any, index: int) -> GraphicsElement:
    where = f"elements[{index}]"
    top_level = ("id", "type", "start", "end", "parameters", "animation")
    obj = _obj(value, where, top_level, ("id", "type", "start", "end", "parameters"))
    element_id = _id(obj["id"], f"{where}.id")
    etype = obj["type"]
    if not isinstance(etype, str) or etype not in ELEMENT_TYPES:
        if isinstance(etype, str) and etype in UNSUPPORTED_ELEMENT_TYPES:
            raise MotionGraphicsError("UNSUPPORTED_OPERATION", f"{where}.type {etype!r} is not implemented: {UNSUPPORTED_ELEMENT_TYPES[etype]}",
                                       {"field": f"{where}.type", "type": etype})
        raise MotionGraphicsError("UNSUPPORTED_OPERATION", f"{where}.type must be one of {sorted(ELEMENT_TYPES)}: {etype!r}", {"field": f"{where}.type"})
    start = _finite_number(obj["start"], f"{where}.start")
    end = _finite_number(obj["end"], f"{where}.end")
    if start < 0:
        raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where}.start must be >= 0: {start}", {"field": f"{where}.start"})
    if end <= start:
        raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where}.end must be greater than {where}.start: end={end} start={start}", {"field": f"{where}.end"})
    if end - start > MAX_DURATION:
        raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where}: duration exceeds {MAX_DURATION}s", {"field": f"{where}.end"})

    tspec = ELEMENT_TYPES[etype]
    pspec = tspec["parameters"]
    praw = _obj(obj["parameters"], f"{where}.parameters", tuple(pspec), tuple(k for k, v in pspec.items() if v.get("required")))
    parameters: Dict[str, Any] = {}
    for name, spec in pspec.items():
        if name not in praw:
            if "default" in spec:
                parameters[name] = spec["default"]
            continue
        raw = praw[name]
        where_p = f"{where}.parameters.{name}"
        if spec["type"] == _STR:
            parameters[name] = _string(raw, where_p, max_length=spec.get("max_length"))
        elif spec["type"] == _COLOR:
            parameters[name] = _color(raw, where_p)
        elif spec["type"] == _POS:
            parameters[name] = _position(raw, where_p)
        elif spec["type"] == _FONT:
            parameters[name] = _font(raw, where_p)
        elif spec["type"] == _PATH:
            parameters[name] = _string(raw, where_p, max_length=4096)
        elif spec["type"] == _INT:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise MotionGraphicsError("INVALID_REQUEST", f"{where_p} must be an integer", {"field": where_p})
            if ("min" in spec and raw < spec["min"]) or ("max" in spec and raw > spec["max"]):
                raise MotionGraphicsError("INVALID_REQUEST", f"{where_p} must be within [{spec.get('min')}, {spec.get('max')}]: {raw}", {"field": where_p})
            parameters[name] = raw
        elif spec["type"] == _NUM:
            num = _finite_number(raw, where_p)
            if ("min" in spec and num < spec["min"]) or ("max" in spec and num > spec["max"]):
                raise MotionGraphicsError("INVALID_REQUEST", f"{where_p} must be within [{spec.get('min')}, {spec.get('max')}]: {num}", {"field": where_p})
            parameters[name] = num
        elif spec["type"] == _BOOL:
            if not isinstance(raw, bool):
                raise MotionGraphicsError("INVALID_REQUEST", f"{where_p} must be a boolean", {"field": where_p})
            parameters[name] = raw
        else:  # pragma: no cover
            raise MotionGraphicsError("INTERNAL_ERROR", f"unhandled parameter type {spec['type']!r}")

    animation: Optional[Animation] = None
    if "animation" in obj:
        animation = _animation(obj["animation"], etype, f"{where}.animation")
        if animation.kind == "fade" and animation.parameters["duration"] > (end - start) / 2:
            raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where}.animation: fade duration {animation.parameters['duration']}s does not fit within [{start}, {end}]",
                                       {"field": f"{where}.animation.parameters.duration"})
    return GraphicsElement(element_id, etype, start, end, parameters, animation)


def parse_request(document: Any) -> GraphicsDocument:
    _reject_forbidden(document, "document")
    top = _obj(document, "document", ("schema", "video", "output", "elements", "options"), ("video", "output", "elements"))
    if "schema" in top and top["schema"] != REQUEST_SCHEMA_ID:
        raise MotionGraphicsError("INVALID_REQUEST", f"document.schema must be {REQUEST_SCHEMA_ID!r}: {top['schema']!r}", {"field": "schema"})

    video = _obj(top["video"], "document.video", ("path",), ("path",))
    video_path = _string(video["path"], "document.video.path", max_length=4096)

    output = _obj(top["output"], "document.output", ("path", "overwrite"), ("path",))
    output_path = _string(output["path"], "document.output.path", max_length=4096)
    overwrite = output.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise MotionGraphicsError("INVALID_REQUEST", "document.output.overwrite must be a boolean", {"field": "document.output.overwrite"})

    raw_elements = top["elements"]
    if not isinstance(raw_elements, list) or not raw_elements:
        raise MotionGraphicsError("INVALID_REQUEST", "document.elements must be a non-empty array", {"field": "elements"})
    if len(raw_elements) > MAX_ELEMENTS:
        raise MotionGraphicsError("INVALID_REQUEST", f"document.elements has more than {MAX_ELEMENTS} entries", {"field": "elements", "count": len(raw_elements)})
    elements = [_element(e, i) for i, e in enumerate(raw_elements)]
    seen: Dict[str, int] = {}
    for i, el in enumerate(elements):
        if el.element_id in seen:
            raise MotionGraphicsError("DEPENDENCY_ERROR", f"duplicate element id {el.element_id!r} (elements[{seen[el.element_id]}] and elements[{i}])",
                                       {"field": "elements", "id": el.element_id})
        seen[el.element_id] = i

    options_raw = top.get("options", {})
    options = _obj(options_raw, "document.options", ("reuse_intermediates", "crf", "preset"), ())
    reuse = options.get("reuse_intermediates", True)
    if not isinstance(reuse, bool):
        raise MotionGraphicsError("INVALID_REQUEST", "document.options.reuse_intermediates must be a boolean", {"field": "document.options.reuse_intermediates"})
    crf = options.get("crf", 18)
    if isinstance(crf, bool) or not isinstance(crf, int) or not (0 <= crf <= 51):
        raise MotionGraphicsError("INVALID_REQUEST", "document.options.crf must be an integer within [0, 51]", {"field": "document.options.crf"})
    preset = options.get("preset", "medium")
    presets = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
    if preset not in presets:
        raise MotionGraphicsError("INVALID_REQUEST", f"document.options.preset must be one of {presets}: {preset!r}", {"field": "document.options.preset"})

    return GraphicsDocument(video_path, output_path, overwrite, tuple(elements), {"reuse_intermediates": reuse, "crf": crf, "preset": preset})
