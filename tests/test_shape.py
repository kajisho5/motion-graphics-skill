"""The `shape` element (docs/decisions.md ADR-22): a solid-colour rectangle that takes two ffmpeg-skill calls in one stage
-- ffmpeg-skill/background generates the rectangle as a clip (a derived input with its own identity and cache entry),
ffmpeg-skill/overlay --video composites it. Unit tests for the request, the argv, the identity and the checkout
detection; integration tests render it with the real ffmpeg-skill and measure the pixels (signalstats YAVG)."""
import json
from types import SimpleNamespace

import pytest

from motion_graphics import adapter
from motion_graphics.contract import skill_contract
from motion_graphics.doctor import capability_status, element_type_status
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.model import ELEMENT_TYPES, UNSUPPORTED_ELEMENT_TYPES, parse_request
from motion_graphics.security import PathPolicy

from conftest import request_doc, shape_element, title_element
from test_integration import _executor, _luma, _luma_diff, _probe


def _argv(el_dict, clip="/abs/shape-clip.mp4", options=None):
    doc = parse_request(request_doc([el_dict], options=options))
    return Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "in.mp4", "out.mp4", {"path": clip}, None, crf=18, preset="medium",
                                                        platform=doc.options.get("platform"))[1]


def _flag(argv, name):
    return argv[argv.index(name) + 1]


# ---- the request
def test_shape_is_supported_and_nothing_is_unsupported_any_more():
    assert "shape" in ELEMENT_TYPES and UNSUPPORTED_ELEMENT_TYPES == {}
    el = parse_request(request_doc([shape_element()])).elements[0]
    assert el.parameters == {"width": 160, "height": 60, "color": "navy", "position": "center", "margin": 24, "opacity": 1.0}


@pytest.mark.parametrize("params, field", [
    ({"width": 161}, "width"),                 # background.py refuses odd sizes (4:2:0)
    ({"height": 59}, "height"),
    ({"width": 0}, "width"),
    ({"height": 7682}, "height"),
    ({"opacity": 1.5}, "opacity"),
    ({"color": "notacolour"}, "color"),
])
def test_invalid_shape_parameters_are_refused(params, field):
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([shape_element(**params)]))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["field"] == f"elements[0].parameters.{field}"


def test_shape_requires_size_and_colour():
    for missing in ("width", "height", "color"):
        el = shape_element()
        del el["parameters"][missing]
        with pytest.raises(MotionGraphicsError) as e:
            parse_request(request_doc([el]))
        assert e.value.code == "INVALID_REQUEST" and missing in e.value.details["missing"]


def test_shape_takes_no_configurable_animation():
    el = shape_element()
    el["animation"] = {"kind": "fade", "parameters": {"duration": 0.3}}
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([el]))
    assert e.value.code == "UNSUPPORTED_OPERATION"


def test_forbidden_fields_are_refused_inside_a_shape():
    el = shape_element()
    el["parameters"]["filter_complex"] = "drawbox"
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([el]))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["reason"] == "forbidden_field"


# ---- the two calls' argv
def test_the_overlay_call_composites_the_generated_clip():
    argv = _argv(shape_element(position="top-left", margin=10, opacity=0.5, start=1, end=3))
    assert _flag(argv, "--video") == "/abs/shape-clip.mp4"
    assert (_flag(argv, "--position"), _flag(argv, "--margin"), _flag(argv, "--opacity")) == ("top-left", "10", "0.5")
    assert (_flag(argv, "--start"), _flag(argv, "--end")) == ("1.000", "3.000")
    for absent in ("--scale", "--scale-percent", "--chromakey", "--fade", "--image", "--text"):
        assert absent not in argv
    assert _flag(_argv(shape_element(position={"x": 12, "y": -4})), "--position") == "12,-4"


def test_a_platform_replaces_the_default_margin_like_other_overlays():
    argv = _argv(shape_element(), options={"platform": "tiktok"})
    assert "--margin" not in argv and _flag(argv, "--platform") == "tiktok"


def test_the_background_call_asks_for_exactly_the_rectangle(tmp_path):
    runs = []

    class FakeSkill:
        def info(self):
            return SimpleNamespace(features={"generator_flags": {"background": {"encoder_quality": "--quality", "overwrite": True, "preset": True}}})

        def run_tool(self, tool, argv, timeout=None, cwd=None):
            runs.append((tool, list(argv)))
            (tmp_path / argv[argv.index("-o") + 1].split("/")[-1]).write_bytes(b"clip")
            return SimpleNamespace(seconds=0.1, commands=["ffmpeg ..."])

        def probe(self, path, timeout=None):
            return {"duration": 3.0, "video": {"width": 160, "height": 60}}

    el = parse_request(request_doc([shape_element(color="Crimson", start=1, end=3)])).elements[0]
    ex = Executor(PathPolicy(str(tmp_path)), skill=FakeSkill(), tool_versions={"ffmpeg-skill": "9.9.9"})
    clip = {"identity": ex._shape_clip_identity(el, 18, "medium"), "parameters": ex._shape_clip_parameters(el)}
    made = ex._shape_clip(el, clip, tmp_path, ".mp4", 18, "medium")
    tool, argv = runs[0]
    assert tool == "background"
    assert argv == ["-o", made["path"], "--duration", "3.000", "--width", "160", "--height", "60", "--color", "0xDC143C",
                    "--quality", "18", "--preset", "medium", "--overwrite"]
    assert made["record"]["status"] == "rendered" and made["record"]["tool"] == "ffmpeg-skill/background"
    # the second time the manifest and sha256 match: reused, no call
    again = ex._shape_clip(el, clip, tmp_path, ".mp4", 18, "medium")
    assert again["record"]["status"] == "reused" and len(runs) == 1


def test_a_clip_of_the_wrong_size_is_a_validation_error(tmp_path):
    class FakeSkill:
        def info(self):
            return SimpleNamespace(features={})

        def run_tool(self, tool, argv, timeout=None, cwd=None):
            (tmp_path / argv[argv.index("-o") + 1].split("/")[-1]).write_bytes(b"clip")
            return SimpleNamespace(seconds=0.1, commands=[])

        def probe(self, path, timeout=None):
            return {"duration": 3.0, "video": {"width": 158, "height": 60}}

    el = parse_request(request_doc([shape_element()])).elements[0]
    ex = Executor(PathPolicy(str(tmp_path)), skill=FakeSkill())
    with pytest.raises(MotionGraphicsError) as e:
        ex._shape_clip(el, {"identity": "x" * 64, "parameters": ex._shape_clip_parameters(el)}, tmp_path, ".mp4", 18, "medium")
    assert e.value.code == "VALIDATION_ERROR" and not list(tmp_path.glob("shape-*.mp4"))


# ---- identity: the clip's identity is part of the stage's, and never a path
def test_the_clip_identity_follows_the_rectangle_not_the_path():
    ex = Executor(PathPolicy("."), skill=None, tool_versions={"ffmpeg-skill": "1"})
    a = parse_request(request_doc([shape_element()])).elements[0]
    b = parse_request(request_doc([shape_element(element_id="other", position="top-left")])).elements[0]   # same rectangle, placed elsewhere
    c = parse_request(request_doc([shape_element(color="red")])).elements[0]
    assert ex._shape_clip_identity(a, 18, "medium") == ex._shape_clip_identity(b, 18, "medium")
    assert ex._shape_clip_identity(a, 18, "medium") != ex._shape_clip_identity(c, 18, "medium")
    assert ex._shape_clip_identity(a, 18, "medium") != ex._shape_clip_identity(a, 23, "medium")


# ---- the checkout: background is detected like a template
def test_check_tools_detects_the_generator():
    base = {t: {"input_schema": {"properties": {f: {} for f in flags + ("quality", "overwrite", "platform")}}} for t, flags in adapter.REQUIRED_FLAGS.items()}
    base["graphics"]["input_schema"]["properties"]["template"] = {"enum": ["title"]}
    problems, features = adapter.check_tools(base)
    assert problems == [] and features["generator_support"] == {"background": False}
    with_bg = dict(base, background={"input_schema": {"properties": {f: {} for f in adapter.GENERATOR_TOOLS["background"] + ("quality",)}}})
    problems, features = adapter.check_tools(with_bg)
    assert problems == [] and features["generator_support"] == {"background": True}
    assert features["generator_flags"]["background"] == {"encoder_quality": "--quality", "overwrite": False, "preset": False}
    assert "background" not in features["encoder_quality_flag"]
    lacking = dict(base, background={"input_schema": {"properties": {"output": {}, "duration": {}}}})
    assert adapter.check_tools(lacking)[1]["generator_support"] == {"background": False}


def test_doctor_reports_shape_unsupported_without_background():
    info = SimpleNamespace(supported=True, template_support=lambda t: True, tool_support=lambda t: False)
    caps = capability_status(info, {"_unavailable": True})
    assert caps["ffmpeg-skill-tool:background"] == "unsupported"
    st = element_type_status(caps)["shape"]
    assert st["status"] == "unsupported" and "ffmpeg-skill-tool:background" in st["missing"] and st["generator"] == "ffmpeg-skill/background"


def test_a_checkout_without_background_refuses_a_shape_before_rendering():
    info = SimpleNamespace(directory="/x", version="0.10.0", template_support=lambda t: True, tool_support=lambda t: False)
    ex = Executor(PathPolicy("."), skill=SimpleNamespace(info=lambda: info))
    with pytest.raises(MotionGraphicsError) as e:
        ex._check_checkout(parse_request(request_doc([title_element(), shape_element(start=2, end=3)])))
    assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_tool"
    assert e.value.details["tool"] == "ffmpeg-skill/background" and e.value.retryable is False
    ex._check_checkout(parse_request(request_doc([title_element()])))       # other types are unaffected


def test_the_script_allowlist_admits_background_but_stays_closed(tmp_path):
    skill = adapter.FfmpegSkill(tmp_path)
    assert skill.script("background").endswith("background.py")
    with pytest.raises(MotionGraphicsError):
        skill.script("cut")


# ---- the contract
def test_contract_declares_shape_with_its_generator():
    c = skill_contract()
    shape = {t["type"]: t for t in c["element_types"]}["shape"]
    assert (shape["tool"], shape["generator"], shape["animation"]) == ("ffmpeg-skill/overlay", "ffmpeg-skill/background", "none")
    assert shape["parameters"]["width"]["multiple_of"] == 2 and shape["parameters"]["color"]["alpha"] is False
    assert c["unsupported_element_types"] == []
    assert c["ffmpeg_skill"]["generator_tools"]["background"]["for"] == ["shape"]
    assert "ffmpeg-skill/background" in c["tools"][0]["delegates_to"]
    assert {"id": "motion_graphics.shape", "lifecycle": "EXPERIMENTAL", "tool_id": "motion-graphics/run", "element_type": "shape"} in c["provides"]


# ---- real renders (ffmpeg-skill + ffmpeg)
def _render(skill_dir, workspace, output, **params):
    resp = _executor(skill_dir, workspace).response(request_doc([shape_element(**params)], output=output))
    assert resp["ok"] is True, resp
    return resp


def test_shape_draws_a_rectangle_at_its_position_in_its_window(skill_dir, workspace):
    box = dict(width=100, height=60, color="white", position="top-left", margin=0, start=1, end=3)
    resp = _render(skill_dir, workspace, "out/shape.mp4", **box)
    # the same pipeline with an invisible rectangle (opacity 0) is the reference: whatever colour handling ffmpeg-skill/overlay
    # applies to the whole picture (it differs between FFmpeg releases) is in both, so only the rectangle itself differs
    ref = _render(skill_dir, workspace, "out/reference.mp4", **dict(box, opacity=0.0))["output"]["path"]
    out = resp["output"]["path"]
    meta = _probe(skill_dir, out)
    assert (meta["video"]["width"], meta["video"]["height"]) == (320, 180) and abs(meta["duration"] - 6.0) < 0.3
    assert _luma(out, 2.0, "80:40:10:10") > 200                           # inside the rectangle, while it is up: white
    assert _luma_diff(out, ref, 2.0, "80:40:10:10") > 30                   # ...and different from the picture without it
    assert _luma_diff(out, ref, 2.0, "60:40:200:120") < 3.0                # away from it: untouched
    assert _luma_diff(out, ref, 0.5, "80:40:10:10") < 3.0                  # before its window
    assert _luma_diff(out, ref, 4.0, "80:40:10:10") < 3.0                  # after it
    op = resp["operations"][0]
    assert op["tool"] == "ffmpeg-skill/overlay" and op["derived"][0]["tool"] == "ffmpeg-skill/background"
    assert op["derived"][0]["status"] == "rendered" and op["parameters"]["clip"] == op["derived"][0]["operation_id"]
    assert str(workspace) not in json.dumps(op["parameters"])           # the stage identity's inputs carry no path (ADR-6)


def test_shape_opacity_lets_the_video_show_through(skill_dir, workspace):
    box = dict(width=100, height=60, color="black", position="top-left", margin=0)
    ref = _render(skill_dir, workspace, "out/ref.mp4", **dict(box, opacity=0.0))["output"]["path"]
    solid = _render(skill_dir, workspace, "out/solid.mp4", **box)["output"]["path"]
    half = _render(skill_dir, workspace, "out/half.mp4", **dict(box, opacity=0.5))["output"]["path"]
    d_solid = _luma_diff(solid, ref, 1.0, "80:40:10:10")
    d_half = _luma_diff(half, ref, 1.0, "80:40:10:10")
    assert d_solid > d_half > 3.0, (d_solid, d_half)


def test_the_generated_clip_is_reused_and_a_shape_in_the_middle_of_a_timeline(skill_dir, workspace):
    doc = request_doc([shape_element(width=120, height=40, color="navy", position="bottom", start=0, end=2), title_element(start=2.5, end=4)],
                      output="out/multi.mp4")
    first = _executor(skill_dir, workspace).response(doc)
    assert first["ok"] is True and first["operations"][0]["derived"][0]["status"] == "rendered"
    assert list((workspace / ".motion-graphics").rglob("shape-*.mp4"))
    doc["output"]["overwrite"] = True
    second = _executor(skill_dir, workspace).response(doc)
    assert second["operations"][0]["status"] == "reused" and "derived" not in second["operations"][0]   # the stage itself was reused
    assert second["output"]["sha256"] == first["output"]["sha256"]
    # a one-element document: its stage is final and always re-renders (ADR-5), but its clip comes from the work dir
    single = request_doc([shape_element(width=120, height=40, color="navy", position="bottom", start=0, end=2)], output="out/single.mp4")
    assert _executor(skill_dir, workspace).response(single)["operations"][0]["derived"][0]["status"] == "rendered"
    single["output"]["overwrite"] = True
    again = _executor(skill_dir, workspace).response(single)
    assert again["operations"][0]["status"] == "rendered" and again["operations"][0]["derived"][0]["status"] == "reused"
