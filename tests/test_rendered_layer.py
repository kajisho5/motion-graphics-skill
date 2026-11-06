"""The `rendered_layer` element (docs/decisions.md ADR-25): a pre-rendered clip with alpha placed by ffmpeg-skill/overlay
--video --place, with its IN / LOOP / OUT segments. Unit tests for the placement arithmetic, the request, the argv and the
checkout detection; integration tests render a layer whose segments have distinct colours and read the frames back."""
import subprocess
from types import SimpleNamespace

import pytest

from motion_graphics import adapter
from motion_graphics.contract import skill_contract
from motion_graphics.doctor import capability_status, element_type_status
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.layers import MAX_LAYER_PIECES, has_alpha, layer_placements, place_args
from motion_graphics.model import parse_request
from motion_graphics.security import PathPolicy

from conftest import request_doc, title_element
from test_integration import _executor, _skill_info


def layer_element(element_id="lyr1", start=0.0, end=6.0, video_path="layer.mov", **extra):
    return {"id": element_id, "type": "rendered_layer", "start": start, "end": end, "parameters": {"video_path": video_path, **extra}}


# ---- where the pieces go
def test_in_loop_out_fill_the_window_exactly():
    pieces, summary = layer_placements(0.0, 6.0, 3.0, 0.5, 0.5, True)
    assert pieces == [(0, 500, 0), (500, 2500, 500), (500, 2500, 2500), (500, 1500, 4500), (2500, 3000, 5500)]
    assert summary["loop"] == {"source": [0.5, 2.5], "repeats": 2, "last_cut": 1.0} and summary["pieces"] == 5
    # consecutive pieces touch: each starts where the previous one ends on the main timeline
    for (a, b, at), (_, _, nxt) in zip(pieces, pieces[1:]):
        assert at + (b - a) == nxt
    assert pieces[-1][2] + pieces[-1][1] - pieces[-1][0] == 6000


def test_place_args_are_millisecond_strings():
    assert place_args([(0, 500, 1250), (500, 2500, 1750)]) == ["--place", "0.000-0.500@1.250", "--place", "0.500-2.500@1.750"]


def test_no_segments_loops_the_whole_clip_and_a_short_window_cuts_it():
    assert layer_placements(1.0, 5.0, 1.5, 0, 0, True)[0] == [(0, 1500, 1000), (0, 1500, 2500), (0, 1000, 4000)]
    assert layer_placements(1.0, 2.0, 3.0, 0, 0, False)[0] == [(0, 1000, 1000)]


def test_loop_false_plays_the_middle_once_and_refuses_a_longer_window():
    assert layer_placements(0.0, 2.0, 3.0, 0.5, 0.5, False)[0] == [(0, 500, 0), (500, 1500, 500), (2500, 3000, 1500)]
    with pytest.raises(MotionGraphicsError) as e:
        layer_placements(0.0, 6.0, 3.0, 0.5, 0.5, False)
    assert e.value.code == "INVALID_TIME_RANGE" and e.value.details["reason"] == "layer_shorter_than_window"


def test_layer_duration_is_floored_so_no_piece_runs_past_the_clip():
    pieces, _ = layer_placements(0.0, 3.0, 2.9996, 0.5, 0.5, True)
    assert max(b for _, b, _ in pieces) == 2999


@pytest.mark.parametrize("args, code, reason", [
    ((0.0, 6.0, 1.0, 0.6, 0.6, True), "INVALID_REQUEST", "segments_longer_than_layer"),
    ((0.0, 0.8, 3.0, 0.5, 0.5, True), "INVALID_TIME_RANGE", "segments_longer_than_window"),
    ((0.0, 6.0, 1.0, 0.5, 0.5, True), "INVALID_REQUEST", "no_loop_segment"),
    ((0.0, 0.0001, 0.0, 0, 0, True), "INVALID_INPUT", "layer_without_duration"),
    ((0.0, 3600.0, 1.0, 0, 0, True), "INVALID_REQUEST", "too_many_pieces"),
])
def test_impossible_placements_are_refused(args, code, reason):
    with pytest.raises(MotionGraphicsError) as e:
        layer_placements(*args)
    assert e.value.code == code and e.value.details["reason"] == reason


def test_the_piece_bound_is_the_documented_one():
    assert len(layer_placements(0.0, float(MAX_LAYER_PIECES), 1.0, 0, 0, True)[0]) == MAX_LAYER_PIECES


@pytest.mark.parametrize("pix_fmt, alpha", [("yuva444p12le", True), ("yuva420p", True), ("rgba", True), ("argb", True), ("gbrap", True),
                                            ("yuv420p", False), ("yuv422p10le", False), ("pal8", False), (None, False)])
def test_alpha_pixel_formats(pix_fmt, alpha):
    assert has_alpha(pix_fmt) is alpha


# ---- the request and the argv
def test_rendered_layer_defaults():
    el = parse_request(request_doc([layer_element()])).elements[0]
    assert el.parameters == {"video_path": "layer.mov", "position": "center", "margin": 24, "opacity": 1.0, "in_duration": 0.0, "out_duration": 0.0, "loop": True}


def test_rendered_layer_takes_no_fade():
    el = layer_element()
    el["animation"] = {"kind": "fade", "parameters": {"duration": 0.3}}
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([el]))
    assert e.value.code == "UNSUPPORTED_OPERATION"


def test_the_overlay_call_places_each_piece_and_has_no_start_end():
    doc = parse_request(request_doc([layer_element(position="bottom-left", margin=0, scale_width=640, opacity=0.9, in_duration=0.5, out_duration=0.5)]))
    pieces, summary = layer_placements(0.0, 6.0, 3.0, 0.5, 0.5, True)
    asset = {"path": "/abs/layer.mov", "pieces": pieces, "segments": summary}
    tool, argv, _ = Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "in.mp4", "out.mp4", asset, None, crf=18, preset="medium")
    assert tool == "overlay"
    assert "--start" not in argv and "--end" not in argv and "--fade" not in argv
    assert argv[argv.index("--video") + 1] == "/abs/layer.mov" and argv.count("--place") == 5
    assert argv[argv.index("--scale") + 1] == "640" and argv[argv.index("--opacity") + 1] == "0.9"
    assert Executor._identity_parameters(doc.elements[0], dict(asset, sha256="ab", size=1), None)["segments"] == summary


# ---- the checkout
def test_check_tools_detects_overlay_place():
    base = {t: {"input_schema": {"properties": {f: {} for f in fl + ("quality",)}}} for t, fl in adapter.REQUIRED_FLAGS.items()}
    assert adapter.check_tools(base)[1]["place_flag"] is False
    base["overlay"]["input_schema"]["properties"]["place"] = {}
    assert adapter.check_tools(base)[1]["place_flag"] is True


def test_doctor_reports_rendered_layer_unsupported_without_place():
    info = SimpleNamespace(supported=True, template_support=lambda t: True, tool_support=lambda t: True, takes_place=lambda: False)
    st = element_type_status(capability_status(info, {"_unavailable": True}))["rendered_layer"]
    assert st["status"] == "unsupported" and st["missing"] == ["ffmpeg-skill-flag:overlay.place"]
    info.takes_place = lambda: True
    assert element_type_status(capability_status(info, {"_unavailable": True}))["rendered_layer"]["status"] != "unsupported"


def test_a_checkout_without_place_refuses_a_rendered_layer_before_rendering():
    info = SimpleNamespace(directory="/x", version="0.12.5", template_support=lambda t: True, tool_support=lambda t: True, takes_place=lambda: False)
    ex = Executor(PathPolicy("."), skill=SimpleNamespace(info=lambda: info))
    with pytest.raises(MotionGraphicsError) as e:
        ex._check_checkout(parse_request(request_doc([title_element(), layer_element(start=2, end=3)])))
    assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_flag" and e.value.retryable is False
    ex._check_checkout(parse_request(request_doc([title_element()])))


def test_contract_declares_rendered_layer():
    c = skill_contract()
    lt = {t["type"]: t for t in c["element_types"]}["rendered_layer"]
    assert (lt["tool"], lt["animation"]) == ("ffmpeg-skill/overlay", "none")
    assert "ffmpeg-skill-flag:overlay.place" in lt["required_capabilities"]
    assert "place" in c["ffmpeg_skill"]["optional_flags"]["overlay"] and "place" in c["ffmpeg_skill"]["flags_used"]["overlay"]
    assert {"id": "motion_graphics.rendered_layer", "lifecycle": "EXPERIMENTAL", "tool_id": "motion-graphics/run", "element_type": "rendered_layer"} in c["provides"]


# ---- real renders
# The layer: 64x36, 3 s at 25 fps, ProRes 4444. Opaque on its left half, transparent on its right half. IN [0, 0.5) is red,
# OUT [2.5, 3) is blue, and the LOOP [0.5, 2.5) is green in four steps of 0.5 s (G = 40, 90, 140, 190), so the loop's phase can
# be read off a frame.
LAYER_GEQ = ("geq=r='if(lt(T,0.5),230,0)':g='if(lt(T,0.5),0,if(lt(T,2.5),40+50*floor((T-0.5)*2),0))':"
             "b='if(gte(T,2.5),230,0)':a='if(lt(X,32),255,0)'")


def _make_layer(path, codec_args=("-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le")):
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x36:r=25:d=3",
                    "-vf", f"format=rgba,{LAYER_GEQ}", *codec_args, str(path)], check=True)


def _rgb(path, t, crop):
    """Mean R, G, B of a cropped region of the frame at time t."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"crop={crop},scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(out) == 3
    return tuple(out)


def _needs_place(skill_dir):
    return _skill_info(skill_dir).takes_place()


def test_in_loop_out_show_at_the_right_frames(skill_dir, workspace):
    """Roadmap step 7's check: a lower third with in 0.5 s / loop / out 0.5 s placed for 6 s shows the in, a looped middle and the
    out at the right frames."""
    _make_layer(workspace / "layer.mov")
    doc = request_doc([layer_element(position="top-left", margin=0, in_duration=0.5, out_duration=0.5)])
    if not _needs_place(skill_dir):
        with pytest.raises(MotionGraphicsError) as e:
            _executor(skill_dir, workspace).response(doc)
        assert e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
        return
    res = _executor(skill_dir, workspace).response(doc)
    out = res["output"]["path"]
    seg = res["operations"][0]["parameters"]["segments"]
    assert seg["loop"] == {"source": [0.5, 2.5], "repeats": 2, "last_cut": 1.0} and seg["pieces"] == 5
    opaque = "24:28:4:4"           # inside the layer's opaque half
    r, g, b = _rgb(out, 0.24, opaque)
    assert r > 180 and g < 60 and b < 60, (r, g, b)                         # IN: red
    phase_a = _rgb(out, 1.24, opaque)                                       # loop phase 0.74 s -> step 1 (G 90)
    phase_b = _rgb(out, 3.24, opaque)                                       # 2 s later: the same phase of the second repeat
    phase_c = _rgb(out, 4.74, opaque)                                       # loop phase 0.24 s -> step 0 (G 40)
    late = _rgb(out, 5.24, opaque)                                          # loop phase 0.74 s of the cut third repeat
    for px in (phase_a, phase_b, phase_c, late):
        assert px[1] > px[0] + 20 and px[1] > px[2] + 20, px                # LOOP: green
    assert abs(phase_a[1] - phase_b[1]) <= 10 and abs(phase_a[1] - late[1]) <= 10
    assert phase_a[1] - phase_c[1] >= 25, (phase_a, phase_c)                # one step (G 50 apart) later in the loop's phase
    r, g, b = _rgb(out, 5.76, opaque)
    assert b > 180 and r < 60 and g < 60, (r, g, b)                         # OUT: blue
    # the transparent half shows the video underneath, unchanged
    src = workspace / "video.mp4"
    for t in (0.24, 1.24, 5.76):
        assert max(abs(x - y) for x, y in zip(_rgb(out, t, "24:28:36:4"), _rgb(src, t, "24:28:36:4"))) <= 12


def test_an_opaque_clip_is_refused_as_a_layer(skill_dir, workspace):
    with pytest.raises(MotionGraphicsError) as e:
        _executor(skill_dir, workspace).response(request_doc([layer_element(video_path="video_short.mp4", end=2.0)]))
    if _needs_place(skill_dir):
        assert e.value.code == "INVALID_INPUT" and e.value.details["reason"] == "layer_without_alpha"
    else:
        assert e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
