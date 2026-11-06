"""Named style presets and the brand kit (docs/decisions.md ADR-24): typed bundles of parameters the element types already
have. Unit tests for the table, the request, the precedence and the argv; one real render of a styled document."""
import pytest

from motion_graphics.colors import NAMED_COLORS
from motion_graphics.contract import skill_contract
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.fonts import FONT_REGISTRY
from motion_graphics.model import ELEMENT_TYPES, parse_request
from motion_graphics.security import PathPolicy
from motion_graphics.styles import STYLE_PRESETS, STYLE_TARGETS

from conftest import (bug_element, chapter_element, countdown_element, hook_element, image_overlay_element, meme_element, progress_element, request_doc,
                      shape_element, sticker_element, text_overlay_element, title_element, video_overlay_element)
from test_integration import _executor, _probe



def lower_third_element(element_id="lt1", start=0.0, end=3.0, name="Ada Lovelace", **extra):
    return {"id": element_id, "type": "lower_third", "start": start, "end": end, "parameters": {"name": name, **extra}}


def every_element():
    """One element of each of the 13 types, each 3 s long (long enough for every preset's fade)."""
    return [title_element(end=3.0), lower_third_element(), text_overlay_element(end=3.0), image_overlay_element(end=3.0),
            video_overlay_element(end=3.0), bug_element(end=3.0), chapter_element(end=3.0), progress_element(end=3.0),
            countdown_element(end=3.0), sticker_element(), hook_element(), meme_element(top="x"), shape_element(end=3.0)]


def _argv(el, options=None):
    doc = parse_request(request_doc([el], options=options))
    e = doc.elements[0]
    asset = {"path": "/abs/asset"} if e.type in ("image_overlay", "video_overlay", "shape") else None
    return Executor(PathPolicy("."), skill=None)._argv(e, "in.mp4", "out.mp4", asset, None, crf=18, preset="medium",
                                                        platform=doc.options.get("platform"))[1]


def _flag(argv, name):
    return argv[argv.index(name) + 1]


# ---- the table
def test_the_six_presets_exist_and_every_element_type_has_a_target_map():
    assert sorted(STYLE_PRESETS) == ["corporate", "minimal", "neon", "news", "swiss", "wafu"]
    assert set(STYLE_TARGETS) == set(ELEMENT_TYPES)


def test_every_target_is_an_optional_parameter_of_that_type():
    for etype, targets in STYLE_TARGETS.items():
        for param, source in targets.items():
            if param == "animation":
                assert ELEMENT_TYPES[etype]["animation"] == "configurable", etype
                continue
            spec = ELEMENT_TYPES[etype]["parameters"][param]
            assert not spec.get("required"), (etype, param)
            if source.startswith("role:") or source == "box_color":
                assert spec["type"] == "color", (etype, param)


@pytest.mark.parametrize("name", sorted(STYLE_PRESETS))
def test_each_preset_is_a_contract_entry_with_typed_values(name):
    section = skill_contract()["style_presets"]
    entry = next(p for p in section["presets"] if p["name"] == name)
    spec = STYLE_PRESETS[name]
    assert entry["roles"] == spec["roles"] and set(entry["roles"]) == set(section["roles"])
    assert all(len(c) == 6 and int(c, 16) >= 0 for c in entry["roles"].values())
    assert entry["font_id"] in FONT_REGISTRY
    assert isinstance(entry["box"], bool) and 0.0 <= entry["box_opacity"] <= 1.0
    assert 0 <= entry["border_width"] <= 20 and 0 <= entry["margin"] <= 2000
    assert entry["fade"] is None or 0.01 <= entry["fade"] <= 30.0
    assert entry["description"]
    assert name in section["names"] and section["targets"] == STYLE_TARGETS


@pytest.mark.parametrize("name", sorted(STYLE_PRESETS))
def test_each_preset_resolves_for_all_13_element_types(name):
    spec = STYLE_PRESETS[name]
    roles = spec["roles"]
    doc = parse_request(request_doc(every_element(), options={"style": name}))
    by_type = {e.type: e for e in doc.elements}
    assert len(by_type) == 13
    for etype, el in by_type.items():
        assert el.style["preset"] == name and el.style["brand_kit"] is False
        for param, source in STYLE_TARGETS[etype].items():
            if param == "animation":
                if spec["fade"]:
                    assert el.animation is not None and el.animation.parameters["duration"] == spec["fade"]
                else:
                    assert el.animation is None
                continue
            if source.startswith("role:"):
                assert el.parameters[param] == roles[source[5:]], (etype, param)
            elif source == "box_color":
                if spec["box"]:
                    assert el.parameters[param] == f"{roles['background']}@{spec['box_opacity']:g}"
            elif source == "font_id":
                assert el.parameters[param] == {"font_id": spec["font_id"]}
            else:
                assert el.parameters[param] == spec[source], (etype, param)
    assert by_type["meme"].style["applied"] == {}  # the template fixes its look
    # what reaches ffmpeg-skill: the role colours, as the graphics templates and overlay take them
    lt = _argv(lower_third_element(), {"style": name})
    assert _flag(lt, "--primary") == roles["primary"] and _flag(lt, "--text-color") == roles["text"]
    txt = _argv(text_overlay_element(end=3.0), {"style": name})
    assert _flag(txt, "--font-color") == "0x" + roles["text"] and _flag(txt, "--margin") == str(spec["margin"])
    assert ("--box" in txt) is spec["box"]


# ---- precedence: explicit > brand kit > preset
def test_a_preset_plus_a_brand_kit_uses_the_kits_colours():
    kit = {"primary": "00FF00", "text": "navy", "background": "202020", "secondary": "FF00FF"}
    doc = parse_request(request_doc(every_element(), options={"style": "news", "brand_kit": kit}))
    by_type = {e.type: e for e in doc.elements}
    assert by_type["lower_third"].parameters["primary_color"] == "00FF00"
    assert by_type["lower_third"].parameters["text_color"] == NAMED_COLORS["navy"]   # a name in the kit is stored as RRGGBB
    assert by_type["text_overlay"].parameters["box_color"] == f"202020@{STYLE_PRESETS['news']['box_opacity']:g}"
    assert by_type["text_overlay"].parameters["font_size"] == 42                      # not a preset target: default
    assert by_type["lower_third"].style["applied"] == {"primary_color": "brand_kit", "text_color": "brand_kit"}
    lt = _argv(lower_third_element(), {"style": "news", "brand_kit": kit})
    assert _flag(lt, "--primary") == "00FF00" and _flag(lt, "--text-color") == "000080"
    txt = _argv(text_overlay_element(end=3.0), {"style": "news", "brand_kit": kit})
    assert _flag(txt, "--font-color") == "0x000080" and _flag(txt, "--box-color") == "0x202020@0.85"


def test_a_brand_kit_without_a_preset_fills_role_colours_only():
    doc = parse_request(request_doc([lower_third_element(), text_overlay_element(end=3.0)], options={"brand_kit": {"primary": "123456"}}))
    lt, txt = doc.elements
    assert lt.parameters == {"name": "Ada Lovelace", "primary_color": "123456"}
    assert txt.parameters["margin"] == 24 and txt.animation is None  # no preset: no margin, no fade
    assert lt.style == {"preset": None, "brand_kit": True, "applied": {"primary_color": "brand_kit"}}


def test_an_explicit_parameter_wins_over_kit_and_preset():
    el = lower_third_element(primary_color="ABCDEF")
    doc = parse_request(request_doc([el], options={"style": "neon", "brand_kit": {"primary": "00FF00"}}))
    assert doc.elements[0].parameters["primary_color"] == "ABCDEF"
    assert doc.elements[0].style["applied"] == {"text_color": "preset"}


def test_an_element_style_overrides_the_document_style():
    el = dict(lower_third_element(), style="wafu")
    doc = parse_request(request_doc([el, title_element(end=3.0)], options={"style": "swiss"}))
    lt, t = sorted(doc.elements, key=lambda e: e.type)
    assert lt.type == "lower_third" and lt.parameters["primary_color"] == STYLE_PRESETS["wafu"]["roles"]["primary"]
    assert t.parameters["primary_color"] == STYLE_PRESETS["swiss"]["roles"]["primary"]


def test_an_explicit_animation_wins_and_a_fade_that_does_not_fit_is_skipped():
    el = text_overlay_element(end=3.0)
    el["animation"] = {"kind": "fade", "parameters": {"duration": 0.1}}
    assert parse_request(request_doc([el], options={"style": "wafu"})).elements[0].animation.parameters["duration"] == 0.1
    short = parse_request(request_doc([text_overlay_element(end=0.6)], options={"style": "wafu"})).elements[0]  # 0.5 s > 0.3 s
    assert short.animation is None and short.style["skipped"] == ["animation"] and "animation" not in short.style["applied"]


def test_no_preset_margin_under_a_platform_safe_zone():
    doc = parse_request(request_doc([text_overlay_element(end=3.0)], options={"style": "news", "platform": "tiktok"}))
    assert "margin" not in doc.elements[0].parameters
    assert "--margin" not in _argv(text_overlay_element(end=3.0), {"style": "news", "platform": "tiktok"})


def test_unstyled_documents_are_unchanged():
    doc = parse_request(request_doc([lower_third_element()]))
    assert doc.elements[0].style is None and "style" not in doc.elements[0].to_dict()
    assert doc.elements[0].parameters == {"name": "Ada Lovelace"}


@pytest.mark.parametrize("options, element_style, field", [
    ({"style": "glam"}, None, "document.options.style"),
    (None, "glam", "elements[0].style"),
    (None, 3, "elements[0].style"),
    ({"brand_kit": {"accent": "00FF00"}}, None, "document.options.brand_kit"),
    ({"brand_kit": {}}, None, "document.options.brand_kit"),
    ({"brand_kit": "00FF00"}, None, "document.options.brand_kit"),
    ({"brand_kit": {"primary": "00FF00@0.5"}}, None, "document.options.brand_kit.primary"),
    ({"brand_kit": {"primary": "notacolour"}}, None, "document.options.brand_kit.primary"),
])
def test_bad_style_requests_are_refused(options, element_style, field):
    el = lower_third_element()
    if element_style is not None:
        el["style"] = element_style
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([el], options=options))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["field"] == field


def test_forbidden_fields_are_still_refused_in_a_brand_kit():
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([lower_third_element()], options={"brand_kit": {"primary": "00FF00", "filter": "x"}}))
    assert e.value.details["reason"] == "forbidden_field"


def test_the_contract_documents_the_request_fields():
    c = skill_contract()
    assert c["request"]["shape"]["options?"]["style?"] in STYLE_PRESETS
    assert set(c["request"]["shape"]["options?"]["brand_kit?"]) == {"primary?", "secondary?", "background?", "text?"}
    assert c["style_presets"]["precedence"].startswith("an explicit parameter > a brand_kit role > the preset's role")


# ---- one real render: a styled document goes through ffmpeg-skill unchanged in shape
def test_a_styled_document_renders(skill_dir, workspace):
    doc = request_doc([lower_third_element(end=2.5), text_overlay_element(start=3.0, end=5.0, text="styled")],
                      options={"style": "corporate", "brand_kit": {"primary": "00FF00"}})
    res = _executor(skill_dir, workspace).response(doc)
    assert res["ok"]
    assert [t["style"]["preset"] for t in res["timeline"]] == ["corporate", "corporate"]
    assert res["operations"][0]["parameters"]["primary_color"] == "00FF00"
    assert _probe(skill_dir, res["output"]["path"])["video"]["width"] == 320
