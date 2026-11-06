"""Brand themes (docs/decisions.md ADR-29): colours, font, logo and spacing from one JSON file, applied to every element, with
several themes in a request and one chosen, and a contrast check. Unit tests for the file, the request, the precedence and the
contrast arithmetic; integration tests render the same document with two themes and read the colours back from the pixels and
the fonts from ffmpeg-skill's own report, and check that a low-contrast theme warns."""
import json
import subprocess

import pytest

from motion_graphics.contract import skill_contract
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.model import parse_request
from motion_graphics.security import PathPolicy
from motion_graphics.styles import STYLE_PRESETS
from motion_graphics.themes import CONTRAST_PAIRS, contrast_ratio, contrast_report, parse_theme, parse_theme_options

from conftest import request_doc, run_cli, text_overlay_element, title_element
from test_integration import _executor

THEME_A = {"schema": "motion-graphics/theme@1", "name": "sunrise",
           "colors": {"primary": "FF3B30", "secondary": "FF9500", "background": "101418", "text": "FFE066"},
           "font": {"font_id": "system:dejavu-serif"}, "logo": {"path": "logo_a.png", "position": "top-right", "margin": 16, "scale_width": 64},
           "spacing": {"margin": 40}}
THEME_B = {"name": "lagoon", "colors": {"primary": "34C759", "secondary": "5AC8FA", "background": "0B1A2A", "text": "66E0FF"},
           "font": {"font_file": "font.ttf"}, "logo": {"path": "logo_b.png", "position": "top-right", "margin": 16, "scale_width": 64},
           "spacing": {"margin": 40}, "base_style": "corporate"}
THEME_LOW = {"name": "fog", "colors": {"primary": "C8C8C8", "secondary": "D0D0D0", "background": "DDDDDD", "text": "CCCCCC"}}


def lower_third(element_id="lt", start=0.0, end=1.5, **extra):
    return {"id": element_id, "type": "lower_third", "start": start, "end": end, "parameters": {"name": "Ada Lovelace", "title": "Analyst", **extra}}


def logo(element_id="logo", start=0.0, end=3.5, **extra):
    return {"id": element_id, "type": "logo", "start": start, "end": end, "parameters": dict(extra)}


def loaded(theme, label="a", base=None):
    t = parse_theme(json.loads(json.dumps(theme)), "theme")
    t["label"] = label
    return t


# ---- contrast
def test_contrast_ratio_reference_values():
    assert contrast_ratio("FFFFFF", "000000") == pytest.approx(21.0)
    assert contrast_ratio("777777", "777777") == pytest.approx(1.0)
    assert contrast_ratio("767676", "FFFFFF") == pytest.approx(4.54, abs=0.01)    # the usual "just passes AA" grey


def test_the_contrast_report_checks_every_stated_pair():
    rep = contrast_report(loaded(THEME_LOW)["colors"])
    assert len(rep) == len(CONTRAST_PAIRS) and not any(c["ok"] for c in rep[:2])
    assert all(c["ok"] for c in contrast_report(loaded(THEME_A)["colors"]))
    assert all(c["ok"] for c in contrast_report(loaded(THEME_B)["colors"]))


# ---- the file
@pytest.mark.parametrize("bad", [
    {"name": "x"}, {"colors": {"primary": "FF0000"}}, {"colors": dict(THEME_A["colors"], text="FF0000@0.5")},
    dict(THEME_A, schema="other/theme@1"), dict(THEME_A, font={"font_id": "a", "font_file": "b"}), dict(THEME_A, logo={"position": "top"}),
    dict(THEME_A, logo={"path": "l.png", "position": "middle"}), dict(THEME_A, spacing={"margin": -1}), dict(THEME_A, base_style="glam"),
    dict(THEME_A, extra=1), dict(THEME_A, logo={"path": "l.png", "filter": "x"}),
])
def test_bad_theme_files_are_refused(bad):
    with pytest.raises(MotionGraphicsError):
        parse_theme(bad, "theme")


def test_theme_options_are_checked_without_reading_files():
    assert parse_theme_options({"themes": {"a": "a.json"}}) == {"themes": {"a": "a.json"}, "theme": "a"}
    for bad in ({"themes": {"a": "a.json", "b": "b.json"}}, {"themes": {"a": "a.json"}, "theme": "b"}, {"theme": "a"}, {"themes": {}}):
        with pytest.raises(MotionGraphicsError) as e:
            parse_theme_options(bad)
        assert e.value.code == "INVALID_REQUEST"
    with pytest.raises(MotionGraphicsError):
        parse_request(request_doc([title_element()], options={"themes": {"a": "a.json"}, "brand_kit": {"primary": "FF0000"}}))
    doc = parse_request(request_doc([title_element()], options={"themes": {"a": "a.json", "b": "b.json"}, "theme": "b"}))
    assert doc.options["themes"]["theme"] == "b" and doc.elements[0].style is None      # structural only: nothing applied


# ---- applying a theme
def test_a_theme_fills_colours_margin_font_and_the_logo_settings():
    a = loaded(THEME_A)
    doc = parse_request(request_doc([lower_third(), title_element(end=3.0), text_overlay_element(end=3.0), logo()],
                                    options={"themes": {"a": "a.json"}}), a)
    lt, t, txt, lg = sorted(doc.elements, key=lambda e: e.element_id)[1], *[e for e in doc.elements if e.type in ("title", "text_overlay", "logo")]
    assert lt.parameters["primary_color"] == "FF3B30" and lt.parameters["text_color"] == "FFE066"
    assert t.parameters["primary_color"] == "FF3B30"
    assert txt.parameters["font_color"] == "FFE066" and txt.parameters["margin"] == 40 and txt.parameters["font"] == {"font_id": "system:dejavu-serif"}
    assert lg.parameters == {"position": "top-right", "margin": 16, "scale_width": 64, "opacity": 1.0}
    assert lt.style == {"preset": None, "theme": "a", "applied": {"primary_color": "theme", "text_color": "theme"}}


def test_explicit_parameters_win_and_the_base_style_fills_the_rest():
    b = loaded(THEME_B, "b")
    doc = parse_request(request_doc([lower_third(primary_color="ABCDEF"), text_overlay_element(end=3.0)], options={"themes": {"b": "b.json"}}), b)
    lt, txt = doc.elements
    assert lt.parameters["primary_color"] == "ABCDEF" and lt.parameters["text_color"] == "66E0FF"
    assert txt.parameters["box"] is STYLE_PRESETS["corporate"]["box"]                       # from base_style
    assert txt.parameters["box_color"] == f"0B1A2A@{STYLE_PRESETS['corporate']['box_opacity']:g}"   # the theme's background
    assert txt.parameters["margin"] == 40 and txt.style["applied"]["margin"] == "theme" and txt.style["applied"]["box"] == "preset"


def test_switching_theme_switches_every_element():
    elements = [lower_third(), title_element(end=3.0)]
    a = parse_request(request_doc(elements, options={"themes": {"a": "a.json", "b": "b.json"}, "theme": "a"}), loaded(THEME_A, "a"))
    b = parse_request(request_doc(elements, options={"themes": {"a": "a.json", "b": "b.json"}, "theme": "b"}), loaded(THEME_B, "b"))
    assert [e.parameters["primary_color"] for e in a.elements] == ["FF3B30"] * 2
    assert [e.parameters["primary_color"] for e in b.elements] == ["34C759"] * 2


def test_graphics_templates_get_the_theme_font():
    from motion_graphics.fonts import resolve_font
    doc = parse_request(request_doc([lower_third()], options={"themes": {"a": "a.json"}}), loaded(THEME_A))
    font = resolve_font({"font_id": "system:dejavu-serif"}, PathPolicy("."))
    argv = Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "in.mp4", "out.mp4", None, font, crf=18, preset="medium")[1]
    assert argv[argv.index("--font") + 1] == "DejaVu Serif"


def test_contract_documents_themes_and_the_logo_element():
    c = skill_contract()
    assert c["themes"]["file_schema"] == "motion-graphics/theme@1" and c["themes"]["contrast"]["template_plate"] == "101418"
    lg = {t["type"]: t for t in c["element_types"]}["logo"]
    assert lg["tool"] == "ffmpeg-skill/overlay" and lg["animation"] == "none" and "image_path" not in lg["parameters"]


def test_theme_check_command(tmp_path):
    (tmp_path / "fog.json").write_text(json.dumps(THEME_LOW), encoding="utf-8")
    code, out, _ = run_cli(["theme-check", str(tmp_path / "fog.json"), "--json"])
    doc = json.loads(out)
    assert code == 0 and doc["ok"] and len(doc["warnings"]) >= 2 and all("low contrast" in w for w in doc["warnings"])
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    code, out, _ = run_cli(["theme-check", str(tmp_path / "bad.json"), "--json"])
    assert code != 0 and json.loads(out)["error"]["code"] == "INVALID_INPUT"


# ---- renders
def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *args], check=True)


def _setup(workspace):
    _ff("-f", "lavfi", "-i", "color=c=0x808080:s=640x360:r=25:d=4", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(workspace / "gray.mp4"))
    themes = workspace / "themes"
    themes.mkdir()
    _ff("-f", "lavfi", "-i", "color=c=0xFF0000:s=64x64", "-frames:v", "1", str(themes / "logo_a.png"))
    _ff("-f", "lavfi", "-i", "color=c=0x0000FF:s=64x64", "-frames:v", "1", str(themes / "logo_b.png"))
    (themes / "font.ttf").write_bytes((workspace / "font.ttf").read_bytes())
    for name, t in (("a", THEME_A), ("b", THEME_B), ("fog", THEME_LOW)):
        (themes / f"{name}.json").write_text(json.dumps(t), encoding="utf-8")


def _frame(path, t):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(raw) == 640 * 360 * 3
    return raw


def _count_near(raw, colour, x0, y0, x1, y1, tol=60):
    r0, g0, b0 = (int(colour[i:i + 2], 16) for i in (0, 2, 4))
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = 3 * (y * 640 + x)
            if abs(raw[i] - r0) + abs(raw[i + 1] - g0) + abs(raw[i + 2] - b0) <= tol:
                n += 1
    return n


DOC_ELEMENTS = [lower_third(start=0.0, end=1.5), title_element("t", start=1.6, end=3.0, title="Episode 12"), logo(start=0.0, end=3.5)]


def test_two_themes_render_the_same_document_in_their_own_colours_and_fonts(skill_dir, workspace):
    """The new roadmap step's check: two themes, the same template, each element's colours and fonts as the theme says, measured
    on the pixels and in ffmpeg-skill's own report."""
    _setup(workspace)
    out = {}
    for name in ("a", "b"):
        doc = request_doc(DOC_ELEMENTS, video="gray.mp4", output=f"out/{name}.mp4",
                          options={"themes": {"a": "themes/a.json", "b": "themes/b.json"}, "theme": name, "preset": "ultrafast"})
        res = _executor(skill_dir, workspace).response(doc)
        assert res["ok"] and res["theme"]["label"] == name and res["warnings"] == []
        out[name] = res
    spec = {"a": THEME_A, "b": THEME_B}
    for name, res in out.items():
        mine, other = spec[name]["colors"], spec["b" if name == "a" else "a"]["colors"]
        f_lt = _frame(res["output"]["path"], 1.0)
        # the lower third's accent strip (left edge of its bar, bottom-left) is the theme's primary
        assert _count_near(f_lt, mine["primary"], 40, 240, 80, 340) >= 20, name
        assert _count_near(f_lt, other["primary"], 40, 240, 80, 340) < 5, name
        # its name is drawn in the theme's text colour
        assert _count_near(f_lt, mine["text"], 40, 240, 340, 340) >= 20, name
        f_title = _frame(res["output"]["path"], 2.3)
        assert _count_near(f_title, mine["text"], 120, 120, 520, 240) >= 30, name           # the title text
        assert _count_near(f_title, other["text"], 120, 120, 520, 240) < 5, name
        logo_rgb = f_lt[3 * (48 * 640 + 592): 3 * (48 * 640 + 592) + 3]                     # top-right, 16 px margin, 64 px wide
        assert (logo_rgb[0] > 180 and logo_rgb[2] < 80) if name == "a" else (logo_rgb[2] > 180 and logo_rgb[0] < 80), (name, tuple(logo_rgb))
        # fonts: what was sent, and what ffmpeg-skill says it drew with (when the checkout reports it)
        ops = {op["element_id"]: op for op in res["operations"]}
        for eid in ("lt", "t"):
            sent = " ".join(ops[eid]["tool_commands_observed"])
            assert ops[eid]["parameters"]["font"]["kind"] == ("system" if name == "a" else "file")
            used = ops[eid].get("font_used")
            if used is not None:
                assert (used.get("family") == "DejaVu Serif") if name == "a" else (used.get("file") == "font.ttf"), (name, used)
            else:  # an older checkout reports no font_used: the ffmpeg command it ran names the font
                assert (("DejaVu Serif" in sent) or ("DejaVuSerif" in sent)) if name == "a" else ("font.ttf" in sent), (name, sent[:500])
    assert out["a"]["output"]["sha256"] != out["b"]["output"]["sha256"]


def test_a_low_contrast_theme_warns(skill_dir, workspace):
    _setup(workspace)
    ex = _executor(skill_dir, workspace)
    ex.dry_run = True
    res = ex.response(request_doc([lower_third()], video="gray.mp4", output="out/fog.mp4", options={"themes": {"fog": "themes/fog.json"}}))
    low = [c for c in res["theme"]["contrast"] if not c["ok"]]
    assert {(c["foreground"], c["background"]) for c in low} >= {("text", "background"), ("primary", "background")}
    assert len(res["warnings"]) == len(low) and all("low contrast" in w for w in res["warnings"])


def test_a_logo_element_needs_a_theme_with_a_logo(skill_dir, workspace):
    _setup(workspace)
    ex = _executor(skill_dir, workspace)
    ex.dry_run = True
    for options in (None, {"themes": {"fog": "themes/fog.json"}}):
        with pytest.raises(MotionGraphicsError) as e:
            ex.response(request_doc([logo()], video="gray.mp4", output="out/l.mp4", options=options))
        assert e.value.details["reason"] == "theme_without_logo"
