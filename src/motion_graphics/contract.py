"""Machine-readable Skill / Capability / Tool contract (`motion-graphics skill --json`, alias `contract --json`).
Derived from the same tables the code runs on (model.ELEMENT_TYPES, model.ANIMATION_KINDS, errors.ERROR_TABLE);
nothing is hand-maintained beside the implementation, so contract and doctor can never claim support that render
does not actually have (STEP 9)."""
from __future__ import annotations

from typing import Any, Dict, List

from . import CONTRACT_SCHEMA_VERSION, DOCTOR_SCHEMA_VERSION, PACKAGE_NAME, REQUEST_SCHEMA_VERSION, RESPONSE_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FLAGS_USED, SUPPORTED_CONTRACT_VERSION, SUPPORTED_MAX_EXCLUSIVE, SUPPORTED_MIN, TOOLS_USED
from .errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES
from .executor import DURATION_TOLERANCE, IMAGE_EXTENSIONS, WORK_DIR_NAME
from .fonts import ALLOWED_FONT_EXTENSIONS, DEFAULT_FONT_ID, FONT_REGISTRY
from .model import (ANIMATION_KINDS, ELEMENT_TYPES, FORBIDDEN_KEYS, ID_RE, MAX_DURATION, MAX_ELEMENTS, POSITION_NAMES,
                    REQUEST_SCHEMA_ID, UNSUPPORTED_ANIMATIONS, UNSUPPORTED_ELEMENT_TYPES)

CONTRACT_SCHEMA_ID = f"{SKILL_ID}/contract@{CONTRACT_SCHEMA_VERSION}"

# Cross-repository Capability ids, matching the ids assigned to this Skill in
# kajisho5/AI-video-production-OS's `docs/CAPABILITY_MATRIX.md` (as of this writing, on that
# repository's not-yet-merged `claude/ai-video-production-os-arch-*` architecture branch --
# not its main branch -- verified directly against that file's content and against the field
# shape `registry/contract.py` there actually validates, not merely against its own docs).
# `text_overlay` and `image_overlay` are both the free-form "overlay" capability there
# (`motion_graphics.overlay`, "free-form text, image/logo overlay"); `title` and
# `lower_third` each get their own built-in-template id. `bug`/`chapter`/`progress` have no id
# there at all -- that matrix predates these element types' implementation here -- so the
# `motion_graphics.<name>` ids below for them are this repository's own provisional choices,
# not a verified match; reconcile them if the OS side ever assigns different ids once merged.
CAPABILITY_IDS: Dict[str, str] = {
    "title": "motion_graphics.title_card", "lower_third": "motion_graphics.lower_third",
    "text_overlay": "motion_graphics.overlay", "image_overlay": "motion_graphics.overlay",
    "bug": "motion_graphics.bug", "chapter": "motion_graphics.chapter", "progress": "motion_graphics.progress",
}


def capability_provides() -> List[Dict[str, str]]:
    return [{"id": CAPABILITY_IDS[etype], "lifecycle": "EXPERIMENTAL", "tool_id": f"{SKILL_ID}/run", "element_type": etype}
            for etype in sorted(ELEMENT_TYPES)]


def _param_schema(ps: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in ps.items() if k in ("type", "required", "min", "max", "enum", "default", "description", "max_length")}


def element_type_specs() -> List[Dict[str, Any]]:
    out = []
    for etype, spec in ELEMENT_TYPES.items():
        out.append({
            "type": etype, "description": spec["description"], "tool": f"ffmpeg-skill/{spec['tool']}",
            "animation": spec["animation"], "parameters": {k: _param_schema(v) for k, v in spec["parameters"].items()},
            "required_capabilities": spec["required_capabilities"], "deterministic": "content_equivalent",
        })
    return out


def animation_specs() -> List[Dict[str, Any]]:
    return [{"kind": k, "description": v["description"], "parameters": {n: _param_schema(p) for n, p in v["parameters"].items()},
             "applies_to": [t for t, s in ELEMENT_TYPES.items() if s.get("animation") == "configurable"]} for k, v in ANIMATION_KINDS.items()]


def skill_contract() -> Dict[str, Any]:
    tools = [{"tool_id": f"{SKILL_ID}/run", "skill_id": SKILL_ID, "version": VERSION, "role": "execution",
              "description": "Render a typed Graphics Document (titles, lower thirds, text overlays, image/logo overlays, with fade animation) onto a "
                              "video and write a validated artifact with provenance",
              "inputs": ["request document (stdin)"], "input_type": REQUEST_SCHEMA_ID, "produces_output": True, "writes_media": True, "deterministic": True,
              "idempotency_hint": "content_equivalent; non-final intermediates reused by deterministic operation id",
              "element_types": sorted(ELEMENT_TYPES), "supports": {"dry_run": True, "timeout": True, "cancel": True, "validate": True},
              "verification": "every artifact is probed (video stream, resolution, duration, sha256); the final output is always re-validated even when intermediates were reused",
              "provenance": "OBSERVED", "mutates_input": False, "delegates_to": [f"ffmpeg-skill/{t}" for t in TOOLS_USED]}]
    return {
        "schema": CONTRACT_SCHEMA_ID, "skill_id": SKILL_ID, "id": SKILL_ID, "name": PACKAGE_NAME, "package": PACKAGE_NAME, "version": VERSION,
        "kind": "execution", "role": "motion graphics rendering (execution); not design, not decision",
        "description": "Deterministic motion-graphics rendering execution: title cards, lower thirds, free-form text overlays and image/logo overlays, "
                       "with built-in template animation (title/lower-third fade and slide) or a configurable linear fade (text/image overlays); "
                       "typed Graphics Document in, a validated video artifact with provenance out. Not an AI agent: it never chooses what to show, "
                       "when to show it, or how it should look.",
        "repository": "https://github.com/kajisho5/motion-graphics-skill",
        "not_provided": ["AI reasoning", "design decisions", "production plans", "template or content selection", "arbitrary ffmpeg filters",
                         "arbitrary shapes without a typed delegate tool", "position/scale animation (see unsupported_animations)",
                         "video editing (video-editing-skill)", "color grading (color-grading-skill)", "subtitle generation (subtitle-skill)",
                         "shell execution", "network access"],
        "tools": tools,
        "provides": capability_provides(),
        "element_types": element_type_specs(),
        "unsupported_element_types": [{"type": t, "status": "not_implemented", "reason": r} for t, r in UNSUPPORTED_ELEMENT_TYPES.items()],
        "animations": animation_specs(),
        "unsupported_animations": [{"kind": k, "status": "not_implemented", "reason": r} for k, r in UNSUPPORTED_ANIMATIONS.items()],
        "positions": {"named": list(POSITION_NAMES), "explicit": "{x: integer, y: integer} pixels; negative counts from the far edge (ffmpeg-skill/overlay semantics)"},
        "fonts": {"registry": {fid: spec["family"] for fid, spec in FONT_REGISTRY.items()}, "default_font_id": DEFAULT_FONT_ID,
                  "custom_font_file": {"allowed_extensions": list(ALLOWED_FONT_EXTENSIONS), "path_policy": True, "provenance": "font_file_hash (sha256)"},
                  "fallback_policy": "an unknown font_id or a missing font_file is MISSING_INPUT / INVALID_INPUT; this skill never substitutes a different font and reports success"},
        "output_formats": {"container": "same extension as document.output.path (ffmpeg-skill picks the codec for it)", "video_codec": "libx264", "audio_codec": "aac (passthrough of the source track's presence, not its codec)"},
        "limits": {"max_elements": MAX_ELEMENTS, "max_element_duration_seconds": MAX_DURATION},
        "timeline": {"unit": "seconds (float)", "ranges": "half-open-by-convention [start, end), start >= 0, end > start, both finite (no NaN/Infinity)",
                     "element_ids": "unique within a document (id_pattern below), duplicates are DEPENDENCY_ERROR",
                     "rendering_order": "elements are rendered in (start, id) order regardless of the order they were listed in the request (docs/decisions.md); each element's own start/end are rendered exactly as given",
                     "duration_tolerance_seconds": DURATION_TOLERANCE},
        "execution": {"mode": "local", "canonical_invocation": ["motion-graphics", "run", "-", "--json"], "stdin": REQUEST_SCHEMA_ID,
                      "stdout": f"exactly one {SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION} document", "stderr": "diagnostics only",
                      "executables": ["python3 <ffmpeg-skill>/scripts/{probe,graphics,overlay}.py (argv lists)"],
                      "executable_resolution": "ffmpeg-skill directory: --ffmpeg-skill, MOTION_GRAPHICS_FFMPEG_SKILL_DIR, VIDEO_AGENT_FFMPEG_SKILL_DIR, ~/.claude/skills/ffmpeg-skill, ./vendor/ffmpeg-skill, ../ffmpeg-skill; ffmpeg/ffprobe: PATH lookup by ffmpeg-skill",
                      "shell": False, "arbitrary_executables": False, "arbitrary_filters": False, "network": False, "input_mutation": False, "ai": False},
        "ffmpeg_skill": {"contract_version": SUPPORTED_CONTRACT_VERSION, "version_window": {"min": ".".join(map(str, SUPPORTED_MIN)), "max_exclusive": ".".join(map(str, SUPPORTED_MAX_EXCLUSIVE))},
                         "tools_used": list(TOOLS_USED), "flags_used": {k: list(v) for k, v in FLAGS_USED.items()}},
        "request": {"schema": REQUEST_SCHEMA_ID, "id_pattern": ID_RE.pattern, "forbidden_fields": sorted(FORBIDDEN_KEYS),
                    "shape": {"schema": REQUEST_SCHEMA_ID, "video": {"path": "file"}, "output": {"path": "file", "overwrite?": False},
                              "elements": [{"id": "id", "type": "title|lower_third|text_overlay|image_overlay", "start": 0, "end": 1,
                                            "parameters": {"...": "see element_types[].parameters"}, "animation?": {"kind": "fade", "parameters": {"duration": 0.5}}}],
                              "options?": {"reuse_intermediates?": True, "crf?": 18, "preset?": "medium"}}},
        "response": {"schema": f"{SKILL_ID}/response@{RESPONSE_SCHEMA_VERSION}",
                     "success": {
                         "run": {"ok": True, "status": "ok", "dry_run": False, "output": "{path, sha256, size, duration, width, height}",
                                 "timeline": "[{id, type, start, end}]", "operations": "[StageResult]", "reused": "bool", "engine": {}, "provenance": {}},
                         "plan": {"ok": True, "status": "ok", "dry_run": True, "plan": "{document_id, video, output: {path}, timeline: [element + planned tool/asset/font]}",
                                  "note": "produced by `plan`, or `run --dry-run`; writes no media; has no output/operations/provenance keys"},
                         "validate": {"ok": True, "status": "ok", "dry_run": True, "validation": "{ok: true, elements: [GraphicsElement, in render order]}",
                                      "note": "produced by `validate` only; structural check alone, no file-system access, no output/operations/plan keys"},
                     },
                     "failure": {"ok": False, "status": "error|cancelled", "error": {"code": "one of errors.codes", "message": "str", "retryable": "bool", "details": {}},
                                 "note": "the same failure shape for every command (validate/plan/run alike)"}},
        "provenance": {"per_operation": ["operation_id", "type", "tool", "status", "parameters", "input_hashes", "output_hash", "seconds", "tool_commands_observed"],
                       "per_output": ["document_id", "video (path, sha256, duration, width, height)", "assets (sha256 per element)", "fonts (font_id or font_file_hash per element)", "operations (chain)", "output_hash"],
                       "identity": "sha256 over canonical JSON of {type, parameters (asset/font paths replaced by their sha256), start, end, animation, previous stage identity, tool_versions}; no timestamps, no UUIDs, no absolute paths"},
        "work_dir": f"<workspace>/{WORK_DIR_NAME}/<document_id>/ (non-final intermediates only; the requested output is always written directly and re-validated)",
        "image_formats": {"allowed_extensions": list(IMAGE_EXTENSIONS)},
        "schema_versions": {"contract": str(CONTRACT_SCHEMA_VERSION), "request": str(REQUEST_SCHEMA_VERSION), "response": str(RESPONSE_SCHEMA_VERSION), "doctor": str(DOCTOR_SCHEMA_VERSION)},
        "errors": {"codes": list(ERROR_CODES), "exit_codes": dict(EXIT_CODES), "retryable": {c: ERROR_TABLE[c][1] for c in ERROR_CODES}, "success_exit_code": 0},
    }
