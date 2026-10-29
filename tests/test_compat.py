"""ffmpeg-skill compatibility by capability detection (docs/decisions.md ADR-18): no version window; the flags each
tool takes are read from the located checkout, and argv is built from what it offers. These tests drive fake
checkouts of every layout that has shipped (0.x: --crf, one _common.py, no --overwrite; 1.10-1.x: --crf and
--quality, --overwrite; 2.x: --quality only, a _common/ package) plus a contract-less one read through --help."""
import json
import textwrap
from pathlib import Path

import pytest

from motion_graphics.adapter import (FLAGS_USED, REQUIRED_FLAGS, FfmpegSkill, SkillInfo, check_tools, flags_from_help)
from motion_graphics.contract import skill_contract
from motion_graphics.doctor import capability_status
from motion_graphics.executor import Executor, _overlay_color
from motion_graphics.model import parse_request
from motion_graphics.security import PathPolicy


def _props(tool, *, quality=False, crf=True, overwrite=False, drop=()):
    flags = set(REQUIRED_FLAGS[tool]) - set(drop)
    if tool != "probe":
        if quality:
            flags.add("quality")
        if crf:
            flags.add("crf")
        if overwrite:
            flags.add("overwrite")
    return {f: {} for f in flags}


def _contract(version, *, quality=False, crf=True, overwrite=False, contract_version="1.0", drop=()):
    return {"contract_version": contract_version, "skill": {"version": version},
            "tools": [{"name": t, "input_schema": {"properties": _props(t, quality=quality, crf=crf, overwrite=overwrite,
                                                                          drop=drop if t == "overlay" else ())}}
                      for t in ("probe", "graphics", "overlay")]}


LAYOUTS = {
    "0.12": dict(quality=False, crf=True, overwrite=False),
    "1.10": dict(quality=True, crf=True, overwrite=True),
    "2.10": dict(quality=True, crf=False, overwrite=True),
}


@pytest.mark.parametrize("layout,expected_quality,expected_overwrite", [("0.12", "--crf", False), ("1.10", "--quality", True), ("2.10", "--quality", True)])
def test_check_tools_picks_the_flag_each_layout_offers(layout, expected_quality, expected_overwrite):
    tools = {t["name"]: t for t in _contract(layout, **LAYOUTS[layout])["tools"]}
    problems, features = check_tools(tools)
    assert problems == []
    assert features["encoder_quality_flag"] == {"graphics": expected_quality, "overlay": expected_quality}
    assert features["overwrite_flag"] == {"graphics": expected_overwrite, "overlay": expected_overwrite}


def test_check_tools_refuses_when_neither_quality_nor_crf_exists():
    tools = {t["name"]: t for t in _contract("9.0.0", quality=False, crf=False)["tools"]}
    problems, _ = check_tools(tools)
    assert any("quality|crf" in p for p in problems)


def test_check_tools_names_a_missing_required_flag():
    tools = {t["name"]: t for t in _contract("0.10.0", drop=("video",))["tools"]}
    problems, _ = check_tools(tools)
    assert problems == ["ffmpeg-skill/overlay lacks flag(s) ['video']"]


def test_flags_used_lists_every_alternative_and_optional_flag():
    for tool in ("graphics", "overlay"):
        assert {"quality", "crf", "overwrite"} <= set(FLAGS_USED[tool])
        assert "crf" not in REQUIRED_FLAGS[tool] and "quality" not in REQUIRED_FLAGS[tool]


# ---- fake checkouts on disk: info() end to end, without any version window

def _write_checkout(root: Path, contract=None, *, help_texts=None, common="file", package_version=None):
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    if contract is not None:
        (scripts / "_contract.py").write_text(f"import json, sys\nprint(json.dumps({contract!r}))\n", encoding="utf-8")
    for tool in ("probe", "graphics", "overlay"):
        text = (help_texts or {}).get(tool, "")
        (scripts / f"{tool}.py").write_text(f"import sys\nif '--help' in sys.argv:\n    print({text!r})\n", encoding="utf-8")
    if common == "file":
        (scripts / "_common.py").write_text("", encoding="utf-8")
    elif common == "package":
        (scripts / "_common").mkdir()
        (scripts / "_common" / "__init__.py").write_text("", encoding="utf-8")
    if package_version:
        (root / "package.json").write_text(json.dumps({"version": package_version}), encoding="utf-8")
    return root


@pytest.mark.parametrize("version", ["0.12.5", "1.0.0", "1.26.0", "2.10.3", "3.0.0", "99.1.0"])
def test_no_version_is_refused_by_number(tmp_path, version):
    layout = LAYOUTS["0.12"] if version.startswith("0.") else (LAYOUTS["1.10"] if version.startswith("1.") else LAYOUTS["2.10"])
    root = _write_checkout(tmp_path / "ffs", _contract(version, **layout), common="file" if version < "2" else "package")
    info = FfmpegSkill.locate(str(root)).info()
    assert info.supported, info.problems
    assert info.version == version
    assert info.features["source"] == "contract"
    assert info.features["common_layout"] == ("_common.py" if version < "2" else "_common/")


def test_a_different_contract_version_is_reported_not_refused(tmp_path):
    root = _write_checkout(tmp_path / "ffs", _contract("3.0.0", contract_version="2.0", **LAYOUTS["2.10"]))
    info = FfmpegSkill.locate(str(root)).info()
    assert info.supported and info.contract_version == "2.0"


def test_missing_capability_is_still_refused(tmp_path):
    root = _write_checkout(tmp_path / "ffs", _contract("2.10.3", drop=("chromakey",), **LAYOUTS["2.10"]))
    info = FfmpegSkill.locate(str(root)).info()
    assert not info.supported
    assert info.problems == ["ffmpeg-skill/overlay lacks flag(s) ['chromakey']"]


def test_contract_less_checkout_is_read_through_help(tmp_path):
    def help_for(tool):
        flags = " ".join(f"[--{f.replace('_', '-')} X]" for f in _props(tool, **LAYOUTS["0.12"]) if f not in ("inputs", "input"))
        positional = "inputs" if tool == "probe" else "input"
        return f"usage: {tool}.py {flags} {positional}\n\npositional arguments:\n  {positional}            the media\n\noptions:\n  -h, --help  show help\n"
    root = _write_checkout(tmp_path / "ffs", None, help_texts={t: help_for(t) for t in ("probe", "graphics", "overlay")}, package_version="0.12.5")
    info = FfmpegSkill.locate(str(root)).info()
    assert info.supported, info.problems
    assert info.features["source"] == "--help"
    assert info.version == "0.12.5"
    assert info.quality_flag("graphics") == "--crf" and not info.takes_overwrite("overlay")


def test_flags_from_help_reads_long_flags_and_positionals():
    text = textwrap.dedent("""\
        usage: overlay.py [-h] [-o OUTPUT] [--chromakey-similarity X] [--quality N] input

        positional arguments:
          input                 source video

        options:
          --overwrite           replace an existing output
        """)
    props = flags_from_help(text)
    assert {"input", "chromakey_similarity", "quality", "overwrite"} <= set(props)


# ---- argv follows what was detected

def _info(layout):
    tools = {t["name"]: t for t in _contract(layout, **LAYOUTS[layout])["tools"]}
    problems, features = check_tools(tools)
    return SkillInfo(Path("."), layout, "1.0", tools, problems, features)


class _InfoOnly:
    def __init__(self, layout):
        self._i = _info(layout)

    def info(self):
        return self._i


def _title():
    return parse_request({"video": {"path": "v.mp4"}, "output": {"path": "o.mp4"},
                          "elements": [{"id": "t", "type": "title", "start": 0, "end": 2, "parameters": {"title": "Hi"}}]}).elements[0]


def _text():
    return parse_request({"video": {"path": "v.mp4"}, "output": {"path": "o.mp4"},
                          "elements": [{"id": "t", "type": "text_overlay", "start": 0, "end": 2, "parameters": {"text": "Hi", "font_color": "ffd200"}}]}).elements[0]


@pytest.mark.parametrize("layout,flag,overwrite", [("0.12", "--crf", False), ("1.10", "--quality", True), ("2.10", "--quality", True)])
@pytest.mark.parametrize("make", [_title, _text])
def test_argv_uses_the_detected_quality_flag_and_overwrite(layout, flag, overwrite, make):
    ex = Executor(PathPolicy("."), skill=_InfoOnly(layout))
    _, argv, _ = ex._argv(make(), "/in.mp4", "/out.mp4", None, None, crf=21, preset="medium")
    assert argv[argv.index(flag) + 1] == "21"
    assert ({"--crf", "--quality"} - {flag}).isdisjoint(argv)
    assert ("--overwrite" in argv) is overwrite


def test_overlay_colours_get_a_0x_prefix_only_when_bare_hex():
    assert _overlay_color("00ff00") == "0x00ff00"
    assert _overlay_color("FFD200@0.5") == "0xFFD200@0.5"
    assert _overlay_color("black@0.5") == "black@0.5"
    assert _overlay_color("white") == "white"


# ---- contract and doctor say what is detected

def test_contract_states_no_version_window():
    fs = skill_contract()["ffmpeg_skill"]
    assert fs["version_window"] == {"min": None, "max_exclusive": None}
    assert "capability detection" in fs["compatibility"]
    assert fs["flag_alternatives"]["graphics"]["encoder_quality"] == ["quality", "crf"]
    assert fs["optional_flags"]["overlay"] == ["overwrite", "platform"]


def test_doctor_without_ffmpeg_skill_doctor_reports_unknown_not_unsupported():
    caps = capability_status(_info("0.12"), {"_unavailable": True})
    assert caps["ffmpeg-skill"] == "supported"
    assert caps["ffmpeg"] == "unknown" and caps["filter:drawtext"] == "unknown"


def test_real_checkout_is_detected(skill_dir):
    """Whichever ffmpeg-skill this suite runs against (CI: main and the oldest verified commit), detection agrees with
    its own contract and nothing is refused by version."""
    info = FfmpegSkill.locate(str(skill_dir)).info()
    assert info.supported, info.problems
    props = info.tools["graphics"]["input_schema"]["properties"]
    assert info.quality_flag("graphics") == ("--quality" if "quality" in props else "--crf")
    assert info.takes_overwrite("overlay") == ("overwrite" in info.tools["overlay"]["input_schema"]["properties"])
