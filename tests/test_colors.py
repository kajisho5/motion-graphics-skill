"""Colour values (docs/decisions.md ADR-20): the fixed colour-name table, the conversion of names to RRGGBB for the
graphics-template elements (ffmpeg-skill/graphics takes RRGGBB only), the refusal of @alpha on those elements, and
what the contract says about it. The real renders are in test_integration.py (test_template_colour_*)."""
import json
import subprocess

import pytest

from motion_graphics.colors import NAMED_COLORS, template_color
from motion_graphics.contract import skill_contract
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.model import ELEMENT_TYPES, parse_request
from motion_graphics.security import PathPolicy

from conftest import (bug_element, chapter_element, countdown_element, hook_element, progress_element, request_doc, run_cli, one_json,
                      sticker_element, text_overlay_element, title_element, video_overlay_element)

TEMPLATE_COLOUR_PARAMS = sorted((t, k) for t, s in ELEMENT_TYPES.items() if s["tool"] == "graphics"
                                for k, ps in s["parameters"].items() if ps["type"] == "color")
# overlay colours that ffmpeg reads itself (names, 0xRRGGBB, @alpha); shape's colour goes to ffmpeg-skill/background first and
# is opaque (alpha: false, ADR-22), so it is covered by the shape tests below instead
OVERLAY_COLOUR_PARAMS = sorted((t, k) for t, s in ELEMENT_TYPES.items() if s["tool"] == "overlay"
                               for k, ps in s["parameters"].items() if ps["type"] == "color" and ps.get("alpha", True))
BUILDERS = {"title": title_element, "bug": bug_element, "chapter": chapter_element, "progress": progress_element, "countdown": countdown_element,
            "sticker": sticker_element, "hook": hook_element,
            "lower_third": lambda **p: {"id": "lt1", "type": "lower_third", "start": 0, "end": 2, "parameters": {"name": "Ada", **p}}}


def _graphics_argv(el_dict):
    doc = parse_request(request_doc([el_dict]))
    return Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "in.mp4", "out.mp4", None, None, crf=18, preset="medium")[1]


def _flag(argv, name):
    return argv[argv.index(name) + 1]


# ---- the table
def test_every_template_colour_parameter_is_covered():
    assert TEMPLATE_COLOUR_PARAMS == [("bug", "text_color"), ("chapter", "primary_color"), ("countdown", "primary_color"), ("hook", "primary_color"),
                                      ("hook", "text_color"), ("lower_third", "primary_color"), ("lower_third", "text_color"), ("progress", "primary_color"),
                                      ("sticker", "primary_color"), ("title", "primary_color"), ("title", "text_color")]
    assert set(t for t, _ in TEMPLATE_COLOUR_PARAMS) == set(BUILDERS)


def test_named_colour_table_is_ffmpegs_own():
    # the table is fixed in the skill (same request, same argv), but it must say what the installed ffmpeg says, so a name
    # means the same RRGGBB on a template element (converted here) and on an overlay element (resolved by ffmpeg)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-colors"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = [line.split() for line in r.stdout.splitlines()[1:] if line.strip()]
    installed = {name.lower(): value.lstrip("#").upper() for name, value in rows}
    assert installed == NAMED_COLORS


def test_named_colour_table_shape():
    assert len(NAMED_COLORS) == 140
    assert NAMED_COLORS["white"] == "FFFFFF" and NAMED_COLORS["navy"] == "000080" and NAMED_COLORS["black"] == "000000"
    assert all(k == k.lower() and k.isalpha() and len(v) == 6 and v == v.upper() and int(v, 16) >= 0 for k, v in NAMED_COLORS.items())


def test_template_color_conversion():
    assert template_color("navy") == "000080" and template_color("NaVy") == "000080"
    assert template_color("ffcc00") == "ffcc00"  # hex goes through as given (graphics.py upper-cases it itself)


# ---- argv: graphics gets RRGGBB, never a name
@pytest.mark.parametrize("etype,param", TEMPLATE_COLOUR_PARAMS)
def test_template_colour_name_is_sent_as_rrggbb(etype, param):
    flag = {"text_color": "--text-color", "primary_color": "--primary"}[param]
    assert _flag(_graphics_argv(BUILDERS[etype](**{param: "Crimson"})), flag) == "DC143C"
    assert _flag(_graphics_argv(BUILDERS[etype](**{param: "12ab9F"})), flag) == "12ab9F"


def test_overlay_colours_are_unchanged():
    doc = parse_request(request_doc([text_overlay_element(font_color="Yellow", border_color="000000", box=True, box_color="navy@0.5")]))
    argv = Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "in.mp4", "out.mp4", None, None, crf=18, preset="medium")[1]
    assert _flag(argv, "--font-color") == "Yellow" and _flag(argv, "--border-color") == "0x000000" and _flag(argv, "--box-color") == "navy@0.5"


# ---- @alpha on a template colour: refused, naming the element
@pytest.mark.parametrize("etype,param", TEMPLATE_COLOUR_PARAMS)
@pytest.mark.parametrize("value", ["red@0.5", "FF0000@1.0", "white@0"])
def test_template_colour_alpha_is_refused_naming_the_element(etype, param, value):
    el = BUILDERS[etype](**{param: value})
    el["id"] = "the-one"
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([title_element(element_id="other"), el]))
    assert e.value.code == "UNSUPPORTED_OPERATION"
    assert e.value.details == {"field": f"elements[1].parameters.{param}", "element_id": "the-one", "element_type": etype,
                               "value": value, "reason": "color_alpha_not_supported"}
    assert "the-one" in e.value.message


@pytest.mark.parametrize("etype,param", OVERLAY_COLOUR_PARAMS)
def test_overlay_colours_still_take_names_and_alpha(etype, param):
    builder = {"text_overlay": text_overlay_element, "video_overlay": video_overlay_element}[etype]
    assert parse_request(request_doc([builder(**{param: "DarkOrange@0.25"})])).elements[0].parameters[param] == "DarkOrange@0.25"


def test_validate_cli_reports_the_alpha_refusal_as_a_structured_error(tmp_path):
    code, out, _ = run_cli(["validate", "-", "--json"], stdin_text=json.dumps(request_doc([progress_element(element_id="bar", primary_color="red@0.5")])), cwd=str(tmp_path))
    doc = one_json(out)
    assert code != 0 and doc["ok"] is False
    assert doc["error"]["code"] == "UNSUPPORTED_OPERATION" and doc["error"]["details"]["element_id"] == "bar"
    assert doc["error"]["details"]["reason"] == "color_alpha_not_supported" and doc["error"]["retryable"] is False


# ---- unknown names: ffmpeg would fail on them at render time (or, for `random`, render a different colour every run)
@pytest.mark.parametrize("builder,param", [(text_overlay_element, "font_color"), (title_element, "primary_color")])
@pytest.mark.parametrize("name", ["random", "notacolour", "bikeshed", "darkgrey"])
def test_unknown_colour_names_are_refused_everywhere(builder, param, name):
    if name in NAMED_COLORS:
        pytest.fail(f"{name} is in the table; pick a name that is not")
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([builder(**{param: name})]))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["reason"] == "unknown_color_name"


# ---- the contract tells the truth per element
def test_contract_marks_every_template_colour_alpha_false_and_no_overlay_colour():
    by_type = {t["type"]: t for t in skill_contract()["element_types"]}
    for etype, param in TEMPLATE_COLOUR_PARAMS:
        ps = by_type[etype]["parameters"][param]
        assert ps["type"] == "color" and ps["alpha"] is False and "no @alpha" in ps["description"]
    for etype, param in OVERLAY_COLOUR_PARAMS:
        assert "alpha" not in by_type[etype]["parameters"][param]


def test_contract_publishes_the_colour_table():
    colors = skill_contract()["colors"]
    assert colors["named"] == NAMED_COLORS
    assert "alpha: false" in colors["alpha"] and set(colors["delegates"]) == {"ffmpeg-skill/graphics", "ffmpeg-skill/overlay", "ffmpeg-skill/background"}


# ---- shape (ADR-22): its colour goes to ffmpeg-skill/background as 0xRRGGBB, opaque; transparency is `opacity`
def test_shape_colour_is_sent_to_background_as_rrggbb():
    from conftest import shape_element
    doc = parse_request(request_doc([shape_element(color="Crimson")]))
    assert Executor._shape_clip_parameters(doc.elements[0])["color"] == "DC143C"
    doc = parse_request(request_doc([shape_element(color="12ab9F")]))
    assert Executor._shape_clip_parameters(doc.elements[0])["color"] == "12ab9F"


@pytest.mark.parametrize("value", ["red@0.5", "FF0000@1.0"])
def test_shape_colour_alpha_is_refused_pointing_at_opacity(value):
    from conftest import shape_element
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([shape_element(element_id="plate", color=value)]))
    assert e.value.code == "UNSUPPORTED_OPERATION" and e.value.details["reason"] == "color_alpha_not_supported"
    assert e.value.details["element_id"] == "plate" and "opacity" in e.value.message and "background" in e.value.message
    ps = {t["type"]: t for t in skill_contract()["element_types"]}["shape"]["parameters"]["color"]
    assert ps["alpha"] is False and "no @alpha" in ps["description"]
