"""Layer export (docs/decisions.md ADR-30): beside the burned programme, every element rendered alone on a transparent picture.

    "options": {"layer_export": {"directory": "layers", "alpha_codec?": "prores4444", "alpha_mode?": "straight"}}

One transparent canvas the size, rate and length of the source video is made once (ffmpeg-skill/background --transparent, then
color --retag with the programme's colour tags, which every layer drawn on it keeps), and
each element is drawn on it by the same ffmpeg-skill call that burns it into the programme, with `--keep-alpha`: the same
template, text, position, margin, timing and fade, so a layer placed at 0 s over the clean video gives the burned picture back.
One file per element (`<element id>.<ext>`) and a `manifest.json` (element id, type, start, end, box, file, sha256, the alpha
check) are written to the directory; ffmpeg-skill reads each layer's alpha back (its `alpha` verification step) and the result
records it with `verified`.

Pure here: parsing the request option, the file names and the manifest. The calls are made by the executor."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import SKILL_ID
from .errors import MotionGraphicsError

LAYERS_MANIFEST_SCHEMA = f"{SKILL_ID}/layers@1"
MANIFEST_NAME = "manifest.json"
# ffmpeg-skill's alpha lines (_common/alpha.ALPHA_CODECS) and the container each layer is written in. ProRes 4444 is ffmpeg-skill's
# own default; qtrle and png are lossless RGB + alpha in a .mov; vp9 is VP9 with an alpha layer in a .webm.
ALPHA_CODECS: Dict[str, str] = {"prores4444": ".mov", "qtrle": ".mov", "png": ".mov", "vp9": ".webm"}
DEFAULT_ALPHA_CODEC = "prores4444"
ALPHA_MODES = ("straight", "premultiplied")
DEFAULT_ALPHA_MODE = "straight"
# what each ffmpeg-skill tool must take for a layer to be drawn: background makes the canvas, graphics / overlay draw on it
# keeping its alpha. Detected from the checkout (adapter.check_tools), never inferred from a version number (ADR-18).
LAYER_EXPORT_FLAGS: Dict[str, tuple] = {"background": ("transparent", "fps", "alpha_codec", "alpha_mode"),
                                        "graphics": ("keep_alpha", "alpha_codec", "alpha_mode"),
                                        "overlay": ("keep_alpha", "alpha_codec", "alpha_mode"),
                                        "color": ("retag",)}
# ffmpeg-skill/color --retag gives the transparent canvas the programme's colour tags (a stream copy): graphics / overlay --keep-alpha
# keep the tags of what they draw on, so every layer says the matrix it was drawn in. Without them a layer reads as "unknown", and a
# compositor that negotiates colour (FFmpeg 7.1 and later) converts the picture under it from BT.709 to its guess.
FIELDS = ("directory", "alpha_codec", "alpha_mode")


def retag_for(video: Dict[str, Any]) -> Optional[str]:
    """ffmpeg-skill/color --retag's name for the colour tags a probed video stream carries, or None when they are none it names."""
    space, transfer = video.get("color_space"), video.get("color_transfer")
    if space == "bt709":
        return "bt709"
    if space in ("bt2020nc", "bt2020c"):
        return {"smpte2084": "bt2020-pq", "arib-std-b67": "bt2020-hlg"}.get(transfer or "")
    if space in ("smpte170m", "bt470bg"):
        return "bt601"
    return None


def parse_layer_export(value: Any, element_ids: Sequence[str]) -> Dict[str, Any]:
    """options.layer_export -> {directory, alpha_codec, alpha_mode}. Structural only (no file system: the executor resolves the
    directory through PathPolicy). Element ids that differ only in letter case are refused: their layer files would be one file
    on a case-insensitive file system (Windows, macOS)."""
    where = "document.options.layer_export"
    if not isinstance(value, dict):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    unknown = sorted(k for k in value if k not in FIELDS)
    if unknown:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: unknown field(s) {unknown}", {"field": where, "unknown": unknown, "allowed": list(FIELDS)})
    directory = value.get("directory")
    if not isinstance(directory, str) or not directory.strip() or len(directory) > 4096:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}.directory must be a non-empty path string", {"field": f"{where}.directory"})
    codec = value.get("alpha_codec", DEFAULT_ALPHA_CODEC)
    if codec not in ALPHA_CODECS:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}.alpha_codec must be one of {sorted(ALPHA_CODECS)}: {codec!r}", {"field": f"{where}.alpha_codec"})
    mode = value.get("alpha_mode", DEFAULT_ALPHA_MODE)
    if mode not in ALPHA_MODES:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}.alpha_mode must be one of {list(ALPHA_MODES)}: {mode!r}", {"field": f"{where}.alpha_mode"})
    seen: Dict[str, str] = {}
    for eid in element_ids:
        key = eid.lower()
        if key in seen and seen[key] != eid:
            raise MotionGraphicsError("INVALID_REQUEST", f"{where}: element ids {seen[key]!r} and {eid!r} differ only in letter case, and their layer files "
                                      f"would be one file on a case-insensitive file system", {"field": where, "reason": "layer_name_collision", "ids": [seen[key], eid]})
        seen[key] = eid
    return {"directory": directory, "alpha_codec": codec, "alpha_mode": mode}


def layer_file_name(element_id: str, alpha_codec: str) -> str:
    """The layer file of one element: its id (ID_RE: letters, digits, '.', '_', '-') and the codec's container."""
    return element_id + ALPHA_CODECS[alpha_codec]


def alpha_step(data: Any) -> Optional[Dict[str, Any]]:
    """ffmpeg-skill's `alpha` verification step from a tool's JSON result, or None when it reported none."""
    steps = data.get("verification") if isinstance(data, dict) else None
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict) and step.get("step") == "alpha":
            return step
    return None


def manifest_document(programme: Dict[str, Any], settings: Dict[str, Any], canvas: Dict[str, Any], layers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The manifest written beside the layers. File names only (no absolute path), so the directory can be moved as a whole."""
    return {"schema": LAYERS_MANIFEST_SCHEMA, "skill": SKILL_ID,
            "programme": programme, "alpha_codec": settings["alpha_codec"], "alpha_mode": settings["alpha_mode"],
            "canvas": canvas,
            "composite": "each layer is the programme's full length from 0 s, at 0,0, the programme's size; laid over the clean video in this list's "
                         "order (the order the programme was rendered in) it gives the burned programme",
            "layers": [{k: layer[k] for k in ("id", "type", "start", "end", "box", "box_precision", "file", "sha256", "size", "duration",
                                              "alpha_codec", "alpha_mode", "verified", "alpha") if k in layer} for layer in layers],
            "verified": all(layer.get("verified") is True for layer in layers)}


def contract_section() -> Dict[str, Any]:
    """The `layer_export` block of the contract."""
    return {
        "request_field": "options.layer_export",
        "shape": {"directory": "path inside the workspace (PathPolicy): the layer files and manifest.json are written there",
                  "alpha_codec?": f"{' | '.join(sorted(ALPHA_CODECS))} (default {DEFAULT_ALPHA_CODEC})",
                  "alpha_mode?": f"{' | '.join(ALPHA_MODES)} (default {DEFAULT_ALPHA_MODE})"},
        "containers": dict(ALPHA_CODECS),
        "files": "one per element, <element id><container>, plus manifest.json; an existing one is replaced only with document.output.overwrite "
                 "(OUTPUT_ERROR, details.reason exists, before anything renders)",
        "timing": "every layer has the programme's size, frame rate and length and starts at 0 s; the element shows at its own start..end, as in the programme",
        "how": "ffmpeg-skill/background --transparent makes one canvas (the source's size, rate and length) and ffmpeg-skill/color --retag gives it the "
               "programme's colour tags; each element is the same graphics / overlay call that burns it into the programme, on that canvas, with "
               "--keep-alpha --alpha-codec --alpha-mode (no audio), so every layer carries the programme's matrix, primaries and transfer tags",
        "requires": {"ffmpeg_skill_flags": {t: list(f) for t, f in LAYER_EXPORT_FLAGS.items()},
                     "otherwise": "TOOL_ERROR before anything renders (details.reason ffmpeg_skill_lacks_flag, details.flags; or ffmpeg_skill_lacks_alpha_codec "
                                  "when the checkout does not offer the codec); doctor reports capability ffmpeg-skill-feature:layer_export"},
        "result": "response.layer_export {directory, manifest {path, sha256}, alpha_codec, alpha_mode, canvas {tool, operation_id, status, output_hash, "
                  "parameters, seconds}, layers [{id, type, start, end, box, box_precision, file, path, sha256, size, duration, tool, alpha_codec, "
                  "alpha_mode, verified, alpha, seconds, tool_commands_observed}], verified}; plan: response.layer_export {directory, alpha_codec, "
                  "alpha_mode, manifest, canvas, layers [{id, type, start, end, file, tool, box, box_precision}]}",
        "verification": "ffmpeg-skill reads each layer's alpha back (its `alpha` step: an alpha plane is stored, and every frame's alpha is at or above the "
                        "canvas's) and says `verified`; this skill also probes each layer (size, length). A layer that ffmpeg-skill does not report "
                        "verified is a warning, and layer_export.verified is false",
        "box": "the element's box at rest, as reserved_regions computes it: exact for a shape and a sized image / video / layer, estimated for templates "
               "(box_precision says which); null when it could not be measured",
    }
