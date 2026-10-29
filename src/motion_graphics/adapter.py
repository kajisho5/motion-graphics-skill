"""ffmpeg-skill adapter: the only place that starts a process.

- Locates an ffmpeg-skill checkout (explicit dir > MOTION_GRAPHICS_FFMPEG_SKILL_DIR > VIDEO_AGENT_FFMPEG_SKILL_DIR >
  ~/.claude/skills/ffmpeg-skill > ./vendor/ffmpeg-skill > ../ffmpeg-skill) and detects what it can do: the flags each
  tool takes are read from its contract (`scripts/_contract.py --json --static`, falling back to `scripts/contract.py`
  and finally to each tool's own `--help`). There is no version window: a checkout is usable when the flags this
  skill emits are present, whatever its version number (docs/decisions.md ADR-18).
- Runs one named tool as  [sys.executable, <dir>/scripts/<tool>.py, <typed argv...>, --json]  with a minimal
  environment, in its own process group, with a timeout; never a shell, never a request-supplied string as a flag.
- Every argv value is produced here from validated numbers / resolved paths / the closed font and position
  vocabularies in model.py and fonts.py; paths are absolute so they can never be parsed as options (ffmpeg-skill
  places the input as a positional argument).
- Parses the tool's JSON document ({"status": "completed"|"failed", ...}); exit code != 0 or status failed ->
  TOOL_ERROR.

Which ffmpeg-skill tools are used, and for what (docs/ffmpeg-skill.md):
  probe     input facts (resolution, duration, streams) and output validation
  graphics  the title, lower-third, bug, chapter, progress, countdown, sticker, hook and meme templates (built-in animation,
            brand-free: colours passed explicitly); sticker/hook/meme only when the checkout has them (TEMPLATE_FLAGS)
  overlay   TEXT_OVERLAY (--text), IMAGE_OVERLAY (--image) and VIDEO_OVERLAY (--video, with optional
            --chromakey/--chromakey-similarity/--chromakey-blend), with position, margin, static scale, opacity
            and (text/image only) a configurable linear --fade in/out
Every graphics/overlay invocation may also carry --audio-stream (document.options.audio_stream, ffmpeg-skill
0.12.0) to select which audio track of the source survives, and --platform (document.options.platform) to put each element's
margin in a destination's safe zone, when the checkout has that flag."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import MotionGraphicsError

# contract_version every ffmpeg-skill release so far declares (0.x, 1.x and 2.x alike); reported, not gated on -- a
# different value only matters if the flags below can no longer be read from it, and that is detected directly
KNOWN_CONTRACT_VERSION = "1.0"
ENV_DIR_KEYS = ("MOTION_GRAPHICS_FFMPEG_SKILL_DIR", "VIDEO_AGENT_FFMPEG_SKILL_DIR")
TOOLS_USED = ("probe", "graphics", "overlay")
# argparse dests of the ffmpeg-skill tools this adapter emits; every one must be present in the located checkout
# (detected from its contract or --help, never inferred from a version number). Together they imply ffmpeg-skill
# >= 0.12.0 (--video/--chromakey* on overlay: 0.11.0, --audio-stream on graphics/overlay: 0.12.0).
# dropped_non_av_streams (0.12.1) is a response field, not a flag: it is read when present and left out of the result
# when absent (adapter.ToolRun, never guessed as false), so it needs no gate of its own.
REQUIRED_FLAGS: Dict[str, Tuple[str, ...]] = {
    "probe": ("inputs",),
    "graphics": ("input", "output", "template", "start", "end", "preset", "audio_stream", "title", "subtitle",
                 "name", "position", "count_from", "text_color", "primary", "json"),
    "overlay": ("input", "output", "position", "margin", "start", "end", "opacity", "preset", "audio_stream",
                "fade", "text", "font_size", "font_color", "border", "border_color", "box", "box_color", "font",
                "font_file", "image", "scale", "scale_percent", "video", "chromakey", "chromakey_similarity",
                "chromakey_blend", "json"),
}
# one of each group must be present; the first the checkout offers is used. ffmpeg-skill 1.10 added --quality (the
# same CRF scale and default) and 2.0 removed --crf from graphics/overlay, so a 0.x checkout has only --crf and a 2.x
# only --quality.
FLAG_ALTERNATIVES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "graphics": {"encoder_quality": ("quality", "crf")},
    "overlay": {"encoder_quality": ("quality", "crf")},
}
# emitted only when the checkout offers it: ffmpeg-skill 1.10+ refuses to replace an existing output without
# --overwrite, and older releases reject the flag. This skill's own output policy (document.output.overwrite,
# checked before anything renders) is the consent; the flag only passes it on.
# `platform` (ffmpeg-skill/graphics and /overlay --platform: margins become a destination's safe zone) is emitted only
# when document.options.platform is set, and refused up front when the checkout has no such flag.
OPTIONAL_FLAGS: Dict[str, Tuple[str, ...]] = {"graphics": ("overwrite", "platform"), "overlay": ("overwrite", "platform")}
# graphics templates the oldest verified ffmpeg-skill (0.12.5) does not have. An element type built on one needs the
# name in the checkout's `--template` choices AND the flags listed here; the element type is refused up front when the
# checkout lacks either (never attempted and left to fail inside the tool), and doctor reports it unsupported.
TEMPLATE_FLAGS: Dict[str, Tuple[str, ...]] = {"sticker": ("text",), "hook": ("duration",), "meme": ("top", "bottom")}
# every flag this adapter may emit, for the contract document (`ffmpeg_skill.flags_used`)
FLAGS_USED: Dict[str, Tuple[str, ...]] = {
    tool: flags + tuple(f for alts in FLAG_ALTERNATIVES.get(tool, {}).values() for f in alts) + OPTIONAL_FLAGS.get(tool, ())
    + (tuple(sorted({f for fl in TEMPLATE_FLAGS.values() for f in fl})) if tool == "graphics" else ())
    for tool, flags in REQUIRED_FLAGS.items()
}
_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TERM",
             "SYSTEMROOT", "SYSTEMDRIVE", "PATHEXT", "COMSPEC", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA")
_HELP_FLAG_RE = re.compile(r"(?<![\w-])--([a-z][a-z0-9-]*)")


def _clean_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _group_kwargs() -> Dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


def kill_tree(proc: "subprocess.Popen[str]") -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.communicate(timeout=5)
    except Exception:
        pass


def fmt_seconds(value: float) -> str:
    """Seconds as a plain decimal string with millisecond precision (ffmpeg-skill parse_time accepts it)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")) or value < 0:
        raise MotionGraphicsError("INTERNAL_ERROR", f"bad time value {value!r}")
    return f"{float(value):.3f}"


def fmt_number(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise MotionGraphicsError("INTERNAL_ERROR", f"bad numeric value {value!r}")
    return f"{float(value):g}"


@dataclass
class ToolRun:
    tool: str
    argv: List[str]
    returncode: int
    data: Dict[str, Any]
    stderr_tail: str
    seconds: float
    commands: List[str] = field(default_factory=list)
    # ffmpeg-skill 0.12.1: true only when graphics/overlay's own subtitle/data-stream preservation attempt failed
    # and it fell back to a video+audio-only re-encode, silently dropping the source's subtitle/data stream(s).
    # None when the tool's response never carries the field at all (e.g. probe, or an older ffmpeg-skill).
    dropped_non_av_streams: Optional[bool] = None


@dataclass
class SkillInfo:
    directory: Path
    version: str
    contract_version: str
    tools: Dict[str, Dict[str, Any]]
    problems: List[str]
    # what was detected, and how: {"source": "contract"|"contract (with detection)"|"contract.py"|"--help"|"none",
    # "encoder_quality_flag": {"graphics": "--quality"|"--crf", ...}, "overwrite_flag": {"graphics": bool, ...},
    # "common_layout": "_common.py"|"_common/"|None, "removed": [...]}; doctor prints it verbatim
    features: Dict[str, Any] = field(default_factory=dict)

    @property
    def supported(self) -> bool:
        return not self.problems

    def quality_flag(self, tool: str) -> str:
        """The x264 CRF flag of ffmpeg-skill/<tool> in this checkout (`--quality` from 1.10, `--crf` before 2.0)."""
        return (self.features.get("encoder_quality_flag") or {}).get(tool) or "--crf"

    def takes_overwrite(self, tool: str) -> bool:
        return bool((self.features.get("overwrite_flag") or {}).get(tool))

    def takes_platform(self, tool: str) -> bool:
        return bool((self.features.get("platform_flag") or {}).get(tool))

    def platform_names(self, tool: str) -> Optional[List[str]]:
        """The destinations ffmpeg-skill/<tool> --platform accepts in this checkout, or None when its choices could not be read."""
        names = (self.features.get("platforms") or {}).get(tool)
        return list(names) if isinstance(names, list) else None

    def template_support(self, template: str) -> Optional[bool]:
        """Whether this checkout's graphics tool can render `template`: True / False when detected, None when it cannot be
        told (e.g. a contract that does not list the `--template` choices)."""
        return (self.features.get("template_support") or {}).get(template)


def _common_layout(directory: Path) -> Optional[str]:
    """ffmpeg-skill's shared helpers: one `scripts/_common.py` before 2.0, a `scripts/_common/` package from 2.0 on.
    Reported by doctor only; this skill never imports either."""
    if (directory / "scripts" / "_common.py").is_file():
        return "_common.py"
    if (directory / "scripts" / "_common" / "__init__.py").is_file():
        return "_common/"
    return None


def flags_from_help(text: str) -> Dict[str, Any]:
    """argparse dests readable from a tool's `--help`, for a checkout whose contract cannot be read: every `--long-flag`
    (dashes -> underscores) and every name listed under "positional arguments". The same keys a contract's
    input_schema.properties carries, so check_tools() works on either."""
    props: Dict[str, Any] = {m.group(1).replace("-", "_"): {} for m in _HELP_FLAG_RE.finditer(text)}
    for flag in ("template", "platform"):  # argparse prints the choices as `--flag {a,b,c}`; a contract carries them as `enum`
        m = re.search(rf"--{flag}\s+\{{([^}}\s]+)\}}", text)
        if m and flag in props:
            props[flag] = {"enum": m.group(1).split(",")}
    in_positional = False
    for line in text.splitlines():
        if line.strip().lower().startswith("positional arguments"):
            in_positional = True
            continue
        if in_positional:
            if line and not line[0].isspace():
                in_positional = False
                continue
            words = line.split()
            if words and line.startswith("  ") and not line.startswith("        ") and re.match(r"^[a-z_][a-z0-9_]*$", words[0]):
                props[words[0]] = {}
    return props


def check_tools(tools: Dict[str, Dict[str, Any]]) -> Tuple[List[str], Dict[str, Any]]:
    """Compare the flags this skill emits with what the located checkout declares. Returns (problems, features); an
    empty problem list means every element type can be rendered. Pure: unit-testable against any contract document."""
    problems: List[str] = []
    quality: Dict[str, Optional[str]] = {}
    overwrite: Dict[str, bool] = {}
    platform_flag: Dict[str, bool] = {}
    platforms: Dict[str, Optional[List[str]]] = {}
    template_support: Dict[str, Optional[bool]] = {}
    for tool, flags in REQUIRED_FLAGS.items():
        spec = tools.get(tool)
        if spec is None:
            problems.append(f"ffmpeg-skill tool {tool!r} is missing")
            continue
        props = (spec.get("input_schema") or {}).get("properties") or {}
        missing = [f for f in flags if f not in props]
        for group, alternatives in FLAG_ALTERNATIVES.get(tool, {}).items():
            found = next((f for f in alternatives if f in props), None)
            if group == "encoder_quality":
                quality[tool] = "--" + found.replace("_", "-") if found else None
            if found is None:
                missing.append("|".join(alternatives))
        if "overwrite" in OPTIONAL_FLAGS.get(tool, ()):
            overwrite[tool] = "overwrite" in props
        if "platform" in OPTIONAL_FLAGS.get(tool, ()):
            platform_flag[tool] = "platform" in props
            choices = (props.get("platform") or {}).get("enum")
            platforms[tool] = list(choices) if isinstance(choices, list) else None
        if tool == "graphics":
            template_choices = (props.get("template") or {}).get("enum")
            for name, needed in TEMPLATE_FLAGS.items():
                if any(f not in props for f in needed):
                    template_support[name] = False
                elif isinstance(template_choices, list):
                    template_support[name] = name in template_choices
                else:
                    template_support[name] = None
        if missing:
            problems.append(f"ffmpeg-skill/{tool} lacks flag(s) {missing}")
    return problems, {"encoder_quality_flag": quality, "overwrite_flag": overwrite, "platform_flag": platform_flag, "platforms": platforms,
                      "template_support": template_support}


class FfmpegSkill:
    """Handle on one ffmpeg-skill checkout."""

    def __init__(self, directory: Path, timeout: float = 600.0):
        self.directory = directory
        self.timeout = timeout
        self._info: Optional[SkillInfo] = None
        self.runs: List[ToolRun] = []
        self._cancelled = False

    # ---- discovery
    @staticmethod
    def candidates(explicit: Optional[str] = None) -> List[Path]:
        out: List[Path] = []
        if explicit:
            return [Path(explicit)]  # an explicit directory is never silently replaced by a fallback
        for key in ENV_DIR_KEYS:
            v = os.environ.get(key)
            if v:
                out.append(Path(v))
        out.append(Path.home() / ".claude" / "skills" / "ffmpeg-skill")
        out.append(Path.cwd() / "vendor" / "ffmpeg-skill")
        out.append(Path.cwd().parent / "ffmpeg-skill")
        return out

    @classmethod
    def locate(cls, explicit: Optional[str] = None, timeout: float = 600.0) -> "FfmpegSkill":
        tried = []
        for c in cls.candidates(explicit):
            tried.append(str(c))
            if all((c / "scripts" / f"{t}.py").is_file() for t in TOOLS_USED):
                return cls(c.resolve(), timeout)
        raise MotionGraphicsError("TOOL_ERROR", "ffmpeg-skill not found (need scripts/{probe,graphics,overlay}.py)",
                                   {"reason": "ffmpeg_skill_missing", "tried": tried}, retryable=False)

    def script(self, tool: str) -> str:
        if tool not in TOOLS_USED:
            raise MotionGraphicsError("INTERNAL_ERROR", f"tool {tool!r} is not on the adapter allowlist")
        return str(self.directory / "scripts" / f"{tool}.py")

    # ---- what the located skill can do
    def _read_contract(self, timeout: float) -> Tuple[Optional[Dict[str, Any]], str, List[str]]:
        """The first contract document one of ffmpeg-skill's contract entry points prints, newest first."""
        errors: List[str] = []
        scripts = self.directory / "scripts"
        attempts = [("contract", scripts / "_contract.py", ["--json", "--static"]),
                    ("contract (with detection)", scripts / "_contract.py", ["--json"]),
                    ("contract.py", scripts / "contract.py", ["--json"])]
        for label, path, extra in attempts:
            if not path.is_file():
                continue
            try:
                code, out, _, _ = self._popen([sys.executable, str(path), *extra], timeout)
                doc = json.loads(out or "{}")
            except (MotionGraphicsError, ValueError, OSError) as e:
                errors.append(f"{path.name} {' '.join(extra)}: {getattr(e, 'message', str(e))}")
                continue
            if code == 0 and isinstance(doc, dict) and isinstance(doc.get("tools"), list):
                return doc, label, errors
            errors.append(f"{path.name} {' '.join(extra)}: exit {code}, no tools list")
        return None, "", errors

    def _tools_from_help(self, timeout: float) -> Dict[str, Dict[str, Any]]:
        tools: Dict[str, Dict[str, Any]] = {}
        for tool in TOOLS_USED:
            try:
                code, out, _, _ = self._popen([sys.executable, self.script(tool), "--help"], timeout)
            except (MotionGraphicsError, OSError):
                continue
            if code == 0 and out.strip():
                tools[tool] = {"name": tool, "input_schema": {"properties": flags_from_help(out)}}
        return tools

    def info(self, timeout: float = 60.0) -> SkillInfo:
        if self._info is not None:
            return self._info
        problems: List[str] = []
        version, contract_version = "unknown", "unknown"
        tools: Dict[str, Dict[str, Any]] = {}
        features: Dict[str, Any] = {}
        doc, source, errors = self._read_contract(timeout)
        if doc is not None:
            version = str((doc.get("skill") or {}).get("version", "unknown"))
            contract_version = str(doc.get("contract_version", "unknown"))
            tools = {t["name"]: t for t in doc.get("tools", []) if isinstance(t, dict) and "name" in t}
        if not tools:
            tools = self._tools_from_help(timeout)
            source = "--help" if tools else "none"
            if version == "unknown":
                version = _package_version(self.directory)
        if not tools:
            problems.append("cannot read ffmpeg-skill's tool flags (no contract, no --help): " + ("; ".join(errors) or "nothing to run"))
        else:
            tool_problems, features = check_tools(tools)
            problems += tool_problems
        features.update({"source": source, "common_layout": _common_layout(self.directory),
                         "removed": [r.get("what") for r in (doc or {}).get("removed") or [] if isinstance(r, dict)]})
        self._info = SkillInfo(self.directory, version, contract_version, tools, problems, features)
        return self._info

    # ---- execution
    def cancel(self) -> None:
        self._cancelled = True

    def _popen(self, argv: Sequence[str], timeout: Optional[float], cwd: Optional[str] = None) -> Tuple[int, str, str, float]:
        for a in argv:
            if not isinstance(a, str) or "\x00" in a:
                raise MotionGraphicsError("INTERNAL_ERROR", "argv element is not a clean string")
        if self._cancelled:
            raise MotionGraphicsError("CANCELLED", "cancelled before the tool started")
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, errors="replace", env=_clean_env(), cwd=cwd or str(self.directory), **_group_kwargs())
        except FileNotFoundError as e:
            raise MotionGraphicsError("TOOL_ERROR", f"cannot start {os.path.basename(argv[0])}: {e}", {"reason": "executable_missing"}, retryable=False)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise MotionGraphicsError("TOOL_ERROR", f"{os.path.basename(argv[1]) if len(argv) > 1 else argv[0]} exceeded {timeout}s", {"reason": "timeout", "timeout": timeout})
        except KeyboardInterrupt:
            kill_tree(proc)
            self._cancelled = True
            raise MotionGraphicsError("CANCELLED", "interrupted while a tool was running", {"reason": "signal"})
        return proc.returncode, out or "", err or "", round(time.monotonic() - t0, 3)

    def run_tool(self, tool: str, args: Sequence[str], timeout: Optional[float] = None, cwd: Optional[str] = None) -> ToolRun:
        argv = [sys.executable, self.script(tool), *args, "--json"]
        code, out, err, seconds = self._popen(argv, timeout or self.timeout, cwd)
        data = _parse_json(out)
        tail = "\n".join(err.strip().splitlines()[-12:])
        dropped = data.get("dropped_non_av_streams") if isinstance(data, dict) else None
        run = ToolRun(tool, argv, code, data, tail, seconds, list(data.get("commands", [])) if isinstance(data.get("commands"), list) else [],
                      dropped if isinstance(dropped, bool) else None)
        self.runs.append(run)
        if code != 0 or (isinstance(data, dict) and data.get("status") == "failed"):
            msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
            raise MotionGraphicsError("TOOL_ERROR", f"ffmpeg-skill/{tool} failed (exit {code}): {msg or tail or 'no message'}",
                                       {"reason": "tool_failed", "tool": f"ffmpeg-skill/{tool}", "exit_code": code, "stderr_tail": tail,
                                        "error_kind": (data.get("error") or {}).get("kind") if isinstance(data.get("error"), dict) else None})
        return run

    def probe(self, path: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """ffmpeg-skill/probe prints its document directly (no status envelope)."""
        argv = [sys.executable, self.script("probe"), path]
        code, out, err, seconds = self._popen(argv, timeout or min(self.timeout, 120.0))
        data = _parse_json(out)
        self.runs.append(ToolRun("probe", argv, code, data, "\n".join(err.strip().splitlines()[-12:]), seconds))
        if code != 0 or not isinstance(data, dict) or "duration" not in data:
            raise MotionGraphicsError("INVALID_INPUT", f"ffmpeg-skill/probe could not read {os.path.basename(path)}: {err.strip().splitlines()[-1] if err.strip() else 'no output'}",
                                       {"reason": "unreadable_media", "path": path, "exit_code": code})
        return data


def _package_version(directory: Path) -> str:
    """ffmpeg-skill's version from package.json, for a checkout whose contract cannot be read; informational only."""
    try:
        return str(json.loads((directory / "package.json").read_text(encoding="utf-8")).get("version", "unknown"))
    except (OSError, ValueError, AttributeError):
        return "unknown"


def _parse_json(text: str) -> Dict[str, Any]:
    """ffmpeg-skill prints one JSON document on stdout under --json; tolerate a preceding plain line."""
    text = text.strip()
    if not text:
        return {}
    start = text.find("{")
    if start < 0:
        return {}
    try:
        doc = json.loads(text[start:])
    except ValueError:
        try:
            doc = json.loads(text.splitlines()[-1])
        except ValueError:
            return {}
    return doc if isinstance(doc, dict) else {}
