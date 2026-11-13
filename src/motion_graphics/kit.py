"""Brand kit reader (docs/decisions.md ADR-31): `options.kit` names a file in the Velisa `kit@1` shape, a superset of ffmpeg-skill
`brand.json`, and it is converted onto this skill's own inputs by the mapping table of the Velisa spec (docs/specs/kit.md):

    kit@1                                  motion-graphics
    colors.primary / secondary /           theme@1 colors (all four roles), or options.brand_kit with the roles
      background / text                    the kit has when one is missing (a theme needs all four)
    font_file                              theme font.font_file
    logo                                   theme logo.path
    logo_position                          theme logo.position (one of the nine position names)
    logo_scale                             theme logo.scale_width
    logo_opacity                           theme logo.opacity
    safe_margin                            theme spacing.margin and logo.margin
    name                                   theme name

Only what the kit states is passed on: ffmpeg-skill's brand.json defaults are not the user's choice, so nothing is filled in. Every
other kit key (`font` as a family name, `colors.outline`, `colors.accent`, `lang`, `styles`, `caption`, `loudness`, `fonts`,
`logo_variants`, `contrast`, keys kit@1 does not define) is reported in `response.kit.unmapped` with the reason, never dropped
silently. `colors.accent` is not used as `secondary`: the spec leaves that a choice to state, not a default.

The mapped theme is an ordinary theme@1 document, checked by themes.parse_theme like a theme file, with paths relative to the kit
file; from there on it is applied as a theme is (ADR-29). `options.style` is the preset under it: an explicit parameter > the kit >
the preset."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import MotionGraphicsError
from .model import ID_RE, POSITION_NAMES
from .styles import ROLES, parse_brand_kit
from .themes import CONTRAST_PAIRS, THEME_SCHEMA, contrast_report, parse_theme, read_theme_document, resolve_theme_paths

KIT_SCHEMA = "velisa/kit@1"
# keys a kit@1 / brand.json defines that have no counterpart in theme@1 or brand_kit, and why (spec: "Keys from brand.json" and
# "Keys added by kit@1")
NO_COUNTERPART = {
    "font": "a family name: theme@1 takes font_id or font_file, and a family name is neither",
    "lang": "a language hint for ffmpeg-skill --lang; elements take lang per element (fit.lang)",
    "styles": "caption styles: this skill draws no captions",
    "caption": "caption burn-in defaults: this skill draws no captions",
    "loudness": "an audio target: this skill does not touch the audio level",
    "fonts": "font licence records: kept in the kit, theme@1 has no field for them",
    "logo_variants": "logo variants: theme@1 has one logo (logo.path)",
    "contrast": "a contrast record: this skill checks the pairs its templates draw itself (theme.contrast)",
}
COLOR_NO_COUNTERPART = {
    "outline": "theme@1 has no outline role",
    "accent": "theme@1 has no accent role; using it as secondary is a choice to state, not a default",
}
THEME_ONLY = ("name", "font_file", "logo", "logo_position", "logo_scale", "logo_opacity", "safe_margin")


def _fail(where: str, message: str, **details: Any) -> MotionGraphicsError:
    return MotionGraphicsError("INVALID_INPUT", f"{where}: {message}", dict({"field": where, "reason": "bad_kit"}, **details))


def map_kit(doc: Any, where: str) -> Dict[str, Any]:
    """The kit converted by the mapping table: {mapped_to: "theme@1" | "brand_kit", theme | brand_kit, unmapped: [{key, reason}]}.
    No file access; paths stay as the kit states them (relative to the kit file)."""
    from .model import _reject_forbidden
    try:
        _reject_forbidden(doc, where)
    except MotionGraphicsError as e:
        raise _fail(where, e.message, **{k: v for k, v in e.details.items() if k != "reason"})
    if not isinstance(doc, dict):
        raise _fail(where, "must be a JSON object")
    if "schema" in doc and doc["schema"] != KIT_SCHEMA:
        raise _fail(f"{where}.schema", f"must be {KIT_SCHEMA!r} (or absent, as in a plain brand.json): {doc['schema']!r}")
    if "colors" in doc and "colours" in doc:
        raise _fail(where, "give colors or colours, not both")
    ckey = "colours" if "colours" in doc else "colors"
    colors_raw = doc.get(ckey, {})
    if not isinstance(colors_raw, dict):
        raise _fail(f"{where}.{ckey}", "must be an object")
    unmapped: List[Dict[str, str]] = []
    roles: Dict[str, Any] = {}
    for k, v in colors_raw.items():
        if k in ROLES:
            roles[k] = v
        else:
            unmapped.append({"key": f"{ckey}.{k}", "reason": COLOR_NO_COUNTERPART.get(k, "not a colour role of theme@1 or brand_kit")})
    if not roles:
        raise _fail(where, f"gives none of the colour roles {list(ROLES)}: nothing maps onto theme@1 or brand_kit", reason="kit_without_colors")
    for k in doc:
        if k in ("schema", "colors", "colours") or k in THEME_ONLY:
            continue
        unmapped.append({"key": k, "reason": NO_COUNTERPART.get(k, "not a kit@1 key")})

    if set(roles) != set(ROLES):
        missing = [r for r in ROLES if r not in roles]
        for k in THEME_ONLY:
            if k in doc:
                unmapped.append({"key": k, "reason": f"the kit has no {', '.join(f'{ckey}.{r}' for r in missing)}, so no theme@1 can be made "
                                                     "(it needs all four roles); options.brand_kit takes colours only"})
        try:
            kit = parse_brand_kit(roles, f"{where}.{ckey}")
        except MotionGraphicsError as e:
            raise _fail(e.details.get("field", where), e.message)
        return {"mapped_to": "brand_kit", "brand_kit": kit, "unmapped": sorted(unmapped, key=lambda u: u["key"])}

    theme: Dict[str, Any] = {"schema": THEME_SCHEMA, "colors": {r: roles[r] for r in ROLES}}
    if "name" in doc:
        if isinstance(doc["name"], str) and ID_RE.match(doc["name"]):
            theme["name"] = doc["name"]
        else:
            unmapped.append({"key": "name", "reason": "theme@1 name is a short identifier (letters, digits, . _ -, at most 64)"})
    if "font_file" in doc:
        theme["font"] = {"font_file": doc["font_file"]}
    if "logo" in doc:
        logo: Dict[str, Any] = {"path": doc["logo"]}
        if "logo_position" in doc:
            if doc["logo_position"] not in POSITION_NAMES:
                raise _fail(f"{where}.logo_position", f"must be one of the position names {list(POSITION_NAMES)} to map onto theme@1 "
                                                      f"logo.position: {doc['logo_position']!r}")
            logo["position"] = doc["logo_position"]
        if "safe_margin" in doc:
            logo["margin"] = doc["safe_margin"]
        if "logo_scale" in doc:
            logo["scale_width"] = doc["logo_scale"]
        if "logo_opacity" in doc:
            logo["opacity"] = doc["logo_opacity"]
        theme["logo"] = logo
    else:
        for k in ("logo_position", "logo_scale", "logo_opacity"):
            if k in doc:
                unmapped.append({"key": k, "reason": "the kit has no logo"})
    if "safe_margin" in doc:
        theme["spacing"] = {"margin": doc["safe_margin"]}
    return {"mapped_to": "theme@1", "theme": theme, "unmapped": sorted(unmapped, key=lambda u: u["key"])}


def parse_kit_option(value: Any) -> str:
    """options.kit, structurally (no file access): a kit file path."""
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 4096:
        raise MotionGraphicsError("INVALID_REQUEST", "document.options.kit must be the path of a kit@1 / brand.json file", {"field": "document.options.kit"})
    return value


def partial_contrast(kit: Dict[str, str]) -> List[Dict[str, Any]]:
    """The theme contrast pairs whose two colours are known: both roles in the brand_kit, or a role and the fixed template plate."""
    return contrast_report(kit, tuple(p for p in CONTRAST_PAIRS if p[0] in kit and (p[1] in kit or p[1] not in ROLES)))


def load_kit(policy: Any, path_text: str, where: str = "document.options.kit") -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Read and map one kit file. Returns (record, theme): the record for response.kit, and the loaded theme (paths resolved
    through PathPolicy, relative to the kit file) when the kit maps onto theme@1, else None (record["brand_kit"] holds the colours)."""
    from .canonical import sha256_file
    path = policy.resolve_input(path_text, where)
    try:
        doc = read_theme_document(path, where)
    except MotionGraphicsError as e:
        raise _fail(where, e.message.split(": ", 1)[-1].replace("theme file", "kit file"))
    mapped = map_kit(doc, where)
    record: Dict[str, Any] = {"schema": doc.get("schema"), "file": {"sha256": sha256_file(str(path)), "size": path.stat().st_size}, **mapped}
    if mapped["mapped_to"] == "brand_kit":
        record["contrast"] = partial_contrast(mapped["brand_kit"])
        return record, None
    try:
        theme = parse_theme(mapped["theme"], f"{where} (as {THEME_SCHEMA})")
    except MotionGraphicsError as e:
        raise _fail(e.details.get("field", where), e.message)
    resolve_theme_paths(policy, theme, Path(path).parent, where)
    theme["file"] = dict(record["file"])
    return record, theme


def warnings_for_kit(record: Dict[str, Any]) -> List[str]:
    out = [f"options.kit: {u['key']} is not used ({u['reason']})" for u in record["unmapped"]]
    for c in record.get("contrast", []):
        if not c["ok"]:
            out.append(f"options.kit: low contrast {c['ratio']:g}:1 (minimum {c['minimum']:g}:1) for {c['drawn_as']} ({c['colors'][0]} on {c['colors'][1]})")
    return out


def contract_section() -> Dict[str, Any]:
    return {
        "file_schema": KIT_SCHEMA,
        "superset_of": "ffmpeg-skill brand.json: a plain brand.json (no schema key) is read the same way",
        "request_field": "options.kit: the kit file's path; refused together with options.themes / options.theme / options.brand_kit",
        "mapping": {"colors.primary|secondary|background|text": "theme@1 colors (all four) or options.brand_kit (the roles the kit has)",
                    "font_file": "theme font.font_file", "logo": "theme logo.path", "logo_position": "theme logo.position (a named position)",
                    "logo_scale": "theme logo.scale_width", "logo_opacity": "theme logo.opacity",
                    "safe_margin": "theme spacing.margin and logo.margin", "name": "theme name (when it is a short identifier)",
                    "colours": "read as colors (remotion spelling)"},
        "without_all_four_roles": "options.brand_kit with the roles the kit has; the theme-only keys are reported unmapped",
        "unmapped": "every other key, with the reason, in response.kit.unmapped and as a warning; nothing is dropped silently",
        "defaults": "none: only what the kit states is passed on (ffmpeg-skill's brand.json defaults are not filled in)",
        "precedence": "an explicit parameter > the kit > options.style (the preset)",
        "result": "response.kit {schema, file {sha256, size}, mapped_to, theme | brand_kit, unmapped, contrast?}; with theme@1 also "
                  "response.theme (label \"kit\", contrast on the kit's colours)",
        "validate": "`validate` touches no files: options.kit is checked as a path and no kit is applied",
    }
