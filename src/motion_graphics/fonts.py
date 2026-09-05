"""Font handling: an explicit, closed registry of system fonts plus PathPolicy-checked custom font files.

A request never supplies a raw fontconfig name or an arbitrary filesystem path string that is trusted as-is:
- `font_id` selects one entry of FONT_REGISTRY (the only fontconfig names this skill will ever emit into a
  drawtext `font=` argument). Unknown font_id is rejected (MISSING_INPUT) -- it is never silently mapped to a
  different, available font and reported as success.
- `font_file` is a path that goes through the exact same PathPolicy.resolve_input() as video and image assets
  (workspace / allowed-roots / symlink checks), must have an allowed font extension, and is passed to the engine
  as `fontfile=<resolved absolute path>` (ffmpeg-skill's own escaping applies; this skill never builds the filter
  string itself). Its sha256 is recorded in provenance as `font_file_hash`.
- No field is provided: the default is the explicit system font below (`DEFAULT_FONT_ID`), recorded in
  provenance exactly like a requested one -- it is a documented default, not an unreported fallback."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .canonical import sha256_file
from .errors import MotionGraphicsError
from .security import PathPolicy

# font_id -> fontconfig family name this skill is willing to pass to ffmpeg-skill's `--font` / `font=`.
# Matches the `fonts-dejavu-core` package that ships in the CI image and is the ffmpeg-skill default font.
FONT_REGISTRY: Dict[str, Dict[str, str]] = {
    "system:dejavu-sans": {"family": "DejaVu Sans", "description": "Default sans-serif, always available where fonts-dejavu-core is installed"},
    "system:dejavu-serif": {"family": "DejaVu Serif", "description": "Serif variant of the DejaVu family"},
    "system:dejavu-sans-mono": {"family": "DejaVu Sans Mono", "description": "Monospace variant of the DejaVu family"},
}
DEFAULT_FONT_ID = "system:dejavu-sans"
ALLOWED_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")
_WIN_EXTENDED_PREFIX = "\\\\?\\"


def _engine_font_file_arg(resolved_path: str) -> str:
    """The path string handed to ffmpeg-skill's `--font-file`, which it embeds into a `fontfile=...` *filter*
    option (drawtext) -- a different context from a plain `-i`/`-o` argv value. On Windows, Path.resolve(strict=True)
    can return an extended-length path with a `\\\\?\\` prefix; converting THAT to forward slashes (as ffmpeg-skill's
    own filter escaping does) produces `//?/C:/...`, which is not a path ffmpeg's file layer recognises, and its
    filter-option parser then fails on the mangled result ("No option name near ...", "Invalid argument"). The
    plain drive-letter form below is well within Windows' normal path length here and both opens correctly and
    escapes correctly as a filter option value."""
    if resolved_path.startswith(_WIN_EXTENDED_PREFIX):
        rest = resolved_path[len(_WIN_EXTENDED_PREFIX):]
        resolved_path = "\\\\" + rest[4:] if rest.startswith("UNC\\") else rest
    return resolved_path.replace("\\", "/")


@dataclass(frozen=True)
class ResolvedFont:
    kind: str            # "system" | "file"
    font_id: Optional[str]        # set for kind == "system"
    font_name: str                # fontconfig family (system) or file stem (file) -- for display/provenance only
    engine_arg: Dict[str, str]    # {"font": "<family>"} or {"font_file": "<resolved path>"}
    font_file_hash: Optional[str] = None
    font_file_path: Optional[str] = None

    def to_provenance(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "font_name": self.font_name}
        if self.font_id:
            d["font_id"] = self.font_id
        if self.font_file_hash:
            d["font_file_hash"] = self.font_file_hash
        return d


def resolve_font(spec: Optional[Dict[str, Any]], policy: PathPolicy) -> ResolvedFont:
    if not spec:
        entry = FONT_REGISTRY[DEFAULT_FONT_ID]
        return ResolvedFont("system", DEFAULT_FONT_ID, entry["family"], {"font": entry["family"]})
    font_id = spec.get("font_id")
    font_file = spec.get("font_file")
    if font_id and font_file:
        raise MotionGraphicsError("INVALID_REQUEST", "font: give font_id or font_file, not both", {"field": "font"})
    if font_id:
        registered = FONT_REGISTRY.get(font_id)
        if registered is None:
            raise MotionGraphicsError("MISSING_INPUT", f"unknown font_id {font_id!r} (not in the font registry)",
                                       {"field": "font_id", "font_id": font_id, "available": sorted(FONT_REGISTRY)})
        return ResolvedFont("system", font_id, registered["family"], {"font": registered["family"]})
    if font_file:
        resolved = policy.resolve_input(font_file, "font_file")
        if resolved.suffix.lower() not in ALLOWED_FONT_EXTENSIONS:
            raise MotionGraphicsError("UNSUPPORTED_FORMAT", f"font_file must be one of {ALLOWED_FONT_EXTENSIONS}: {resolved.suffix}",
                                       {"field": "font_file", "extension": resolved.suffix})
        digest = sha256_file(str(resolved))
        return ResolvedFont("file", None, resolved.stem, {"font_file": _engine_font_file_arg(str(resolved))}, font_file_hash=digest, font_file_path=str(resolved))
    entry = FONT_REGISTRY[DEFAULT_FONT_ID]
    return ResolvedFont("system", DEFAULT_FONT_ID, entry["family"], {"font": entry["family"]})
