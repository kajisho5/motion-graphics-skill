"""YouTube end-screen elements and card teasers as reserved regions (docs/decisions.md ADR-28). Unit tests for the request,
the default layouts and the element boxes; integration tests plan and render documents whose graphics sit under an end-screen
element and under a card teaser, and check what is reported."""
import pytest

from motion_graphics.contract import skill_contract
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.model import parse_request
from motion_graphics.regions import DEFAULT_END_SCREENS, aspect_class, element_boxes, overlaps, parse_reserved_regions, resolve_regions

from conftest import bug_element, request_doc, shape_element, title_element
from test_integration import _executor

W, H = 320, 180          # the fixture video
LONG = "Thanks for watching, see you next week"


def _plan(skill_dir, workspace, elements, regions):
    ex = _executor(skill_dir, workspace)
    ex.dry_run = True
    return ex.response(request_doc(elements, output="out/plan.mp4", options={"reserved_regions": regions}))


# ---- the request
@pytest.mark.parametrize("value", [
    {}, {"end_screen": {"duration": 3}}, {"end_screen": {"duration": 21}}, {"end_screen": {"layout": "grid"}},
    {"end_screen": {"layout": "default", "elements": [{"x": 0, "y": 0, "width": 10, "height": 10}]}},
    {"end_screen": {"elements": [{"x": 0, "y": 0, "width": 10}]}}, {"end_screen": {"elements": []}},
    {"cards": [{"duration": 3}]}, {"cards": [{"time": 1, "region": {"x": 0, "y": 0, "width": 0, "height": 5}}]},
    {"cards": [{"time": 1}] * 6}, {"end_screen": {}, "on_overlap": "move"}, {"end_screen": {}, "unknown": 1},
])
def test_bad_reserved_regions_are_refused(value):
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([title_element()], options={"reserved_regions": value}))
    assert e.value.code == "INVALID_REQUEST"


def test_defaults():
    spec = parse_reserved_regions({"end_screen": {}, "cards": [{"time": 2}]})
    assert spec["end_screen"] == {"duration": 20.0, "elements": None} and spec["cards"][0]["duration"] == 5.0 and spec["on_overlap"] == "report"


def test_default_layouts_per_aspect_stay_inside_the_frame():
    for (w, h) in ((1920, 1080), (1080, 1920), (1080, 1080), (320, 180)):
        regions = resolve_regions(parse_reserved_regions({"end_screen": {"duration": 10}, "cards": [{"time": 1}]}), w, h, 60.0)
        assert regions[0]["source"] == f"default layout {aspect_class(w, h)}"
        assert len([r for r in regions if r["kind"] == "end_screen"]) == len(DEFAULT_END_SCREENS[aspect_class(w, h)])
        for r in regions:
            x, y, bw, bh = r["box"]
            assert 0 <= x and 0 <= y and x + bw <= w and y + bh <= h, (w, h, r)
        assert regions[0]["start"] == 50.0 and regions[-1]["kind"] == "card_teaser" and regions[-1]["end"] == 6.0


def test_an_end_screen_longer_than_the_video_is_refused():
    with pytest.raises(MotionGraphicsError) as e:
        resolve_regions(parse_reserved_regions({"end_screen": {"duration": 10}}), W, H, 6.0)
    assert e.value.code == "INVALID_TIME_RANGE"


def test_element_boxes_exact_for_a_shape_and_estimated_for_templates():
    shape = parse_request(request_doc([shape_element(width=100, height=40, position="top-left", margin=10)])).elements[0]
    assert element_boxes(shape, W, H) == ([(10, 10, 100, 40)], "exact")
    bug = parse_request(request_doc([bug_element(position="top-right")])).elements[0]
    (x, y, w, h), = element_boxes(bug, W, H)[0]
    assert element_boxes(bug, W, H)[1] == "estimated" and x + w == W - 48 and y == 48


def test_scale_percent_wins_over_scale_width_as_in_the_renderer():
    from conftest import image_overlay_element
    el = parse_request(request_doc([image_overlay_element(position="top-left", margin=0, scale_width=200, scale_percent=25.0)])).elements[0]
    assert element_boxes(el, W, H, asset_size=(64, 32)) == ([(0, 0, 80, 40)], "exact")       # 25 % of 320, not 200
    only_width = parse_request(request_doc([image_overlay_element(position="top-left", margin=0, scale_width=200)])).elements[0]
    assert element_boxes(only_width, W, H, asset_size=(64, 32)) == ([(0, 0, 200, 100)], "exact")


def test_an_unmeasured_asset_has_no_box_and_meets_no_region():
    from conftest import image_overlay_element
    el = parse_request(request_doc([image_overlay_element(position="center")])).elements[0]
    assert element_boxes(el, W, H, asset_size=None) == ([], "unmeasured")
    els = [{"id": "img1", "type": "image_overlay", "start": 0.0, "end": 2.0, "boxes": [], "precision": "unmeasured"}]
    reg = [{"kind": "card_teaser", "id": "c", "box": (0, 0, W, H), "start": 0.0, "end": 2.0, "source": "input"}]
    assert overlaps(els, reg) == []


def test_an_unmeasured_element_is_reported_not_refused():
    from motion_graphics.executor import Executor
    from motion_graphics.security import PathPolicy
    doc = parse_request(request_doc([dict(title_element("t", start=0.0, end=1.0, title="x"))],
                                    options={"reserved_regions": {"cards": [{"time": 0.0, "region": {"x": 0, "y": 0, "width": 10, "height": 10}}],
                                                                  "on_overlap": "refuse"}}))
    ex = Executor(PathPolicy("."), skill=None)
    img = parse_request(request_doc([{"id": "img", "type": "video_overlay", "start": 0.0, "end": 1.0, "parameters": {"video_path": "v.mp4"}}])).elements[0]
    rep = ex._reserved_regions(doc, (img,), {"img": {"path": "v.mp4", "dims": (0, 0)}}, {}, W, H, 6.0)
    assert rep["unmeasured"] == ["img"] and rep["overlaps"] == []
    assert any("could not be measured" in w for w in Executor._region_warnings(rep))


def test_overlap_needs_both_time_and_space():
    els = [{"id": "a", "type": "title", "start": 0.0, "end": 2.0, "boxes": [(0, 0, 50, 50)], "precision": "estimated"}]
    reg = [{"kind": "card_teaser", "id": "c", "box": (40, 40, 20, 20), "start": 1.5, "end": 3.0, "source": "input"}]
    assert overlaps(els, reg)[0]["time"] == [1.5, 2.0] and overlaps(els, reg)[0]["area"] == [40, 40, 10, 10]
    assert overlaps(els, [dict(reg[0], start=2.0)]) == []
    assert overlaps(els, [dict(reg[0], box=(50, 0, 10, 10))]) == []


def test_contract_documents_reserved_regions():
    c = skill_contract()["reserved_regions"]
    assert c["request_field"] == "options.reserved_regions" and set(c["default_end_screens"]) == {"16:9", "9:16", "1:1"}
    success = skill_contract()["response"]["success"]
    assert skill_contract()["response"]["conditional"]["reserved_regions"]["in"] == ["run", "plan"] and "warnings" in success["plan"]
    assert "reserved_regions" not in success["run"] and "reserved_regions" not in success["plan"]   # not on every response
    assert "on_overlap?" in c["shape"]


# ---- the roadmap check: a title under an end-screen element, another under a card teaser; both reported
def test_a_title_under_the_end_screen_and_another_under_a_card_teaser_are_both_reported(skill_dir, workspace):
    elements = [title_element("t_end", start=2.0, end=4.0, title=LONG), title_element("t_card", start=0.0, end=0.9, title=LONG),
                title_element("t_clear", start=0.9, end=1.0, title="x")]
    regions = {"end_screen": {"duration": 5}, "cards": [{"id": "poll", "time": 0.2, "duration": 0.5}]}
    res = _executor(skill_dir, workspace).response(request_doc(elements, options={"reserved_regions": dict(regions, cards=[
        {"id": "poll", "time": 0.2, "duration": 0.5, "region": {"x": 100, "y": 70, "width": 120, "height": 40}}])}))
    assert res["ok"]
    found = {(o["element_id"], o["region"]["kind"]) for o in res["reserved_regions"]["overlaps"]}
    assert ("t_end", "end_screen") in found and ("t_card", "card_teaser") in found
    assert not any(e == "t_clear" for e, _ in found)                     # on screen at neither time
    card = next(o for o in res["reserved_regions"]["overlaps"] if o["element_id"] == "t_card")
    assert card["time"] == [0.2, 0.7] and card["region"]["source"] == "input"
    end = next(o for o in res["reserved_regions"]["overlaps"] if o["element_id"] == "t_end")
    assert end["time"] == [2.0, 4.0] and end["region"]["source"] == "default layout 16:9"
    assert len(res["warnings"]) == len(res["reserved_regions"]["overlaps"]) and all("overlaps the" in w for w in res["warnings"])
    # the default card area is the top-right corner: a plate there at the card's time is reported, the centred title is not
    plan = _plan(skill_dir, workspace, [shape_element("b1", position="top-right", margin=4, start=0.0, end=1.0), title_element("t", start=0.0, end=1.0)],
                 {"cards": [{"time": 0.2}]})
    assert [(o["element_id"], o["region"]["source"], o["precision"]) for o in plan["reserved_regions"]["overlaps"]] == [("b1", "default area top-right", "exact")]


def test_end_screen_positions_given_as_input_are_the_ones_checked(skill_dir, workspace):
    el = [title_element("t_end", start=2.0, end=4.0, title=LONG)]
    away = _plan(skill_dir, workspace, el, {"end_screen": {"duration": 5, "elements": [{"id": "v1", "x": 0, "y": 140, "width": 64, "height": 36}]}})
    assert away["reserved_regions"]["overlaps"] == [] and away["reserved_regions"]["regions"][0]["source"] == "input"
    over = _plan(skill_dir, workspace, el, {"end_screen": {"duration": 5, "elements": [{"id": "v1", "x": 120, "y": 70, "width": 80, "height": 45}]}})
    (o,) = over["reserved_regions"]["overlaps"]
    assert o["region"] == {"kind": "end_screen", "id": "v1", "box": [120, 70, 80, 45], "source": "input"}


def test_refuse_mode_refuses_before_anything_renders(skill_dir, workspace):
    doc = request_doc([title_element("t_end", start=2.0, end=4.0, title=LONG)], options={"reserved_regions": {"end_screen": {"duration": 5}, "on_overlap": "refuse"}})
    with pytest.raises(MotionGraphicsError) as e:
        _executor(skill_dir, workspace).response(doc)
    assert e.value.code == "INVALID_REQUEST" and e.value.details["reason"] == "reserved_region_overlap"
    assert e.value.details["overlaps"][0]["element_id"] == "t_end"
    assert not (workspace / "out" / "out.mp4").exists()
