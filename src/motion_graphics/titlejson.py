"""title.json reader (docs/decisions.md ADR-33): a `rendered_layer` element's `title_json` names the metadata file written beside
title material (the Velisa spec for title and stinger metadata, `velisa/title@1`; hyperframes-skill `render` writes it as
`<stem>.title.json`). Its `segments` are mapped onto the element's own parameters by the spec's table:

    title.json            rendered_layer
    segments.in           in_duration
    segments.out          out_duration
    segments.loop > 0     loop: true (0: false, the middle plays once)

A parameter the request states wins over the file; one it leaves out comes from the file (never the parameter's default). The
file must describe the clip it is given with: its `sha256` (when present) is the clip's, else its `file` (when present) is the
clip's file name. Its segments add up to the clip's probed length within one frame, and its `alpha` (when present) is `straight`:
ffmpeg-skill/overlay composites straight alpha, so a premultiplied clip would be drawn wrong and is refused. Nothing else in the
file is used; the fields this skill reads are listed in the contract's `title_json.reads`."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import MotionGraphicsError

TITLE_SCHEMA = "velisa/title@1"
MAX_TITLE_JSON_BYTES = 256 * 1024
SEGMENT_MAX = 60.0  # the bound of in_duration / out_duration
FILLS = ("in_duration", "out_duration", "loop")
READS = ("segments", "sha256", "file", "alpha", "schema")


def _fail(where: str, message: str, reason: str, **details: Any) -> MotionGraphicsError:
    return MotionGraphicsError("INVALID_INPUT", f"{where}: {message}", dict({"field": where, "reason": reason}, **details))


def read_title_json(path: Path, where: str) -> Any:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_TITLE_JSON_BYTES + 1)
    except OSError as e:
        raise _fail(where, f"cannot read the title.json file: {e}", "bad_title_json")
    if len(raw) > MAX_TITLE_JSON_BYTES:
        raise _fail(where, f"title.json file is larger than {MAX_TITLE_JSON_BYTES} bytes", "bad_title_json")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _fail(where, f"title.json file is not valid UTF-8 JSON: {e}", "bad_title_json")


def _seconds(value: Any, where: str, limit: Optional[float]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise _fail(where, f"must be a number of seconds >= 0: {value!r}", "bad_title_json")
    if limit is not None and value > limit:
        raise _fail(where, f"must be at most {limit:g} s (the bound of the rendered_layer parameter it maps onto): {value!r}", "bad_title_json")
    return float(value)


def map_title(doc: Any, where: str) -> Dict[str, Any]:
    """The file's segments and the facts it is checked by. No file access."""
    if not isinstance(doc, dict):
        raise _fail(where, "must be a JSON object", "bad_title_json")
    if "schema" in doc and doc["schema"] != TITLE_SCHEMA:
        raise _fail(f"{where}.schema", f"must be {TITLE_SCHEMA!r} (or absent): {doc['schema']!r}", "not_title_json", schema=doc["schema"])
    seg = doc.get("segments")
    if not isinstance(seg, dict) or not all(k in seg for k in ("in", "loop", "out")):
        raise _fail(f"{where}.segments", "must be an object {in, loop, out} in seconds", "title_json_without_segments")
    segments = {"in": _seconds(seg["in"], f"{where}.segments.in", SEGMENT_MAX),
                "loop": _seconds(seg["loop"], f"{where}.segments.loop", None),
                "out": _seconds(seg["out"], f"{where}.segments.out", SEGMENT_MAX)}
    out: Dict[str, Any] = {"schema": doc.get("schema"), "segments": segments}
    for key in ("sha256", "file"):
        if key in doc:
            if not isinstance(doc[key], str) or not doc[key]:
                raise _fail(f"{where}.{key}", "must be a non-empty string", "bad_title_json")
            out[key] = doc[key]
    if "alpha" in doc:
        if doc["alpha"] not in ("straight", "premultiplied"):
            raise _fail(f"{where}.alpha", f"must be 'straight' or 'premultiplied': {doc['alpha']!r}", "bad_title_json")
        out["alpha"] = doc["alpha"]
    return out


def resolve_title_json(policy: Any, element_id: str, parameters: Dict[str, Any], layer_path: Path, layer_sha256: str,
                       layer_duration: float, layer_fps: float) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """(parameters with in_duration / out_duration / loop filled, the record for response.title_json, warnings)."""
    from .canonical import sha256_file
    where = f"elements[{element_id}].parameters.title_json"
    path = policy.resolve_input(parameters["title_json"], where)
    mapped = map_title(read_title_json(path, where), where)
    if mapped.get("alpha") == "premultiplied":
        raise _fail(f"{where}.alpha", "the clip's alpha is premultiplied; ffmpeg-skill/overlay composites straight alpha, so it would be drawn "
                    "wrong: render the clip with straight alpha", "title_json_premultiplied_alpha")
    if "sha256" in mapped:
        if mapped["sha256"].lower() != layer_sha256:
            raise _fail(f"{where}.sha256", f"describes another file than video_path ({layer_path.name}): its sha256 does not match; a "
                        "title.json is valid only for the render it was written beside", "title_json_stale",
                        expected=mapped["sha256"], actual=layer_sha256)
        matched_by = "sha256"
    elif "file" in mapped:
        if mapped["file"] != layer_path.name:
            raise _fail(f"{where}.file", f"describes {mapped['file']!r}, not video_path ({layer_path.name!r})", "title_json_other_file",
                        file=mapped["file"])
        matched_by = "file"
    else:
        matched_by = None
    seg = mapped["segments"]
    total = seg["in"] + seg["loop"] + seg["out"]
    tolerance = (1.0 / layer_fps if layer_fps > 0 else 0.0) + 0.002
    if abs(total - layer_duration) > tolerance:
        raise _fail(f"{where}.segments", f"in + loop + out ({total:g} s) is not the clip's length ({layer_duration:g} s, within one frame): "
                    "the file does not describe this clip", "title_json_segments_mismatch", segments_total=total, layer_duration=layer_duration)
    from_file = {"in_duration": seg["in"], "out_duration": seg["out"], "loop": seg["loop"] > 0}
    params = dict(parameters)
    sources: Dict[str, str] = {}
    warnings: List[str] = []
    for name in FILLS:
        if name in parameters:
            sources[name] = "request"
            if parameters[name] != from_file[name]:
                warnings.append(f"elements[{element_id}].parameters.{name} {parameters[name]!r} is used, not the title.json value "
                                f"{from_file[name]!r} (a value the request states wins)")
        else:
            params[name] = from_file[name]
            sources[name] = "title_json"
    record = {"schema": mapped["schema"], "file": {"sha256": sha256_file(str(path)), "size": path.stat().st_size},
              "layer_matched_by": matched_by, "segments": dict(seg), **({"alpha": mapped["alpha"]} if "alpha" in mapped else {}),
              "values": {n: params[n] for n in FILLS}, "sources": sources}
    if matched_by is None:
        warnings.append(f"elements[{element_id}].parameters.title_json has neither sha256 nor file: it is checked against the clip by its "
                        "segments' length only")
    return params, record, warnings


def contract_section() -> Dict[str, Any]:
    return {
        "file_schema": TITLE_SCHEMA,
        "written_by": "hyperframes-skill render of title material: <stem>.title.json beside the clip (the Velisa spec for title and stinger metadata)",
        "request_field": "elements[].parameters.title_json on a rendered_layer: the title.json file's path (PathPolicy, at most "
                         f"{MAX_TITLE_JSON_BYTES} bytes, UTF-8 JSON)",
        "mapping": {"segments.in": "in_duration", "segments.out": "out_duration", "segments.loop > 0": "loop (true; 0 gives false)"},
        "reads": list(READS),
        "checks": {"sha256": "when present, the clip's sha256 (else title_json_stale)",
                   "file": "when present and sha256 is not, the clip's file name (else title_json_other_file)",
                   "segments": "in + loop + out is the clip's probed length within one frame (else title_json_segments_mismatch)",
                   "alpha": "when present, straight (premultiplied: title_json_premultiplied_alpha)",
                   "schema": f"when present, {TITLE_SCHEMA} (else not_title_json)"},
        "errors": "INVALID_INPUT, details.reason one of bad_title_json, not_title_json, title_json_without_segments, title_json_stale, "
                  "title_json_other_file, title_json_segments_mismatch, title_json_premultiplied_alpha",
        "precedence": "a value the request states (in_duration, out_duration, loop) wins over the file, with a warning when they differ; "
                      "a value it leaves out comes from the file, not the parameter's default",
        "result": "response.title_json {<element id>: {schema, file {sha256, size}, layer_matched_by (sha256 | file | null), segments, "
                  "alpha?, values {in_duration, out_duration, loop}, sources {<parameter>: request | title_json}}} in plan and run, "
                  "present when an element gives title_json",
        "validate": "`validate` touches no files: title_json is checked as a path, and the parameters it would fill are left out",
    }
