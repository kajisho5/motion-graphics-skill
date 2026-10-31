"""sticker / hook / meme element types and document.options.platform (docs/decisions.md ADR-19). Pure tests: request
validation, argv construction, checkout detection against synthetic contract/--help documents, the up-front refusal
when the located ffmpeg-skill lacks a template or flag, and the doctor/contract surface. The real-media tests are in
test_integration.py."""
from pathlib import Path

import pytest

from motion_graphics.adapter import SkillInfo, ToolRun, check_tools, flags_from_help
from motion_graphics.contract import CAPABILITY_IDS, skill_contract
from motion_graphics.doctor import capability_status, element_type_status
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.model import ELEMENT_TYPES, PLATFORM_NAMES, parse_request
from motion_graphics.security import PathPolicy

from conftest import (hook_element, meme_element, request_doc, sticker_element, text_overlay_element, title_element)

NEW_TYPES = ("sticker", "hook", "meme")


def _argv(el_dict, platform=None):
    doc = parse_request(request_doc([el_dict], options={"platform": platform} if platform else None))
    return Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "in.mp4", "out.mp4", None, None, crf=18, preset="medium", platform=doc.options["platform"])


def _flag(argv, name):
    return argv[argv.index(name) + 1]


# ---- request validation
def test_sticker_defaults_and_corner_position():
    el = parse_request(request_doc([sticker_element()])).elements[0]
    assert el.parameters == {"text": "NEW", "position": "top-right"}
    assert parse_request(request_doc([sticker_element(position="bottom-left", primary_color="FF0000")])).elements[0].parameters["position"] == "bottom-left"


@pytest.mark.parametrize("bad", ["center", "top", {"x": 1, "y": 2}])
def test_sticker_rejects_a_non_corner_position(bad):
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([sticker_element(position=bad)]))
    assert e.value.code == "INVALID_REQUEST"


def test_sticker_has_no_text_color_because_the_template_ignores_it():
    # graphics.py draws the sticker's text in the brand background colour and never reads --text-color for it
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([sticker_element(text_color="FFFFFF")]))
    assert e.value.code == "INVALID_REQUEST" and "unknown field" in e.value.message


def test_sticker_requires_text():
    d = request_doc([{"id": "s", "type": "sticker", "start": 0, "end": 2, "parameters": {}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_hook_accepts_colours_but_no_position_subtitle_or_duration():
    assert parse_request(request_doc([hook_element(text_color="FFFFFF", primary_color="FF0000")])).elements[0].parameters["title"] == "WATCH THIS"
    for extra in ({"position": "top-left"}, {"subtitle": "x"}, {"duration": 2}):
        with pytest.raises(MotionGraphicsError):
            parse_request(request_doc([hook_element(**extra)]))


def test_meme_needs_at_least_one_line():
    assert parse_request(request_doc([meme_element(top="A")])).elements[0].parameters == {"top": "A"}
    assert parse_request(request_doc([meme_element(bottom="B")])).elements[0].parameters == {"bottom": "B"}
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([meme_element()]))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["at_least_one_of"] == ["top", "bottom"]


def test_meme_has_no_colour_or_position_parameters():
    for extra in ({"text_color": "FF0000"}, {"primary_color": "FF0000"}, {"position": "top-left"}):
        with pytest.raises(MotionGraphicsError):
            parse_request(request_doc([meme_element(top="A", **extra)]))


@pytest.mark.parametrize("make", [sticker_element, hook_element, lambda: meme_element(top="A")])
def test_new_types_do_not_accept_a_configurable_animation(make):
    el = make()
    el["animation"] = {"kind": "fade", "parameters": {"duration": 0.3}}
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([el]))
    assert e.value.code == "UNSUPPORTED_OPERATION"


# ---- document.options.platform
def test_platform_option_defaults_to_none_and_accepts_every_declared_destination():
    assert parse_request(request_doc([title_element()])).options["platform"] is None
    for name in PLATFORM_NAMES:
        assert parse_request(request_doc([title_element()], options={"platform": name})).options["platform"] == name


@pytest.mark.parametrize("bad", ["yt", "ig", "x", "custom", "TikTok", "", 5, True, ["tiktok"]])
def test_platform_option_rejects_aliases_custom_and_non_strings(bad):
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([title_element()], options={"platform": bad}))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["field"] == "document.options.platform"


def test_without_platform_the_overlay_margin_default_is_materialised():
    assert parse_request(request_doc([text_overlay_element()])).elements[0].parameters["margin"] == 24


def test_with_platform_an_omitted_overlay_margin_is_left_to_the_safe_zone_but_an_explicit_one_stays():
    doc = parse_request(request_doc([text_overlay_element(element_id="a"), text_overlay_element(element_id="b", margin=40)], options={"platform": "tiktok"}))
    by_id = {e.element_id: e for e in doc.elements}
    assert "margin" not in by_id["a"].parameters
    assert by_id["b"].parameters["margin"] == 40


# ---- argv
def test_sticker_argv():
    tool, argv, cwd = _argv(sticker_element(text="LIVE NOW", position="bottom-right", primary_color="FF0000"))
    assert tool == "graphics" and cwd is None
    assert _flag(argv, "--template") == "sticker" and _flag(argv, "--text") == "LIVE NOW" and _flag(argv, "--position") == "bottom-right"
    assert _flag(argv, "--primary") == "FF0000" and "--text-color" not in argv and "--title" not in argv


def test_hook_argv_passes_the_elements_window_as_the_templates_duration():
    _, argv, _ = _argv(hook_element(start=2.0, end=7.5, title="WATCH THIS", text_color="FFFFFF"))
    assert _flag(argv, "--template") == "hook" and _flag(argv, "--title") == "WATCH THIS"
    assert _flag(argv, "--start") == "2.000" and _flag(argv, "--end") == "7.500" and _flag(argv, "--duration") == "5.500"
    assert _flag(argv, "--text-color") == "FFFFFF"


def test_meme_argv_sends_only_the_lines_given():
    _, both, _ = _argv(meme_element(top="A", bottom="B"))
    assert _flag(both, "--template") == "meme" and _flag(both, "--top") == "A" and _flag(both, "--bottom") == "B"
    _, only_top, _ = _argv(meme_element(top="A"))
    assert "--top" in only_top and "--bottom" not in only_top
    _, only_bottom, _ = _argv(meme_element(bottom="B"))
    assert "--bottom" in only_bottom and "--top" not in only_bottom


def test_no_platform_flag_unless_the_option_is_set():
    for el in (title_element(), text_overlay_element()):
        assert "--platform" not in _argv(el)[1]


def test_platform_flag_goes_to_graphics_and_overlay():
    assert _flag(_argv(title_element(), "broadcast")[1], "--platform") == "broadcast"
    assert _flag(_argv(text_overlay_element(), "tiktok")[1], "--platform") == "tiktok"


def test_overlay_sends_no_margin_under_platform_unless_given_explicitly():
    assert "--margin" not in _argv(text_overlay_element(), "tiktok")[1]
    assert _flag(_argv(text_overlay_element(margin=40), "tiktok")[1], "--margin") == "40"
    assert _flag(_argv(text_overlay_element())[1], "--margin") == "24"


# ---- detecting what the checkout can do
def _tools(graphics_props, overlay_props=None):
    base = {"input": {}, "output": {}, "template": {}, "start": {}, "end": {}, "preset": {}, "audio_stream": {}, "title": {}, "subtitle": {},
            "name": {}, "position": {}, "count_from": {}, "text_color": {}, "primary": {}, "json": {}, "quality": {}}
    ov = {"input": {}, "output": {}, "position": {}, "margin": {}, "start": {}, "end": {}, "opacity": {}, "preset": {}, "audio_stream": {}, "fade": {}, "text": {},
          "font_size": {}, "font_color": {}, "border": {}, "border_color": {}, "box": {}, "box_color": {}, "font": {}, "font_file": {}, "image": {}, "scale": {},
          "scale_percent": {}, "video": {}, "chromakey": {}, "chromakey_similarity": {}, "chromakey_blend": {}, "json": {}, "quality": {}}
    return {"probe": {"input_schema": {"properties": {"inputs": {}}}},
            "graphics": {"input_schema": {"properties": {**base, **graphics_props}}},
            "overlay": {"input_schema": {"properties": {**ov, **(overlay_props or {})}}}}


NEW_FLAGS = {"text": {}, "top": {}, "bottom": {}, "duration": {}}


def test_a_checkout_with_the_templates_and_flags_supports_them():
    problems, f = check_tools(_tools({**NEW_FLAGS, "template": {"enum": ["title", "lower-third", "sticker", "hook", "meme"]}}))
    assert problems == [] and f["template_support"] == {"sticker": True, "hook": True, "meme": True}


def test_a_checkout_whose_template_choices_lack_a_name_does_not_support_it():
    _, f = check_tools(_tools({**NEW_FLAGS, "template": {"enum": ["title", "sticker"]}}))
    assert f["template_support"] == {"sticker": True, "hook": False, "meme": False}


def test_a_checkout_without_the_templates_flags_does_not_support_them_even_if_the_choices_were_unreadable():
    _, f = check_tools(_tools({"template": {}}))
    assert f["template_support"] == {"sticker": False, "hook": False, "meme": False}


def test_unreadable_template_choices_with_the_flags_present_is_unknown_not_unsupported():
    _, f = check_tools(_tools({**NEW_FLAGS}))
    assert f["template_support"] == {"sticker": None, "hook": None, "meme": None}


def test_missing_new_templates_are_not_a_compatibility_problem():
    # an old checkout (0.12.5) stays usable for every element type that does not need them
    problems, _ = check_tools(_tools({}))
    assert problems == []


def test_platform_flag_and_choices_are_detected_per_tool():
    _, f = check_tools(_tools({"platform": {"enum": ["tiktok", "broadcast"]}}, {"platform": {}}))
    assert f["platform_flag"] == {"graphics": True, "overlay": True}
    assert f["platforms"] == {"graphics": ["tiktok", "broadcast"], "overlay": None}
    _, none = check_tools(_tools({}))
    assert none["platform_flag"] == {"graphics": False, "overlay": False}


_HELP = """usage: graphics.py [-h] [-o OUTPUT] --template
                   {lower-third,title,sticker,hook,meme}
                   [--platform {broadcast,tiktok,youtube}]
                   [--duration DURATION] [--top TOP] [--bottom BOTTOM] [--text TEXT]
                   input

options:
  --template {lower-third,title,sticker,hook,meme}
  --platform {broadcast,tiktok,youtube}
"""


def test_template_and_platform_choices_are_read_from_help_text():
    props = flags_from_help(_HELP)
    assert props["template"] == {"enum": ["lower-third", "title", "sticker", "hook", "meme"]}
    assert props["platform"] == {"enum": ["broadcast", "tiktok", "youtube"]}
    assert "duration" in props and "top" in props


# ---- the located checkout is checked before anything renders
class _Skill:
    def __init__(self, features):
        self.features = features
        self.calls = []

    def info(self):
        return SkillInfo(Path("."), "0.12.5", "1.0", {}, [], {"encoder_quality_flag": {"graphics": "--crf", "overlay": "--crf"},
                                                              "overwrite_flag": {"graphics": False, "overlay": False}, **self.features})

    def probe(self, path, timeout=None):
        return {"video": {"width": 4, "height": 4}, "duration": 3.0, "audio_streams": []}

    def run_tool(self, tool, args, timeout=None, cwd=None):
        self.calls.append((tool, list(args)))
        Path(args[args.index("-o") + 1]).write_bytes(b"placeholder")
        return ToolRun(tool, list(args), 0, {"status": "completed"}, "", 0.01, [])


def _response(tmp_path, skill, doc_elements, **kw):
    (tmp_path / "video.mp4").write_bytes(b"placeholder")
    return Executor(PathPolicy(str(tmp_path)), skill).response(request_doc(doc_elements, output="out.mp4", **kw))


@pytest.mark.parametrize("make,template", [(sticker_element, "sticker"), (hook_element, "hook"), (lambda: meme_element(top="A"), "meme")])
def test_a_checkout_lacking_the_template_refuses_the_element_before_rendering(tmp_path, make, template):
    skill = _Skill({"template_support": {template: False}})
    with pytest.raises(MotionGraphicsError) as e:
        _response(tmp_path, skill, [title_element(element_id="a_first", start=0, end=1), make()])
    assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_template" and e.value.details["template"] == template
    assert skill.calls == []  # not even the earlier, renderable title stage ran


def test_an_undetermined_template_is_attempted_not_refused(tmp_path):
    skill = _Skill({"template_support": {"sticker": None}})
    assert _response(tmp_path, skill, [sticker_element()])["ok"] is True
    assert skill.calls and _flag(skill.calls[0][1], "--template") == "sticker"


def test_platform_without_the_flag_is_refused_before_rendering(tmp_path):
    skill = _Skill({"platform_flag": {"graphics": False, "overlay": False}})
    with pytest.raises(MotionGraphicsError) as e:
        _response(tmp_path, skill, [title_element()], options={"platform": "tiktok"})
    assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_platform_flag" and skill.calls == []


def test_platform_the_checkout_does_not_know_is_refused(tmp_path):
    skill = _Skill({"platform_flag": {"graphics": True}, "platforms": {"graphics": ["youtube"]}})
    with pytest.raises(MotionGraphicsError) as e:
        _response(tmp_path, skill, [title_element()], options={"platform": "broadcast"})
    assert e.value.details["reason"] == "ffmpeg_skill_lacks_platform" and e.value.details["known"] == ["youtube"] and skill.calls == []


def test_platform_is_sent_to_every_stage_and_changes_the_stage_identity(tmp_path):
    flags = {"platform_flag": {"graphics": True, "overlay": True}, "platforms": {"graphics": ["broadcast"], "overlay": None}}
    with_platform = _Skill(flags)
    r1 = _response(tmp_path, with_platform, [title_element(), text_overlay_element(start=0, end=2)], options={"platform": "broadcast"})
    assert [_flag(args, "--platform") for _, args in with_platform.calls] == ["broadcast", "broadcast"]
    without = _Skill({})
    r2 = _response(tmp_path, without, [title_element(), text_overlay_element(start=0, end=2)], overwrite=True)
    assert all("--platform" not in args for _, args in without.calls)
    assert r1["operations"][0]["operation_id"] != r2["operations"][0]["operation_id"]


def test_plan_applies_the_same_checkout_check(tmp_path):
    (tmp_path / "video.mp4").write_bytes(b"placeholder")
    ex = Executor(PathPolicy(str(tmp_path)), _Skill({"template_support": {"hook": False}}), dry_run=True)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([hook_element()], output="out.mp4"))
    assert e.value.details["reason"] == "ffmpeg_skill_lacks_template"


# ---- doctor and contract
class _Info:
    supported = True

    def __init__(self, support):
        self.support = support

    def template_support(self, template):
        return self.support.get(template)

    def tool_support(self, tool):
        return True


def _caps(support):
    return capability_status(_Info(support), {"ffmpeg": "6", "ffprobe": "6", "available": ["filter:drawtext", "filter:drawbox", "filter:overlay", "filter:color", "encoder:libx264"],
                                              "missing": [], "unknown": []})


def test_doctor_reports_an_element_type_unsupported_when_its_template_is_missing():
    types = element_type_status(_caps({"sticker": False, "hook": True, "meme": None}))
    assert types["sticker"]["status"] == "unsupported" and types["sticker"]["missing"] == ["graphics-template:sticker"]
    assert types["hook"]["status"] == "supported"
    assert types["meme"]["status"] == "unknown" and types["meme"]["unknown"] == ["graphics-template:meme"]
    assert types["title"]["status"] == "supported"


def test_doctor_reports_the_templates_unsupported_without_a_usable_checkout():
    caps = capability_status(None, {})
    assert all(caps[f"graphics-template:{t}"] == "unsupported" for t in NEW_TYPES)


def test_every_template_capability_an_element_type_needs_is_one_doctor_reports():
    caps = _caps({t: True for t in NEW_TYPES})
    for etype in NEW_TYPES:
        needs = [c for c in ELEMENT_TYPES[etype]["required_capabilities"] if c.startswith("graphics-template:")]
        assert needs == [f"graphics-template:{etype}"] and all(c in caps for c in needs)


def test_contract_declares_the_new_types_platforms_and_constraints():
    c = skill_contract()
    specs = {t["type"]: t for t in c["element_types"]}
    assert set(NEW_TYPES) <= set(specs) and specs["sticker"]["animation"] == "builtin_pop" and specs["hook"]["animation"] == "none" and specs["meme"]["animation"] == "none"
    assert specs["meme"]["constraints"] == [{"at_least_one_of": ["top", "bottom"]}] and "constraints" not in specs["sticker"]
    assert {p["element_type"] for p in c["provides"]} >= set(NEW_TYPES)
    assert all(CAPABILITY_IDS[t] == f"motion_graphics.{t}" for t in NEW_TYPES)
    assert c["platforms"]["names"] == list(PLATFORM_NAMES) and c["platforms"]["request_field"] == "options.platform"
    assert c["request"]["shape"]["options?"]["platform?"] in PLATFORM_NAMES
    assert c["ffmpeg_skill"]["template_requirements"] == {"sticker": {"graphics_flags": ["text"]}, "hook": {"graphics_flags": ["duration"]}, "meme": {"graphics_flags": ["top", "bottom"]}}
    assert {"text", "top", "bottom", "duration", "platform"} <= set(c["ffmpeg_skill"]["flags_used"]["graphics"])
