"""Brand themes (docs/decisions.md ADR-29): colours, font, logo and spacing in one JSON file, applied to every element at once.

A theme file:

    {"schema": "motion-graphics/theme@1", "name": "acme",
     "colors": {"primary": "0B5FFF", "secondary": "00A3E0", "background": "0F1C2E", "text": "FFFFFF"},
     "font": {"font_id": "system:dejavu-serif"} | {"font_file": "fonts/Brand.ttf"},
     "logo": {"path": "logo.png", "position": "top-right", "margin": 32, "scale_width": 160, "opacity": 1.0},
     "spacing": {"margin": 40},
     "base_style": "corporate"}

`colors` (all four roles) is required; the rest is optional. Relative paths are relative to the theme file and go through the
same PathPolicy as every other input. A request names its themes (`options.themes`: {name: file}) and picks one
(`options.theme`); switching theme is changing that one name.

What a theme does, per element (the same targets as a style preset, styles.STYLE_TARGETS): its colours are the role colours, its
spacing.margin the margin, its font the text_overlay font and the font every graphics template draws with (ffmpeg-skill/graphics
--font / --font-file, which every checkout has), its base_style the preset for the other settings; the `logo` element type draws
its logo. Precedence: an explicit parameter > the theme > the base_style preset.

Contrast: each theme is checked with the relative-luminance contrast ratio, (L1 + 0.05) / (L2 + 0.05) of the sRGB colours, for the pairs
the templates draw, and every pair under its minimum
is a warning (`theme.contrast`). This skill reports; it never changes a colour."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import SKILL_ID
from .errors import MotionGraphicsError
from .model import ID_RE, POSITION_NAMES
from .styles import ROLES, STYLE_PRESETS, _kit_color

THEME_SCHEMA = f"{SKILL_ID}/theme@1"
MAX_THEMES = 16
MAX_THEME_BYTES = 256 * 1024
# the dark plate ffmpeg-skill/graphics draws the lower third on (its default brand background, the same in every checkout this
# skill runs against); the theme's text and primary colours are drawn on it, so they are checked against it too
TEMPLATE_PLATE = "101418"
# (foreground role or fixed colour, background role or fixed colour, minimum ratio, what is drawn that way)
CONTRAST_PAIRS = (
    ("text", "background", 4.5, "text on the theme background (text_overlay box, shape plates)"),
    ("primary", "background", 3.0, "primary accents on the theme background (graphical objects)"),
    ("text", TEMPLATE_PLATE, 4.5, "lower_third name on the template's dark plate"),
    ("primary", TEMPLATE_PLATE, 4.5, "lower_third second line, drawn in primary on the template's dark plate"),
)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rrggbb: str) -> float:
    r, g, b = (int(rrggbb[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def contrast_report(colors: Dict[str, str], pairs: tuple = CONTRAST_PAIRS) -> List[Dict[str, Any]]:
    out = []
    for fg, bg, minimum, what in pairs:
        a, b = colors.get(fg, fg), colors.get(bg, bg)
        ratio = round(contrast_ratio(a, b), 2)
        out.append({"foreground": fg, "background": bg if bg in colors else f"template plate {bg}", "colors": [a, b], "ratio": ratio,
                    "minimum": minimum, "ok": ratio >= minimum, "drawn_as": what})
    return out


def _fail(where: str, message: str, **details: Any) -> MotionGraphicsError:
    return MotionGraphicsError("INVALID_INPUT", f"{where}: {message}", dict({"field": where, "reason": "bad_theme"}, **details))


def _obj(value: Any, where: str, allowed: tuple, required: tuple = ()) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "must be an object")
    unknown = sorted(k for k in value if k not in allowed)
    if unknown:
        raise _fail(where, f"unknown field(s) {unknown}", unknown=unknown, allowed=list(allowed))
    missing = [k for k in required if k not in value]
    if missing:
        raise _fail(where, f"missing field(s) {missing}", missing=missing)
    return value


def parse_theme(doc: Any, where: str) -> Dict[str, Any]:
    """A validated theme (paths still relative strings; see resolve_theme_paths)."""
    from .model import _reject_forbidden
    _reject_forbidden(doc, where)
    top = _obj(doc, where, ("schema", "name", "colors", "font", "logo", "spacing", "base_style"), ("colors",))
    if "schema" in top and top["schema"] != THEME_SCHEMA:
        raise _fail(f"{where}.schema", f"must be {THEME_SCHEMA!r}")
    name = top.get("name")
    if name is not None and (not isinstance(name, str) or not ID_RE.match(name)):
        raise _fail(f"{where}.name", "must be a short identifier")
    colors_raw = _obj(top["colors"], f"{where}.colors", ROLES, ROLES)
    try:
        colors = {r: _kit_color(colors_raw[r], f"{where}.colors.{r}") for r in ROLES}
    except MotionGraphicsError as e:
        raise _fail(e.details.get("field", where), e.message)
    theme: Dict[str, Any] = {"name": name, "colors": colors, "font": None, "logo": None, "margin": None, "base_style": None}
    if "font" in top:
        f = _obj(top["font"], f"{where}.font", ("font_id", "font_file"))
        if len(f) != 1 or not isinstance(next(iter(f.values())), str) or not next(iter(f.values())):
            raise _fail(f"{where}.font", "give font_id or font_file (one of them, a string)")
        theme["font"] = dict(f)
    if "logo" in top:
        lg = _obj(top["logo"], f"{where}.logo", ("path", "position", "margin", "scale_width", "opacity"), ("path",))
        if not isinstance(lg["path"], str) or not lg["path"]:
            raise _fail(f"{where}.logo.path", "must be a file path")
        if "position" in lg and lg["position"] not in POSITION_NAMES:
            raise _fail(f"{where}.logo.position", f"must be one of {POSITION_NAMES}")
        for k, lo, hi, kind in (("margin", 0, 2000, int), ("scale_width", 1, 8192, int), ("opacity", 0.0, 1.0, float)):
            if k in lg:
                v = lg[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind is int and not isinstance(v, int)) or not (lo <= v <= hi):
                    raise _fail(f"{where}.logo.{k}", f"must be within [{lo}, {hi}]")
        theme["logo"] = dict(lg)
    if "spacing" in top:
        sp = _obj(top["spacing"], f"{where}.spacing", ("margin",), ("margin",))
        m = sp["margin"]
        if isinstance(m, bool) or not isinstance(m, int) or not (0 <= m <= 2000):
            raise _fail(f"{where}.spacing.margin", "must be an integer within [0, 2000]")
        theme["margin"] = m
    if "base_style" in top:
        if top["base_style"] not in STYLE_PRESETS:
            raise _fail(f"{where}.base_style", f"must be one of {sorted(STYLE_PRESETS)}")
        theme["base_style"] = top["base_style"]
    theme["contrast"] = contrast_report(colors)
    return theme


def parse_theme_options(options: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{themes: {name: path}, theme: name} from document.options, structurally (no file access); None when no theme is asked for."""
    themes, chosen = options.get("themes"), options.get("theme")
    if themes is None and chosen is None:
        return None
    where = "document.options.themes"
    if not isinstance(themes, dict) or not (1 <= len(themes) <= MAX_THEMES):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must map 1 to {MAX_THEMES} theme names to theme files", {"field": where})
    for k, v in themes.items():
        if not ID_RE.match(k) or not isinstance(v, str) or not v or "\x00" in v or len(v) > 4096:
            raise MotionGraphicsError("INVALID_REQUEST", f"{where}.{k}: a theme name maps to a file path", {"field": f"{where}.{k}"})
    if chosen is None:
        if len(themes) != 1:
            raise MotionGraphicsError("INVALID_REQUEST", "document.options.theme must name one of document.options.themes", {"field": "document.options.theme"})
        chosen = next(iter(themes))
    if not isinstance(chosen, str) or chosen not in themes:
        raise MotionGraphicsError("INVALID_REQUEST", f"document.options.theme must be one of {sorted(themes)}: {chosen!r}",
                                  {"field": "document.options.theme", "allowed": sorted(themes)})
    return {"themes": dict(themes), "theme": chosen}


def read_theme_document(path: Any, where: str) -> Any:
    """The JSON document of a theme file: at most MAX_THEME_BYTES bytes (read as bytes, one byte past the limit to tell), decoded as
    UTF-8, then parsed. The one reader for `load_theme` and `theme-check`, so both refuse the same files the same way."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_THEME_BYTES + 1)
    except OSError as e:
        raise _fail(where, f"cannot read the theme file: {e}")
    if len(raw) > MAX_THEME_BYTES:
        raise _fail(where, f"theme file is larger than {MAX_THEME_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _fail(where, f"theme file is not valid UTF-8 JSON: {e}")


def load_theme(policy: Any, path_text: str, where: str) -> Dict[str, Any]:
    """Read, validate and resolve one theme file: its font_file and logo path become PathPolicy-checked absolute paths."""
    from .canonical import sha256_file
    path = policy.resolve_input(path_text, where)
    theme = parse_theme(read_theme_document(path, where), where)
    resolve_theme_paths(policy, theme, path.parent, where)
    theme["file"] = {"sha256": sha256_file(str(path)), "size": path.stat().st_size}
    return theme


def resolve_theme_paths(policy: Any, theme: Dict[str, Any], base: Path, where: str) -> None:
    """A parsed theme's font_file and logo path, relative to `base` (the file they were read from), made PathPolicy-checked absolute paths."""
    def rel(p: str, what: str) -> str:
        q = Path(p)
        return str(policy.resolve_input(str(q if q.is_absolute() else base / q), what))

    if theme["font"] and "font_file" in theme["font"]:
        theme["font"] = {"font_file": rel(theme["font"]["font_file"], f"{where}.font.font_file")}
    if theme["logo"]:
        theme["logo"]["path"] = rel(theme["logo"]["path"], f"{where}.logo.path")


def warnings_for(theme: Dict[str, Any]) -> List[str]:
    out = []
    for c in theme["contrast"]:
        if not c["ok"]:
            out.append(f"theme {theme.get('label')!r}: low contrast {c['ratio']:g}:1 (minimum {c['minimum']:g}:1) for {c['drawn_as']} "
                       f"({c['colors'][0]} on {c['colors'][1]})")
    return out


def contract_section() -> Dict[str, Any]:
    return {
        "file_schema": THEME_SCHEMA,
        "file_shape": {"schema?": THEME_SCHEMA, "name?": "id", "colors": {r: "RRGGBB or a colour name" for r in ROLES},
                       "font?": "{font_id} | {font_file} (relative to the theme file)", "logo?": {"path": "PNG/JPG, relative to the theme file",
                       "position?": "a named position", "margin?": "px", "scale_width?": "px", "opacity?": "0..1"},
                       "spacing?": {"margin": "px"}, "base_style?": "a style_presets name"},
        "request_fields": {"options.themes": f"{{name: theme file}}, 1 to {MAX_THEMES}", "options.theme": "the theme to use (optional when there is one)"},
        "applies": ["colours: the role colours of every element (style_presets.targets), as a brand kit's would be",
                    "spacing.margin: every margin target", "font: text_overlay's font and every graphics template's (--font / --font-file)",
                    "logo: the `logo` element type", "base_style: the preset for the settings the theme does not give"],
        "precedence": "an explicit parameter > the theme > its base_style preset; options.brand_kit and options.style are refused together with a theme",
        "contrast": {"method": "(L1 + 0.05) / (L2 + 0.05) of the relative luminances of the sRGB colours, lighter over darker", "template_plate": TEMPLATE_PLATE,
                     "pairs": [{"foreground": f, "background": b, "minimum": m, "drawn_as": w} for f, b, m, w in CONTRAST_PAIRS],
                     "below_minimum": "a warning (response.warnings) and ok: false in response.theme.contrast; colours are never changed"},
        "result": "response.theme {name, label, file {sha256, size}, colors, font, logo, margin, base_style, contrast}; operations[].font_used when "
                  "ffmpeg-skill reports the font it drew with",
        "validate": "`validate` touches no files, so it checks options.themes / options.theme structurally and applies no theme",
        "check_command": "motion-graphics theme-check <theme.json> --json prints the validated theme and its contrast report",
    }
