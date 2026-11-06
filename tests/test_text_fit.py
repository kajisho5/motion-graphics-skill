"""Text fit for lower thirds and titles, and one lower third per CSV row (docs/decisions.md ADR-27). Unit tests for the width
table, the break rules and profiles, the fit search, the request and the CSV; an integration test renders a 10-row CSV with
English, Arabic and Japanese rows and measures that every lower third stays within its box."""
import json
import subprocess
import unicodedata
from pathlib import Path

import pytest

from motion_graphics.contract import skill_contract
from motion_graphics.csvrows import lower_thirds_document
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.model import parse_request
from motion_graphics.security import PathPolicy
from motion_graphics.textfit import CLOSE_PUNCT, KINSOKU_EXTRA, OPEN_PUNCT, direction, fit, text_em, wrap

from conftest import request_doc, run_cli, title_element
from test_integration import _executor, _skill_info

CSV = Path(__file__).resolve().parent / "fixtures" / "speakers.csv"
BOX = 300
FRAME = (640, 360)


def lt(element_id="lt1", name="Ada Lovelace", title=None, start=0.0, end=3.0, **fit_):
    params = {"name": name, **({"title": title} if title else {})}
    return {"id": element_id, "type": "lower_third", "start": start, "end": end, "parameters": params, "fit": {"width": BOX, **fit_}}


# ---- widths and breaks
def test_width_table_rounds_up_for_wide_and_narrow_glyphs():
    assert text_em("WWW") == 3.0 and text_em("漢字かな") == 4.0 and text_em("ab") == 1.3
    assert text_em("é") == text_em("e")        # a combining mark advances nothing


def test_lines_join_back_to_the_text():
    for text in ("Senior Research Scientist, Institute of Advanced Media Studies", "東京映像大学大学院（情報理工学系研究科）准教授",
                 "مدير قسم الوسائط الرقمية في المعهد الوطني", "García-Márquez/Director"):
        lines, forced = wrap(text, 8.0)
        assert not forced
        assert "".join(lines).replace(" ", "") == text.replace(" ", "")


def test_no_line_starts_with_closing_or_ends_with_opening_punctuation():
    text = "東京映像大学大学院（情報理工学系研究科）准教授「メディア論」担当です。"
    for max_em in (3.0, 4.0, 5.0, 6.0, 7.0):
        lines, _ = wrap(text, max_em)
        assert all(ln[0] not in CLOSE_PUNCT for ln in lines[1:]), lines
        assert all(ln[-1] not in OPEN_PUNCT for ln in lines[:-1]), lines


def test_the_kinsoku_profile_keeps_small_kana_and_the_long_mark_off_line_starts():
    text = "ちょっとしたフリーランス・ディレクターのショートフィルム"
    default_starts = set()
    for max_em in (3.0, 4.0, 5.0):
        lines, _ = wrap(text, max_em, "ja-kinsoku")
        assert all(ln[0] not in KINSOKU_EXTRA and ln[0] not in CLOSE_PUNCT for ln in lines[1:]), lines
        default_starts |= {ln[0] for ln in wrap(text, max_em)[0][1:]}
    assert default_starts & KINSOKU_EXTRA      # without the profile, such a break is allowed (UAX #14 normal strictness)


def test_alphabetic_words_are_not_broken_and_spaces_are_break_points():
    lines, forced = wrap("Office of Naval Research and Development", 10.0)
    assert not forced and lines == ["Office of Naval", "Research and", "Development"]


def test_an_unbreakable_run_is_broken_between_characters_only_as_a_last_resort():
    lines, forced = wrap("Supercalifragilisticexpialidocious", 6.0)
    assert forced and "".join(lines) == "Supercalifragilisticexpialidocious" and all(text_em(ln) <= 6.0 for ln in lines)


def test_direction_from_the_first_strong_character():
    assert direction("فاطمة الزهراء") == "rtl" and direction("Ada") == "ltr" and direction("123 שלום") == "rtl" and direction("山田") == "ltr"


# ---- the fit search
def test_a_short_name_fits_at_full_size_on_one_line():
    f = fit("lower_third", {"name": "Ada Lovelace", "title": "Analyst"}, FRAME, BOX, 2, 0.6, None)
    assert f["scale"] == 1.0 and f["fits"] and f["texts"]["name"]["lines"] == ["Ada Lovelace"] and f["texts"]["name"]["font_size"] == 19


def test_a_long_text_is_shrunk_first_then_broken_and_stays_in_the_box():
    f = fit("lower_third", {"name": "Grace Hopper", "title": "Office of Naval Research and Development"}, FRAME, BOX, 2, 0.6, None)
    assert f["fits"] and f["scale"] < 1.0
    for t in f["texts"].values():
        assert t["widest_px"] + 2 * f["pad"] <= BOX
    # too wide for one line even at min_scale 0.8: the largest size that fits on two lines
    g = fit("lower_third", {"name": "Dr. Maximilian Alexander Wolfgang von Hohenberg"}, FRAME, BOX, 2, 0.8, None)
    assert g["fits"] and g["texts"]["name"]["lines"] == ["Dr. Maximilian Alexander", "Wolfgang von Hohenberg"] and g["scale"] == 0.9


def test_too_much_text_is_reported_not_cut():
    long = "word " * 60
    f = fit("lower_third", {"name": long.strip()}, FRAME, 200, 2, 0.6, None)
    assert not f["fits"] and f["overflow"] == "more lines than max_lines" and f["scale"] == 0.6
    assert " ".join(f["texts"]["name"]["lines"]) == long.strip()


def test_title_uses_its_own_sizes():
    f = fit("title", {"title": "Episode 12", "subtitle": "The math of video"}, FRAME, 500, 2, 0.6, None)
    assert f["texts"]["title"]["font_size"] == int(360 * 0.11) and f["pad"] == 0


# ---- the request, the argv, the contract
def test_fit_is_validated_and_only_for_lower_third_and_title():
    el = parse_request(request_doc([lt(profile="ja-kinsoku", lang="ja")])).elements[0]
    assert el.fit == {"width": BOX, "max_lines": 2, "min_scale": 0.6, "profile": "ja-kinsoku", "lang": "ja"}
    assert parse_request(request_doc([dict(title_element(), fit={"width": 400})])).elements[0].fit["width"] == 400
    bad = [({"width": 8}, "INVALID_REQUEST"), ({"width": 300, "min_scale": 0.1}, "INVALID_REQUEST"), ({"width": 300, "profile": "zh"}, "INVALID_REQUEST"),
           ({"width": 300, "lang": "Japanese!"}, "INVALID_REQUEST"), ({"max_lines": 2}, "INVALID_REQUEST"), ({"width": 300, "shrink": True}, "INVALID_REQUEST")]
    for f, code in bad:
        with pytest.raises(MotionGraphicsError) as e:
            parse_request(request_doc([dict(lt(), fit=f)]))
        assert e.value.code == code, f
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([{"id": "b", "type": "bug", "start": 0, "end": 1, "parameters": {"title": "LIVE"}, "fit": {"width": 100}}]))
    assert e.value.code == "UNSUPPORTED_OPERATION"


def test_the_argv_carries_the_fitted_lines_and_scale():
    doc = parse_request(request_doc([lt(name="Grace Hopper", title="Office of Naval Research and Development", lang="en")]))
    el = doc.elements[0]
    f = fit("lower_third", {"name": el.parameters["name"], "title": el.parameters["title"]}, FRAME, BOX, 2, 0.6, None)
    argv = Executor(PathPolicy("."), skill=None)._argv(el, "in.mp4", "out.mp4", None, None, crf=18, preset="medium", fit=f)[1]
    assert argv[argv.index("--title") + 1] == "\n".join(f["texts"]["title"]["lines"])
    assert argv[argv.index("--scale") + 1] == f"{f['scale']:g}" and argv[argv.index("--lang") + 1] == "en"


def test_contract_documents_text_fit():
    c = skill_contract()["text_fit"]
    assert c["applies_to"] == ["lower_third", "title"] and c["fields"]["width"]["required"] is True
    assert "ja-kinsoku" in c["profiles"] and c["requires"].startswith("ffmpeg-skill/graphics --scale and --wrap")


# ---- the CSV
def test_the_csv_gives_one_fitted_lower_third_per_row():
    doc = lower_thirds_document(CSV.read_text(encoding="utf-8"), "video.mp4", "out/lt.mp4", fit_width=BOX, start=0.0, duration=1.0, gap=0.15)
    els = doc["elements"]
    assert len(els) == 10 and [e["id"] for e in els] == [f"lt{i:02d}" for i in range(1, 11)]
    assert {e["fit"].get("lang") for e in els} == {"en", "ar", "ja", "es"}
    assert [e["fit"].get("profile") for e in els].count("ja-kinsoku") == 2
    assert any(len(e["parameters"].get("title", "")) >= 40 for e in els)
    assert els[1]["start"] == pytest.approx(1.15) and els[9]["end"] == pytest.approx(11.35)


@pytest.mark.parametrize("text", ["title\nx\n", "name,colour\nAda,red\n", "id,title\nx,y\n"])
def test_bad_csvs_are_refused(text):
    with pytest.raises(MotionGraphicsError) as e:
        lower_thirds_document(text, "v.mp4", "o.mp4", fit_width=BOX)
    assert e.value.code == "INVALID_REQUEST"


def test_the_lower_thirds_command_prints_a_valid_request(tmp_path):
    code, out, _ = run_cli(["lower-thirds", str(CSV), "--video", "v.mp4", "--output", "o.mp4", "--fit-width", str(BOX), "--json"])
    assert code == 0
    doc = json.loads(out)
    assert len(parse_request(doc).elements) == 10


# ---- the render
def _gray_video(path, seconds=12):
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x808080:s=640x360:r=25:d={seconds}",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)], check=True)


def _changed_columns(path, t):
    """Leftmost and rightmost column of the lower half where the frame at t differs from the grey background."""
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "crop=640:180:0:180", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(raw) == 640 * 180 * 3
    cols = set()
    for y in range(180):
        row = raw[y * 1920:(y + 1) * 1920]
        for x in range(640):
            r, g, b = row[3 * x], row[3 * x + 1], row[3 * x + 2]
            if abs(r - 128) > 24 or abs(g - 128) > 24 or abs(b - 128) > 24:
                cols.add(x)
    return (min(cols), max(cols)) if cols else (None, None)


def test_a_ten_row_csv_renders_ten_lower_thirds_within_the_box(skill_dir, workspace):
    """Roadmap step 9's check: a 10-row CSV with English, Arabic and Japanese rows, one with a 40-character affiliation, renders
    10 lower thirds within the box; the Japanese rows with the kinsoku profile have no line starting with a closing bracket."""
    _gray_video(workspace / "gray.mp4")
    doc = lower_thirds_document(CSV.read_text(encoding="utf-8"), "gray.mp4", "out/lt.mp4", fit_width=BOX, start=0.0, duration=1.0, gap=0.15)
    doc["options"] = {"preset": "ultrafast", "reuse_intermediates": False}
    info = _skill_info(skill_dir)
    if not (info.takes_flag("graphics", "scale") and info.takes_flag("graphics", "wrap") and info.takes_flag("graphics", "lang")):
        with pytest.raises(MotionGraphicsError) as e:
            _executor(skill_dir, workspace).response(doc)
        assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
        return
    res = _executor(skill_dir, workspace).response(doc)
    assert res["ok"] and len(res["operations"]) == 10
    fits = {op["element_id"]: op["parameters"]["fit"] for op in res["operations"]}
    assert all(f["fits"] and not f["forced_break"] for f in fits.values())
    for el in doc["elements"]:
        f = fits[el["id"]]
        if el["fit"].get("profile") == "ja-kinsoku":
            for t in f["texts"].values():
                assert all(ln[0] not in CLOSE_PUNCT and ln[0] not in KINSOKU_EXTRA for ln in t["lines"][1:]), t["lines"]
        if el["fit"]["lang"] == "ar":
            assert all(t["direction"] == "rtl" for t in f["texts"].values())
        assert all(unicodedata.normalize("NFC", "".join(t["lines"]).replace(" ", "")) ==
                   unicodedata.normalize("NFC", el["parameters"][k].replace(" ", "")) for k, t in f["texts"].items())   # nothing cut
        left, right = _changed_columns(res["output"]["path"], el["start"] + 0.55)    # the bar is still: in done, out not begun
        assert left is not None, el["id"]
        assert right - left + 1 <= BOX + 2, (el["id"], left, right, f)
    assert any(f["scale"] < 1.0 for f in fits.values()) and any(len(t["lines"]) > 1 for f in fits.values() for t in f["texts"].values())
    assert res["warnings"] == []
