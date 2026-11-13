"""Brand kit reader (docs/decisions.md ADR-31): `options.kit` takes a Velisa kit@1 file (a superset of ffmpeg-skill brand.json) and
maps it onto theme@1 or brand_kit by the spec's mapping table. Unit tests for the mapping, what is reported unmapped, the fallback to
brand_kit and the precedence; integration tests render the same document from the kit fixture and from the equivalent theme@1 and
compare the decoded pixels, and check that the contrast report runs on the kit's colours."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from motion_graphics.contract import skill_contract
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.kit import load_kit, map_kit
from motion_graphics.model import parse_request
from motion_graphics.security import PathPolicy
from motion_graphics.styles import STYLE_PRESETS
from motion_graphics.themes import parse_theme

from conftest import request_doc, text_overlay_element, title_element
from test_integration import _executor
from test_themes import _ff, lower_third, logo

KIT = json.loads((Path(__file__).resolve().parent / "fixtures" / "kit_lagoon.json").read_text(encoding="utf-8"))
# the theme@1 file the mapping table gives for the fixture, written by hand
EQUIVALENT_THEME = {"schema": "motion-graphics/theme@1", "name": "lagoon",
                    "colors": {"primary": "34C759", "secondary": "5AC8FA", "background": "0B1A2A", "text": "66E0FF"},
                    "font": {"font_file": "font.ttf"},
                    "logo": {"path": "logo_b.png", "position": "top-right", "margin": 40, "scale_width": 64, "opacity": 1.0},
                    "spacing": {"margin": 40}}
UNMAPPED_KEYS = ["caption", "colors.accent", "colors.outline", "contrast", "font", "fonts", "lang", "logo_variants", "loudness", "styles"]
# a plain ffmpeg-skill brand.json: no schema, no secondary colour
BRAND_JSON = {"font": "DejaVu Sans", "colors": {"primary": "FFD200", "text": "FFFFFF", "background": "101418", "outline": "000000"},
              "logo": "logo.png", "logo_position": "top-right", "safe_margin": 48}


def _loaded_from(mapped):
    t = parse_theme(mapped["theme"], "kit")
    t["label"] = "kit"
    return t


# ---- the mapping table
def test_the_fixture_maps_onto_the_equivalent_theme_and_reports_every_other_key():
    m = map_kit(json.loads(json.dumps(KIT)), "kit")
    assert m["mapped_to"] == "theme@1" and m["theme"] == EQUIVALENT_THEME
    assert [u["key"] for u in m["unmapped"]] == UNMAPPED_KEYS
    assert all(u["reason"] for u in m["unmapped"])
    assert "secondary" in {u["key"]: u["reason"] for u in m["unmapped"]}["colors.accent"]     # accent is not used as secondary


def test_only_what_the_kit_states_is_passed_on():
    m = map_kit({"colors": dict(EQUIVALENT_THEME["colors"])}, "kit")
    assert m["theme"] == {"schema": "motion-graphics/theme@1", "colors": EQUIVALENT_THEME["colors"]} and m["unmapped"] == []
    m = map_kit({"colors": dict(EQUIVALENT_THEME["colors"]), "logo": "l.png", "logo_scale": 100}, "kit")
    assert m["theme"]["logo"] == {"path": "l.png", "scale_width": 100} and "spacing" not in m["theme"]
    m = map_kit({"colors": dict(EQUIVALENT_THEME["colors"]), "logo_opacity": 0.5, "safe_margin": 10, "name": "Acme Corp"}, "kit")
    assert m["theme"] == {"schema": "motion-graphics/theme@1", "colors": EQUIVALENT_THEME["colors"], "spacing": {"margin": 10}}
    assert {u["key"] for u in m["unmapped"]} == {"logo_opacity", "name"}


def test_colours_spelling_is_read_as_colors():
    m = map_kit({"colours": dict(EQUIVALENT_THEME["colors"])}, "kit")
    assert m["mapped_to"] == "theme@1" and m["theme"]["colors"] == EQUIVALENT_THEME["colors"]
    with pytest.raises(MotionGraphicsError):
        map_kit({"colours": {"primary": "FF0000"}, "colors": {"primary": "FF0000"}}, "kit")


def test_a_brand_json_without_secondary_maps_onto_brand_kit_and_reports_the_theme_only_keys():
    m = map_kit(dict(BRAND_JSON), "kit")
    assert m["mapped_to"] == "brand_kit" and m["brand_kit"] == {"primary": "FFD200", "background": "101418", "text": "FFFFFF"}
    assert [u["key"] for u in m["unmapped"]] == ["colors.outline", "font", "logo", "logo_position", "safe_margin"]
    assert "colors.secondary" in {u["key"]: u["reason"] for u in m["unmapped"]}["logo"]


@pytest.mark.parametrize("bad", [
    [], {"schema": "velisa/kit@2", "colors": {"primary": "FF0000"}}, {"font": "X"}, {"colors": {"outline": "000000"}},
    {"colors": "FF0000"}, {"colors": {"primary": "FF0000@0.5"}}, {"colors": {"primary": "nope"}},
    dict(EQUIVALENT_THEME, filter="x"),
])
def test_bad_kits_are_refused(bad):
    with pytest.raises(MotionGraphicsError) as e:
        map_kit(bad, "kit")
    assert e.value.code == "INVALID_INPUT" and e.value.details["reason"] in ("bad_kit", "kit_without_colors")


def test_a_logo_position_outside_the_nine_names_is_refused():
    with pytest.raises(MotionGraphicsError) as e:
        map_kit({"colors": dict(EQUIVALENT_THEME["colors"]), "logo": "l.png", "logo_position": "middle"}, "kit")
    assert e.value.details["field"] == "kit.logo_position"


# ---- the request
def test_options_kit_is_checked_structurally_and_refused_with_another_brand_input():
    doc = parse_request(request_doc([title_element()], options={"kit": "kit.json"}))
    assert doc.options["kit"] == "kit.json" and doc.elements[0].style is None       # validate: nothing read, nothing applied
    for options in ({"kit": ""}, {"kit": 3}, {"kit": "k.json", "brand_kit": {"primary": "FF0000"}}, {"kit": "k.json", "themes": {"a": "a.json"}}):
        with pytest.raises(MotionGraphicsError) as e:
            parse_request(request_doc([title_element()], options=options))
        assert e.value.code == "INVALID_REQUEST"


def test_explicit_values_win_over_the_kit_and_the_kit_over_the_preset():
    t = _loaded_from(map_kit(json.loads(json.dumps(KIT)), "kit"))
    doc = parse_request(request_doc([lower_third(primary_color="ABCDEF"), text_overlay_element(end=3.0)],
                                    options={"kit": "kit.json", "style": "corporate"}), t)
    lt, txt = doc.elements
    assert lt.parameters["primary_color"] == "ABCDEF" and lt.parameters["text_color"] == "66E0FF"
    assert txt.parameters["box"] is STYLE_PRESETS["corporate"]["box"] and txt.style["applied"]["box"] == "preset"
    assert txt.parameters["margin"] == 40 and txt.style["applied"]["margin"] == "theme"
    assert txt.parameters["font_color"] == "66E0FF"


def test_a_brand_kit_mapping_applies_its_roles_over_the_preset():
    doc = parse_request(request_doc([lower_third()], options={"kit": "kit.json", "style": "news"}), None, {"primary": "FFD200"})
    lt = doc.elements[0]
    assert lt.parameters["primary_color"] == "FFD200" and lt.style["brand_kit"] is True and lt.style["applied"]["primary_color"] == "brand_kit"


def test_load_kit_resolves_paths_relative_to_the_kit_file_and_reports_the_brand_kit_contrast(tmp_path):
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "kit.json").write_text(json.dumps(KIT), encoding="utf-8")
    (tmp_path / "brand" / "font.ttf").write_bytes(b"x")
    (tmp_path / "brand" / "logo_b.png").write_bytes(b"x")
    record, theme = load_kit(PathPolicy(str(tmp_path)), str(tmp_path / "brand" / "kit.json"))
    assert record["mapped_to"] == "theme@1" and record["theme"] == EQUIVALENT_THEME and record["schema"] == "velisa/kit@1"
    assert theme["logo"]["path"] == str(tmp_path / "brand" / "logo_b.png") and theme["font"] == {"font_file": str(tmp_path / "brand" / "font.ttf")}
    assert [c["colors"] for c in theme["contrast"]][:2] == [["66E0FF", "0B1A2A"], ["34C759", "0B1A2A"]]
    (tmp_path / "brand.json").write_text(json.dumps(dict(BRAND_JSON, colors={"primary": "C8C8C8", "text": "CCCCCC"})), encoding="utf-8")
    record, theme = load_kit(PathPolicy(str(tmp_path)), str(tmp_path / "brand.json"))
    assert theme is None and record["mapped_to"] == "brand_kit"
    # only the pairs whose colours the kit states: text and primary on the template plate (no background in this kit)
    assert [(c["foreground"], c["background"]) for c in record["contrast"]] == [("text", "template plate 101418"), ("primary", "template plate 101418")]
    (tmp_path / "big.json").write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(MotionGraphicsError) as e:
        load_kit(PathPolicy(str(tmp_path)), str(tmp_path / "big.json"))
    assert e.value.details["reason"] == "bad_kit" and "kit file is larger" in e.value.message


def test_contract_documents_the_kit():
    c = skill_contract()
    assert c["kit"]["file_schema"] == "velisa/kit@1" and "kit?" in c["request"]["shape"]["options?"]
    # documented in kit.result, as response.theme is in themes.result: response.conditional is a key the agent pins as fatal
    assert "kit" not in c["response"]["conditional"] and "response.kit" in c["kit"]["result"]


# ---- renders
def _setup(workspace):
    _ff("-f", "lavfi", "-i", "color=c=0x808080:s=640x360:r=25:d=4", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(workspace / "gray.mp4"))
    brand = workspace / "brand"
    brand.mkdir()
    _ff("-f", "lavfi", "-i", "color=c=0x0000FF:s=64x64", "-frames:v", "1", str(brand / "logo_b.png"))
    (brand / "font.ttf").write_bytes((workspace / "font.ttf").read_bytes())
    (brand / "kit.json").write_text(json.dumps(KIT), encoding="utf-8")
    (brand / "theme.json").write_text(json.dumps(EQUIVALENT_THEME), encoding="utf-8")


def _frames_md5(path):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(raw) >= 640 * 360 * 3 * 90
    return hashlib.md5(raw).hexdigest()


DOC_ELEMENTS = [lower_third(start=0.0, end=1.5), title_element("t", start=1.6, end=3.0, title="Episode 12"), logo(start=0.0, end=3.5)]


def test_a_kit_and_the_equivalent_theme_render_the_same_pixels(skill_dir, workspace):
    """The roadmap step's check: the kit fixture and the theme@1 the mapping table gives for it render the same document to the
    same decoded frames, and the contrast report runs on the kit's colours."""
    _setup(workspace)
    ex = _executor(skill_dir, workspace)
    by_kit = ex.response(request_doc(DOC_ELEMENTS, video="gray.mp4", output="out/kit.mp4", options={"kit": "brand/kit.json", "preset": "ultrafast"}))
    by_theme = ex.response(request_doc(DOC_ELEMENTS, video="gray.mp4", output="out/theme.mp4",
                                       options={"themes": {"lagoon": "brand/theme.json"}, "preset": "ultrafast"}))
    assert by_kit["ok"] and by_theme["ok"]
    assert by_kit["kit"]["mapped_to"] == "theme@1" and by_kit["kit"]["theme"] == EQUIVALENT_THEME
    assert [u["key"] for u in by_kit["kit"]["unmapped"]] == UNMAPPED_KEYS
    assert sorted(w for w in by_kit["warnings"] if w.startswith("options.kit:")) == sorted(by_kit["warnings"])
    assert len(by_kit["warnings"]) == len(UNMAPPED_KEYS) and by_theme["warnings"] == []
    # the contrast report runs on the kit's colours: the same pairs and ratios as the equivalent theme's
    assert by_kit["theme"]["contrast"] == by_theme["theme"]["contrast"]
    assert by_kit["theme"]["colors"] == {r: KIT["colors"][r] for r in ("primary", "secondary", "background", "text")}
    assert _frames_md5(by_kit["output"]["path"]) == _frames_md5(by_theme["output"]["path"])
    assert [op["element_id"] for op in by_kit["operations"]] == [op["element_id"] for op in by_theme["operations"]]


def test_a_low_contrast_brand_json_warns_on_its_own_colours(skill_dir, workspace):
    _setup(workspace)
    (workspace / "brand.json").write_text(json.dumps({"colors": {"primary": "C8C8C8", "background": "DDDDDD", "text": "CCCCCC"}}), encoding="utf-8")
    ex = _executor(skill_dir, workspace)
    ex.dry_run = True
    res = ex.response(request_doc([lower_third()], video="gray.mp4", output="out/b.mp4", options={"kit": "brand.json"}))
    assert res["kit"]["mapped_to"] == "brand_kit" and "theme" not in res
    low = [c for c in res["kit"]["contrast"] if not c["ok"]]
    assert {(c["foreground"], c["background"]) for c in low} >= {("text", "background"), ("primary", "background")}
    assert sum("low contrast" in w for w in res["warnings"]) == len(low)
    assert res["plan"]["timeline"][0]["parameters"]["primary_color"] == "C8C8C8"
