"""Environment diagnosis against the contract. Reports only what was detected; every capability carries one of
supported | unsupported | unknown, never a guess. Detection goes through ffmpeg-skill (`_contract.py doctor --json`
and `_contract.py --json --static`): this skill runs no ffmpeg of its own. Font availability is checked with
`fc-list` (fixed argv, no shell) when present; if fontconfig itself is unavailable, font status is "unknown", not
"unsupported" -- ffmpeg still accepts a font name it cannot enumerate through fc-list on some platforms."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from typing import Any, Dict, List, Optional

from . import DOCTOR_SCHEMA_VERSION, SKILL_ID, VERSION
from .adapter import FfmpegSkill
from .errors import MotionGraphicsError
from .fonts import FONT_REGISTRY
from .model import ANIMATION_KINDS, ELEMENT_TYPES, UNSUPPORTED_ANIMATIONS, UNSUPPORTED_ELEMENT_TYPES
from .security import PathPolicy

DOCTOR_SCHEMA_ID = f"{SKILL_ID}/doctor@{DOCTOR_SCHEMA_VERSION}"
CORE_CAPABILITIES = ("filter:drawtext", "filter:drawbox", "filter:overlay", "filter:color", "filter:scale", "filter:colorchannelmixer", "encoder:libx264", "encoder:aac")


def ffmpeg_skill_doctor(skill: FfmpegSkill, timeout: float = 120.0) -> Dict[str, Any]:
    argv = [sys.executable, str(skill.directory / "scripts" / "_contract.py"), "doctor", "--json"]
    code, out, err, _ = skill._popen(argv, timeout)
    try:
        doc = json.loads(out or "{}")
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["_exit_code"] = code
    return doc


def capability_status(skill_info: Optional[Any], ffdoc: Dict[str, Any]) -> Dict[str, str]:
    """capability name -> supported | unsupported | unknown."""
    status: Dict[str, str] = {}
    have_skill = skill_info is not None and skill_info.supported
    status["ffmpeg-skill"] = "supported" if have_skill else "unsupported"
    ff_ok = bool(ffdoc.get("ffmpeg")) and have_skill
    fp_ok = bool(ffdoc.get("ffprobe")) and have_skill
    status["ffmpeg"] = "supported" if ff_ok else "unsupported"
    status["ffprobe"] = "supported" if fp_ok else "unsupported"
    available = set(ffdoc.get("available") or [])
    unknown = set(ffdoc.get("unknown") or [])
    filters_unreliable = ff_ok and not any(c.startswith("filter:") for c in available)
    for cap in CORE_CAPABILITIES:
        if not ff_ok:
            status[cap] = "unsupported"
        elif cap in available:
            status[cap] = "supported"
        elif cap in unknown or (cap.startswith("filter:") and filters_unreliable):
            status[cap] = "unknown"
        else:
            status[cap] = "unsupported" if cap.startswith("encoder:") else "unknown"
    return status


def font_status() -> Dict[str, Any]:
    try:
        r = subprocess.run(["fc-list", ":family"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return {"detection": "unavailable (fc-list not found or failed)", "fonts": {fid: "unknown" for fid in FONT_REGISTRY}}
    if r.returncode != 0:
        return {"detection": "unavailable (fc-list exited non-zero)", "fonts": {fid: "unknown" for fid in FONT_REGISTRY}}
    families = {line.strip() for line in r.stdout.splitlines() if line.strip()}
    fonts: Dict[str, str] = {}
    for fid, spec in FONT_REGISTRY.items():
        fonts[fid] = "supported" if any(spec["family"] in fam for fam in families) else "unsupported"
    return {"detection": "fc-list", "fonts": fonts}


def doctor_report(ffmpeg_skill_dir: Optional[str] = None, workspace: Optional[str] = None, allowed_input: Optional[List[str]] = None) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    problems: List[str] = []
    warnings: List[str] = []
    checks["python"] = {"status": "ok", "version": platform.python_version(), "implementation": platform.python_implementation(), "platform": platform.system()}

    skill: Optional[FfmpegSkill] = None
    info = None
    ffdoc: Dict[str, Any] = {}
    try:
        skill = FfmpegSkill.locate(ffmpeg_skill_dir)
        info = skill.info()
        checks["ffmpeg_skill"] = {"status": "ok" if info.supported else "fail", "directory": str(info.directory), "version": info.version,
                                  "contract_version": info.contract_version, "tools_used": sorted(info.tools.keys() & set(("probe", "graphics", "overlay"))),
                                  "problems": info.problems}
        problems += ["ffmpeg-skill: " + p for p in info.problems]
        ffdoc = ffmpeg_skill_doctor(skill)
        checks["ffmpeg"] = {"status": "ok" if ffdoc.get("ffmpeg") else "missing", "version": ffdoc.get("ffmpeg"), "detected_by": "ffmpeg-skill doctor"}
        checks["ffprobe"] = {"status": "ok" if ffdoc.get("ffprobe") else "missing", "version": ffdoc.get("ffprobe"), "detected_by": "ffmpeg-skill doctor"}
        if not ffdoc.get("ffmpeg") or not ffdoc.get("ffprobe"):
            problems.append("ffmpeg / ffprobe not detected by ffmpeg-skill doctor")
    except MotionGraphicsError as e:
        checks["ffmpeg_skill"] = {"status": "missing", "detail": e.message, "tried": e.details.get("tried")}
        checks["ffmpeg"] = {"status": "unknown", "detail": "not checked: ffmpeg-skill missing"}
        checks["ffprobe"] = {"status": "unknown", "detail": "not checked: ffmpeg-skill missing"}
        problems.append("ffmpeg-skill: " + e.message)

    caps = capability_status(info, ffdoc)
    checks["capabilities"] = caps
    if ffdoc.get("ffmpeg") and not any(c.startswith("filter:") for c in ffdoc.get("available") or []):
        checks["filter_detection"] = {"status": "unknown", "detail": "ffmpeg-skill doctor detected no filters at all; filter capabilities are reported unknown and verified per run by output validation"}
        warnings.append("filter detection through ffmpeg-skill doctor is unreliable on this ffmpeg; filter capabilities are unknown")
    else:
        checks["filter_detection"] = {"status": "ok" if ffdoc.get("ffmpeg") else "unknown", "detail": "filters detected by ffmpeg-skill doctor" if ffdoc.get("ffmpeg") else "ffmpeg not available"}

    element_types: Dict[str, Any] = {}
    for etype, spec in ELEMENT_TYPES.items():
        need = spec["required_capabilities"]
        st = "unsupported" if any(caps.get(c) == "unsupported" for c in need) else ("unknown" if any(caps.get(c) == "unknown" for c in need) else "supported")
        element_types[etype] = {"status": st, "tool": f"ffmpeg-skill/{spec['tool']}", "required_capabilities": need,
                                "missing": [c for c in need if caps.get(c) == "unsupported"], "unknown": [c for c in need if caps.get(c) == "unknown"]}
    checks["element_types"] = element_types
    checks["unsupported_element_types"] = dict(UNSUPPORTED_ELEMENT_TYPES)
    checks["animations"] = {k: {"status": element_types.get("text_overlay", {}).get("status", "unknown")} for k in ANIMATION_KINDS}
    checks["unsupported_animations"] = dict(UNSUPPORTED_ANIMATIONS)
    checks["fonts"] = font_status()

    try:
        policy = PathPolicy(workspace, allowed_input)
        checks["path_policy"] = {"status": "ok", "workspace": str(policy.workspace),
                                 "allowed_input_roots": [str(r) for r in policy.allowed_input_roots] if policy.allowed_input_roots else None,
                                 "work_dir": str(policy.workspace / ".motion-graphics"),
                                 "input_rule": "regular files (symlinks resolved)" + (" under allowed roots" if policy.allowed_input_roots else ""),
                                 "output_rule": "inside the workspace, never the input, never an existing file unless overwrite"}
    except MotionGraphicsError as e:
        checks["path_policy"] = {"status": "fail", "detail": e.message}
        problems.append("path policy: " + e.message)

    unsupported = sorted(t for t, o in element_types.items() if o["status"] == "unsupported")
    status = "fail" if problems else ("degraded" if unsupported else "ok")
    return {"schema": DOCTOR_SCHEMA_ID, "skill": {"id": SKILL_ID, "version": VERSION}, "status": status, "checks": checks,
            "unavailable_element_types": unsupported, "problems": problems, "warnings": warnings, "secrets_shown": False}


def runtime_context(ffmpeg_skill_dir: Optional[str], timeout: float) -> Any:
    """What the executor needs: the located skill, tool versions, and capability statuses. Raises MotionGraphicsError."""
    skill = FfmpegSkill.locate(ffmpeg_skill_dir, timeout)
    info = skill.info()
    if not info.supported:
        raise MotionGraphicsError("TOOL_ERROR", "ffmpeg-skill at " + str(info.directory) + " is not usable: " + "; ".join(info.problems),
                                   {"reason": "ffmpeg_skill_incompatible", "problems": info.problems}, retryable=False)
    ffdoc = ffmpeg_skill_doctor(skill)
    versions = {"ffmpeg-skill": info.version, "ffmpeg-skill_contract": info.contract_version, "ffmpeg": ffdoc.get("ffmpeg") or "unknown", "ffprobe": ffdoc.get("ffprobe") or "unknown"}
    return skill, versions, capability_status(info, ffdoc)
