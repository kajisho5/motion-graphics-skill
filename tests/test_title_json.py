"""rendered_layer reads the clip's title.json (docs/decisions.md ADR-33, roadmap step 12), and a refused slide / move / scale /
opacity_keyframes animation names rendered_layer as the way. Unit tests for the mapping, the precedence and the refusals; a real
render places a lower third from its title.json alone and compares it, frame by frame, with the same layer placed from the
numbers given by hand."""
import hashlib
import json
import subprocess

import pytest

from motion_graphics.contract import skill_contract
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.model import UNSUPPORTED_ANIMATIONS, parse_request
from motion_graphics.security import PathPolicy
from motion_graphics.titlejson import map_title, resolve_title_json

from conftest import request_doc, text_overlay_element, run_cli, one_json
from test_integration import _executor
from test_rendered_layer import _needs_place, _rgb, layer_element


# ---- a refused animation names the way
@pytest.mark.parametrize("kind", sorted(UNSUPPORTED_ANIMATIONS))
def test_a_refused_animation_names_rendered_layer(kind):
    d = request_doc([{**text_overlay_element(start=0, end=4), "animation": {"kind": kind, "parameters": {}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION" and "rendered_layer" in e.value.message


def test_a_slide_on_a_template_element_names_rendered_layer_too():
    lower_third = {"id": "lt1", "type": "lower_third", "start": 0.0, "end": 3.0, "parameters": {"name": "Ada"}}
    d = request_doc([{**lower_third, "animation": {"kind": "slide", "parameters": {}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION" and "rendered_layer" in e.value.message


def test_the_contract_says_it():
    c = skill_contract()
    assert all("rendered_layer" in u["reason"] for u in c["unsupported_animations"])
    lt = {t["type"]: t for t in c["element_types"]}["rendered_layer"]
    assert lt["parameters"]["title_json"] == {"type": "path", "required": False}
    # the parameters already pinned are unchanged (additive)
    assert lt["parameters"]["in_duration"] == {"type": "number", "required": False, "min": 0.0, "max": 60.0, "default": 0.0}
    assert lt["parameters"]["loop"] == {"type": "boolean", "required": False, "default": True}
    assert c["title_json"]["file_schema"] == "velisa/title@1"
    assert c["title_json"]["mapping"] == {"segments.in": "in_duration", "segments.out": "out_duration", "segments.loop > 0": "loop (true; 0 gives false)"}
    assert "title_json" not in c["response"]["conditional"]  # documented by title_json.result, as kit.result does for response.kit


# ---- the request
def test_with_title_json_the_segment_parameters_are_not_defaulted():
    el = parse_request(request_doc([layer_element(title_json="layer.title.json")])).elements[0]
    assert el.parameters == {"video_path": "layer.mov", "position": "center", "margin": 24, "opacity": 1.0, "title_json": "layer.title.json"}
    el = parse_request(request_doc([layer_element(title_json="layer.title.json", in_duration=0.25)])).elements[0]
    assert el.parameters["in_duration"] == 0.25 and "out_duration" not in el.parameters and "loop" not in el.parameters


def test_validate_touches_no_files():
    # validate is structural only: a title_json that does not exist is not read
    code, out, _ = run_cli(["validate", "-", "--json"], stdin_text=json.dumps(request_doc([layer_element(title_json="missing.title.json")])))
    assert code == 0 and one_json(out)["ok"] is True


# ---- the file
def _sidecar(**over):
    """A title.json in the shape hyperframes-skill 0.9.0 `render --segments` writes (lib/sidecar.mjs buildSidecar)."""
    doc = {"schema": "velisa/title@1", "file": "layer.mov", "frame_rate": "24/1", "alpha": "straight",
           "segments": {"in": 0.5, "loop": 2, "out": 0.5}, "segment_frames": {"in": 12, "loop": 48, "out": 12},
           "loop_seam": {"measured": True}, "safe_area": {"x": 0, "y": 0, "width": 64, "height": 36},
           "text": [{"role": "name", "text": "Ada Lovelace", "lang": "en", "font": "sans-serif", "font_size": 56}]}
    doc.update(over)
    return {k: v for k, v in doc.items() if v is not None}


def _resolve(tmp_path, doc, params=None, layer_sha="ab" * 32, duration=3.0, fps=24.0):
    (tmp_path / "layer.title.json").write_text(json.dumps(doc), encoding="utf-8")
    p = {"video_path": "layer.mov", "title_json": "layer.title.json", **(params or {})}
    return resolve_title_json(PathPolicy(str(tmp_path)), "lyr1", p, tmp_path / "layer.mov", layer_sha, duration, fps)


def test_segments_map_onto_the_parameters(tmp_path):
    params, record, warnings = _resolve(tmp_path, _sidecar())
    assert (params["in_duration"], params["out_duration"], params["loop"]) == (0.5, 0.5, True)
    assert record["sources"] == {"in_duration": "title_json", "out_duration": "title_json", "loop": "title_json"}
    assert record["layer_matched_by"] == "file" and record["alpha"] == "straight" and record["schema"] == "velisa/title@1"
    assert record["file"]["sha256"] == hashlib.sha256((tmp_path / "layer.title.json").read_bytes()).hexdigest()
    assert warnings == []


def test_a_zero_loop_plays_the_middle_once(tmp_path):
    params, _, _ = _resolve(tmp_path, _sidecar(segments={"in": 1.0, "loop": 0, "out": 2.0}))
    assert params["loop"] is False


def test_a_value_the_request_states_wins(tmp_path):
    params, record, warnings = _resolve(tmp_path, _sidecar(), {"in_duration": 0.25, "loop": True})
    assert (params["in_duration"], params["out_duration"], params["loop"]) == (0.25, 0.5, True)
    assert record["sources"] == {"in_duration": "request", "out_duration": "title_json", "loop": "request"}
    assert len(warnings) == 1 and "in_duration 0.25" in warnings[0] and "0.5" in warnings[0]


def test_the_sha256_ties_the_file_to_the_clip(tmp_path):
    _, record, _ = _resolve(tmp_path, _sidecar(sha256="cd" * 32, file="renamed.mov"), layer_sha="cd" * 32)
    assert record["layer_matched_by"] == "sha256"
    with pytest.raises(MotionGraphicsError) as e:
        _resolve(tmp_path, _sidecar(sha256="cd" * 32))
    assert e.value.code == "INVALID_INPUT" and e.value.details["reason"] == "title_json_stale"


def test_no_sha256_and_no_file_is_checked_by_length_and_warned(tmp_path):
    _, record, warnings = _resolve(tmp_path, _sidecar(file=None))
    assert record["layer_matched_by"] is None and any("neither sha256 nor file" in w for w in warnings)


@pytest.mark.parametrize("doc, reason", [
    (_sidecar(file="other.mov"), "title_json_other_file"),
    (_sidecar(alpha="premultiplied"), "title_json_premultiplied_alpha"),
    (_sidecar(alpha="key"), "bad_title_json"),
    (_sidecar(schema="velisa/stinger@1"), "not_title_json"),
    (_sidecar(segments=None), "title_json_without_segments"),
    (_sidecar(segments={"in": 0.5, "out": 0.5}), "title_json_without_segments"),
    (_sidecar(segments={"in": -1, "loop": 2, "out": 0.5}), "bad_title_json"),
    (_sidecar(segments={"in": 61, "loop": 0, "out": 0}), "bad_title_json"),
    (_sidecar(segments={"in": 0.5, "loop": 1, "out": 0.5}), "title_json_segments_mismatch"),
    ([1, 2], "bad_title_json"),
])
def test_a_title_json_that_does_not_describe_the_clip_is_refused(tmp_path, doc, reason):
    with pytest.raises(MotionGraphicsError) as e:
        _resolve(tmp_path, doc)
    assert e.value.code == "INVALID_INPUT" and e.value.details["reason"] == reason


def test_one_frame_of_slack_in_the_length(tmp_path):
    _resolve(tmp_path, _sidecar(), duration=3.0 + 1 / 24)
    with pytest.raises(MotionGraphicsError):
        _resolve(tmp_path, _sidecar(), duration=3.0 + 2 / 24)


def test_not_json_and_too_big_are_refused(tmp_path):
    (tmp_path / "layer.title.json").write_text("{", encoding="utf-8")
    pol = PathPolicy(str(tmp_path))
    p = {"video_path": "layer.mov", "title_json": "layer.title.json"}
    with pytest.raises(MotionGraphicsError) as e:
        resolve_title_json(pol, "lyr1", p, tmp_path / "layer.mov", "ab" * 32, 3.0, 24.0)
    assert e.value.details["reason"] == "bad_title_json"
    (tmp_path / "layer.title.json").write_text(" " * (256 * 1024 + 1), encoding="utf-8")
    with pytest.raises(MotionGraphicsError) as e:
        resolve_title_json(pol, "lyr1", p, tmp_path / "layer.mov", "ab" * 32, 3.0, 24.0)
    assert e.value.details["reason"] == "bad_title_json"


def test_map_title_keeps_only_what_it_reads():
    assert map_title(_sidecar(), "x") == {"schema": "velisa/title@1", "segments": {"in": 0.5, "loop": 2.0, "out": 0.5}, "file": "layer.mov", "alpha": "straight"}


# ---- real renders
# A lower third as hyperframes-skill renders title material: ProRes 4444 at 24 fps, 3 s, IN 12 frames, LOOP 48, OUT 12 (whole
# frames, as its segments are). IN red, OUT blue, LOOP green in four steps of 0.5 s; opaque on the left half only.
GEQ = ("geq=r='if(lt(T,0.5),230,0)':g='if(lt(T,0.5),0,if(lt(T,2.5),40+50*floor((T-0.5)*2),0))':"
       "b='if(gte(T,2.5),230,0)':a='if(lt(X,32),255,0)'")


def _make_lower_third(path):
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x36:r=24:d=3",
                    "-vf", f"format=rgba,{GEQ}", "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", str(path)], check=True)


def _frames_md5(path):
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert out
    return hashlib.md5(out).hexdigest()


def test_placed_from_title_json_alone_matches_the_numbers_by_hand(skill_dir, workspace):
    """Roadmap step 12's check: the lower third placed from its title.json alone shows the in, the looped middle and the out at the
    same frames as with in_duration / out_duration / loop given by hand."""
    layer = workspace / "lt.mov"
    _make_lower_third(layer)
    sidecar = _sidecar(file="lt.mov", sha256=hashlib.sha256(layer.read_bytes()).hexdigest())
    (workspace / "lt.title.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    from_file = request_doc([layer_element(video_path="lt.mov", position="top-left", margin=0, title_json="lt.title.json")], output="out/from_file.mp4")
    by_hand = request_doc([layer_element(video_path="lt.mov", position="top-left", margin=0, in_duration=0.5, out_duration=0.5, loop=True)],
                          output="out/by_hand.mp4")
    if not _needs_place(skill_dir):
        with pytest.raises(MotionGraphicsError) as e:
            _executor(skill_dir, workspace).response(from_file)
        assert e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
        return
    plan = _executor(skill_dir, workspace)
    plan.dry_run = True
    planned = plan.response(from_file)
    assert planned["title_json"]["lyr1"]["values"] == {"in_duration": 0.5, "out_duration": 0.5, "loop": True}
    a = _executor(skill_dir, workspace).response(from_file)
    b = _executor(skill_dir, workspace).response(by_hand)
    rec = a["title_json"]["lyr1"]
    assert rec["layer_matched_by"] == "sha256" and set(rec["sources"].values()) == {"title_json"} and "title_json" not in b
    seg_a, seg_b = a["operations"][0]["parameters"]["segments"], b["operations"][0]["parameters"]["segments"]
    assert seg_a == seg_b and seg_a["loop"] == {"source": [0.5, 2.5], "repeats": 2, "last_cut": 1.0}
    assert a["operations"][0]["parameters"]["title_json"] == rec["file"]   # identity: the file's content, not its path
    # the same frames, every one of them
    assert _frames_md5(a["output"]["path"]) == _frames_md5(b["output"]["path"])
    out = a["output"]["path"]
    opaque = "24:28:4:4"
    r, g, b_ = _rgb(out, 0.24, opaque)
    assert r > 180 and g < 60 and b_ < 60, (r, g, b_)                        # IN: red
    phase_a, phase_b, late = _rgb(out, 1.24, opaque), _rgb(out, 3.24, opaque), _rgb(out, 5.24, opaque)
    for px in (phase_a, phase_b, late):
        assert px[1] > px[0] + 20 and px[1] > px[2] + 20, px                 # LOOP: green, the same phase in each repeat
    assert abs(phase_a[1] - phase_b[1]) <= 10 and abs(phase_a[1] - late[1]) <= 10
    r, g, b_ = _rgb(out, 5.76, opaque)
    assert b_ > 180 and r < 60 and g < 60, (r, g, b_)                        # OUT: blue


def test_a_stale_title_json_is_refused_before_rendering(skill_dir, workspace):
    layer = workspace / "lt.mov"
    _make_lower_third(layer)
    (workspace / "lt.title.json").write_text(json.dumps(_sidecar(file="lt.mov", sha256="00" * 32)), encoding="utf-8")
    doc = request_doc([layer_element(video_path="lt.mov", title_json="lt.title.json")])
    with pytest.raises(MotionGraphicsError) as e:
        _executor(skill_dir, workspace).response(doc)
    if _needs_place(skill_dir):
        assert e.value.details["reason"] == "title_json_stale"
        assert not (workspace / "out" / "out.mp4").exists()
    else:
        assert e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
