"""Named style presets: typed bundles of parameters the element types already have (docs/decisions.md ADR-24).

A preset is not a design decision this skill makes. The caller names one (`element.style`, or `document.options.style`
for every element), and this module fills in the element's *optional* parameters the request left out, from a fixed
table. Nothing new reaches ffmpeg-skill: a preset only supplies values for parameters the element type already takes
(colours, font, box, margin, border) and, for the two element types with a configurable fade, a `fade` animation.

Each preset has four colour roles -- primary, secondary, background, text -- and a few typed settings. Each element
type maps some of its parameters to a role or setting (STYLE_TARGETS); a type with no styleable parameter (meme: its
colours, position and size are fixed by the template) maps none, and a preset leaves it unchanged.

`document.options.brand_kit` replaces role colours: a role the kit gives wins over the preset's. An explicit parameter
in the request wins over both. So the precedence is: explicit parameter > brand kit role > preset role.

Role colours are opaque RRGGBB (a name from colors.NAMED_COLORS is accepted in a brand kit and converted), because the
graphics templates draw them opaque (ADR-20). The one translucent value a preset makes, a text box, is the background
role plus the preset's own `box_opacity`, sent to ffmpeg-skill/overlay as `@alpha`, which it accepts."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .colors import NAMED_COLORS, is_hex, split_alpha
from .errors import MotionGraphicsError

ROLES = ("primary", "secondary", "background", "text")

# name -> preset. Colours are bare RRGGBB; font_id is a key of fonts.FONT_REGISTRY; fade is seconds or None (no fade).
STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "swiss": {
        "description": "Swiss / international style: black text on a solid white plate, one red accent, no fade.",
        "roles": {"primary": "E30613", "secondary": "000000", "background": "FFFFFF", "text": "111111"},
        "font_id": "system:dejavu-sans", "box": True, "box_opacity": 0.95, "border_width": 0, "margin": 48, "fade": None,
    },
    "minimal": {
        "description": "Minimal: white text with no plate and no outline, a wide margin, a slow fade.",
        "roles": {"primary": "FFFFFF", "secondary": "BBBBBB", "background": "000000", "text": "FFFFFF"},
        "font_id": "system:dejavu-sans", "box": False, "box_opacity": 0.0, "border_width": 0, "margin": 64, "fade": 0.4,
    },
    "neon": {
        "description": "Neon: near-white text on a dark violet plate, cyan primary, magenta secondary, a quick fade.",
        "roles": {"primary": "00E5FF", "secondary": "FF2BD6", "background": "0B0221", "text": "F5F5F5"},
        "font_id": "system:dejavu-sans", "box": True, "box_opacity": 0.6, "border_width": 2, "margin": 40, "fade": 0.25,
    },
    "corporate": {
        "description": "Corporate: white text on a navy plate, blue primary, a short fade.",
        "roles": {"primary": "0B5FFF", "secondary": "00A3E0", "background": "0F1C2E", "text": "FFFFFF"},
        "font_id": "system:dejavu-sans", "box": True, "box_opacity": 0.8, "border_width": 0, "margin": 48, "fade": 0.3,
    },
    "wafu": {
        "description": "Wafu (Japanese-inspired): off-white text on an ink-black plate, crimson primary, gold secondary, a serif face, "
                       "a slow fade. A colour and type bundle only: it changes no line breaking or other regional behaviour.",
        "roles": {"primary": "B7282E", "secondary": "C9A063", "background": "1E1E1E", "text": "F7F3E8"},
        "font_id": "system:dejavu-serif", "box": True, "box_opacity": 0.7, "border_width": 0, "margin": 56, "fade": 0.5,
    },
    "news": {
        "description": "News: white text on a dark plate, red primary, yellow secondary, a tight margin, no fade.",
        "roles": {"primary": "C8102E", "secondary": "FFD200", "background": "101820", "text": "FFFFFF"},
        "font_id": "system:dejavu-sans", "box": True, "box_opacity": 0.85, "border_width": 0, "margin": 32, "fade": None,
    },
}

# element type -> {parameter: source}. A source is "role:<role>", "box_color" (background role + box_opacity), or the name of
# a preset setting (font_id, box, border_width, margin). "animation" is the fade (text_overlay / image_overlay only).
STYLE_TARGETS: Dict[str, Dict[str, str]] = {
    "title": {"text_color": "role:text", "primary_color": "role:primary"},
    "lower_third": {"text_color": "role:text", "primary_color": "role:primary"},
    "hook": {"text_color": "role:text", "primary_color": "role:primary"},
    "bug": {"text_color": "role:text"},
    "chapter": {"primary_color": "role:primary"},
    "progress": {"primary_color": "role:primary"},
    "countdown": {"primary_color": "role:primary"},
    "sticker": {"primary_color": "role:primary"},
    "meme": {},
    "text_overlay": {"font_color": "role:text", "border_color": "role:background", "box": "box", "box_color": "box_color",
                     "font": "font_id", "border_width": "border_width", "margin": "margin", "animation": "fade"},
    "image_overlay": {"margin": "margin", "animation": "fade"},
    "video_overlay": {"margin": "margin"},
    "shape": {"margin": "margin"},
}

BRAND_KIT_FIELDS = ROLES


def _kit_color(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 40:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be RRGGBB hex or a colour name", {"field": where})
    base, alpha = split_alpha(value)
    if alpha is not None:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: a brand kit colour is opaque (it is also drawn by the graphics templates, which "
                                  f"take no @alpha): {value!r}", {"field": where, "reason": "color_alpha_not_supported"})
    if is_hex(base):
        return base.upper()
    if base.lower() in NAMED_COLORS:
        return NAMED_COLORS[base.lower()]
    raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be RRGGBB hex or a colour name from the contract's colors.named table: {value!r}",
                              {"field": where, "reason": "unknown_color_name"})


def parse_brand_kit(value: Any, where: str = "document.options.brand_kit") -> Dict[str, str]:
    """{role: RRGGBB} for the roles the kit gives (at least one)."""
    if not isinstance(value, dict):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    unknown = sorted(k for k in value if k not in BRAND_KIT_FIELDS)
    if unknown:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: unknown field(s) {unknown}", {"field": where, "unknown": unknown, "allowed": list(BRAND_KIT_FIELDS)})
    if not value:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must give at least one of {list(BRAND_KIT_FIELDS)}", {"field": where})
    return {role: _kit_color(value[role], f"{where}.{role}") for role in BRAND_KIT_FIELDS if role in value}


def style_name(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in STYLE_PRESETS:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be one of {sorted(STYLE_PRESETS)}: {value!r}", {"field": where, "allowed": sorted(STYLE_PRESETS)})
    return value


def resolve_roles(preset: Optional[str], kit: Optional[Dict[str, str]]) -> Dict[str, Tuple[str, str]]:
    """{role: (RRGGBB, "brand_kit"|"preset")} for every role the preset or the kit gives."""
    out: Dict[str, Tuple[str, str]] = {}
    if preset is not None:
        out.update({r: (c, "preset") for r, c in STYLE_PRESETS[preset]["roles"].items()})
    for r, c in (kit or {}).items():
        out[r] = (c, "brand_kit")
    return out


def style_values(etype: str, preset: Optional[str], kit: Optional[Dict[str, str]], given: Dict[str, Any],
                 has_animation: bool, platform_set: bool) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Dict[str, str]]:
    """The raw parameter values a preset (and brand kit) supplies for one element, for the parameters the request left out.

    Returns (parameters, animation-or-None, sources) where sources maps each filled parameter (and "animation") to
    "preset" or "brand_kit". Values are in request form, so they go through the same validation as a caller's own.
    A margin is not supplied when document.options.platform is set: there an omitted margin is the destination's safe zone,
    which a preset must not override. With a kit and no preset, only role colours are supplied."""
    targets = STYLE_TARGETS.get(etype, {})
    roles = resolve_roles(preset, kit)
    spec = STYLE_PRESETS.get(preset) if preset is not None else None
    params: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    animation: Optional[Dict[str, Any]] = None
    for param, source in targets.items():
        if param == "animation":
            if spec is not None and spec["fade"] and not has_animation:
                animation = {"kind": "fade", "parameters": {"duration": spec["fade"]}}
                sources["animation"] = "preset"
            continue
        if param in given:
            continue
        if source.startswith("role:"):
            role = source[5:]
            if role in roles:
                params[param], sources[param] = roles[role]
        elif source == "box_color":
            if spec is not None and spec["box"] and "background" in roles:
                colour, src = roles["background"]
                params[param] = f"{colour}@{spec['box_opacity']:g}"
                sources[param] = src
        elif spec is not None:
            if source == "margin" and platform_set:
                continue
            value = spec[source]
            params[param] = {"font_id": value} if source == "font_id" else value
            sources[param] = "preset"
    return params, animation, sources


def contract_section() -> Dict[str, Any]:
    """The `style_presets` block of the contract: every preset with its values, and what each element type takes from it."""
    return {
        "names": sorted(STYLE_PRESETS),
        "roles": list(ROLES),
        "presets": [{"name": n, "description": p["description"], "roles": dict(p["roles"]), "font_id": p["font_id"], "box": p["box"],
                     "box_opacity": p["box_opacity"], "border_width": p["border_width"], "margin": p["margin"], "fade": p["fade"]}
                    for n, p in sorted(STYLE_PRESETS.items())],
        "targets": {t: dict(m) for t, m in sorted(STYLE_TARGETS.items())},
        "request_fields": {"element": "style (a preset name; overrides options.style for that element)",
                           "document": "options.style (the preset for every element without its own style)",
                           "brand_kit": "options.brand_kit {primary?, secondary?, background?, text?}: opaque RRGGBB or a colour name"},
        "precedence": "an explicit parameter > a brand_kit role > the preset's role; a preset fills only optional parameters the request left out",
        "notes": ["box_color is the background role at the preset's box_opacity (ffmpeg-skill/overlay takes @alpha)",
                  "a preset supplies no margin when options.platform is set (the destination's safe zone applies)",
                  "a preset fade is applied to text_overlay / image_overlay only, and only when the element has no animation; "
                  "a preset fade longer than half the element's window is left out and listed in style.skipped",
                  "meme takes nothing from a preset: its colours, position and size are fixed by the template",
                  "no new animation: a preset never adds one an element type does not already offer"],
        "response": "each styled element carries style {preset, brand_kit, applied: {parameter: preset|brand_kit}, skipped?: [...]} in validate/plan output and in the run response's timeline",
    }


def applied_summary(preset: Optional[str], kit: Optional[Dict[str, str]], sources: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if preset is None and not kit:
        return None
    return {"preset": preset, "brand_kit": bool(kit), "applied": dict(sorted(sources.items()))}


def all_target_parameters() -> List[Tuple[str, str]]:
    return [(t, p) for t, m in STYLE_TARGETS.items() for p in m if p != "animation"]
