"""Layer export (docs/decisions.md ADR-30): beside the burned programme, every element drawn alone on a transparent canvas by the same
ffmpeg-skill call with --keep-alpha, one file per element and a manifest. Unit tests for the request option, the argv, the checkout
detection and the refusals; integration tests render a document with layers and composite them over the clean video with
ffmpeg-skill/overlay, which must give the burned programme back within the stated PSNR."""
import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from motion_graphics import adapter
from motion_graphics.adapter import FfmpegSkill
from motion_graphics.contract import skill_contract
from motion_graphics.doctor import capability_status
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import CANVAS_ALPHA_CODEC, Executor
from motion_graphics.layerexport import (LAYER_EXPORT_FLAGS, LAYERS_MANIFEST_SCHEMA, alpha_step, layer_file_name, manifest_document,
                                         parse_layer_export)
from motion_graphics.model import parse_request
from motion_graphics.security import PathPolicy

from conftest import image_overlay_element, request_doc, shape_element, text_overlay_element, title_element
from test_integration import _executor, _skill_info

# The stated tolerance (ADR-30): the layers laid over the clean video with ffmpeg-skill/overlay give the burned programme back with an
# average PSNR of at least this over the whole programme. Both pictures are separate H.264 encodes of the same frames; the residue is the
# 4:2:0 conversion of each layer's anti-aliased edges at composite time, which the burn does in one pass.
MIN_PSNR_DB = 30.0


def lower_third_element(element_id="lt1", start=0.5, end=3.5, name="Ada Lovelace", **extra):
    return {"id": element_id, "type": "lower_third", "start": start, "end": end, "parameters": {"name": name, **extra}}


def _options(**layer_export):
    return {"layer_export": dict({"directory": "layers"}, **layer_export)}


# ---- the request option
def test_defaults_and_fields():
    doc = parse_request(request_doc([title_element()], options=_options()))
    assert doc.options["layer_export"] == {"directory": "layers", "alpha_codec": "prores4444", "alpha_mode": "straight"}
    doc = parse_request(request_doc([title_element()], options=_options(alpha_codec="qtrle", alpha_mode="premultiplied")))
    assert doc.options["layer_export"]["alpha_codec"] == "qtrle" and doc.options["layer_export"]["alpha_mode"] == "premultiplied"
    assert parse_request(request_doc([title_element()])).options["layer_export"] is None


@pytest.mark.parametrize("value, field", [
    ({"directory": "layers", "alpha_codec": "h264"}, "alpha_codec"),
    ({"directory": "layers", "alpha_mode": "linear"}, "alpha_mode"),
    ({"directory": ""}, "directory"),
    ({"alpha_codec": "qtrle"}, "directory"),
    ({"directory": 3}, "directory"),
])
def test_bad_values_are_refused(value, field):
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([title_element()], options={"layer_export": value}))
    assert e.value.code == "INVALID_REQUEST" and e.value.details["field"].endswith(field)


def test_unknown_field_and_non_object_are_refused():
    for value in ({"directory": "layers", "codec": "qtrle"}, "layers", ["layers"]):
        with pytest.raises(MotionGraphicsError) as e:
            parse_request(request_doc([title_element()], options={"layer_export": value}))
        assert e.value.code == "INVALID_REQUEST"


def test_ids_differing_only_in_case_are_refused_for_a_layer_export():
    els = [title_element("Intro"), title_element("intro", start=2.0, end=3.0)]
    parse_request(request_doc(els))  # fine without a layer export
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc(els, options=_options()))
    assert e.value.details["reason"] == "layer_name_collision" and e.value.details["ids"] == ["Intro", "intro"]
    with pytest.raises(MotionGraphicsError):
        parse_layer_export({"directory": "x"}, ["A.b", "a.B"])


def test_file_names_follow_the_codec_container():
    assert layer_file_name("t1", "prores4444") == "t1.mov" and layer_file_name("t1", "qtrle") == "t1.mov"
    assert layer_file_name("t1", "png") == "t1.mov" and layer_file_name("t1", "vp9") == "t1.webm"


# ---- the argv
def _argv(element, alpha=("prores4444", "straight"), audio_stream=1, platform=None):
    doc = parse_request(request_doc([element]))
    asset = {"path": "/abs/logo.png"} if element["type"] in ("image_overlay", "shape") else None
    return Executor(PathPolicy("."), skill=None)._argv(doc.elements[0], "/abs/canvas.mov", "/abs/layers/x.mov", asset, None, 18, "medium",
                                                       audio_stream, platform, alpha=alpha)


def _without(argv, flags):
    """argv with the given flags removed, each with its number of values."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] in flags:
            i += 1 + flags[argv[i]]
            continue
        out.append(argv[i])
        i += 1
    return out


@pytest.mark.parametrize("element", [title_element(), lower_third_element(), text_overlay_element(), image_overlay_element()])
def test_a_layer_is_the_same_call_with_the_alpha_flags(element):
    tool, argv, _ = _argv(element, platform="youtube")
    _, burned, _ = _argv(element, alpha=None, platform="youtube")
    assert argv[argv.index("--alpha-codec") + 1] == "prores4444" and argv[argv.index("--alpha-mode") + 1] == "straight" and "--keep-alpha" in argv
    # no x264 settings and no audio track choice (the canvas has no audio); everything else is the burn's argv
    for flag in ("--crf", "--quality", "--preset", "--audio-stream"):
        assert flag not in argv
    assert "--audio-stream" in burned and "--preset" in burned
    assert _without(argv, {"--keep-alpha": 0, "--alpha-codec": 1, "--alpha-mode": 1}) == _without(burned, {"--crf": 1, "--preset": 1, "--audio-stream": 1})
    assert argv[0] == "/abs/canvas.mov" and argv[argv.index("-o") + 1] == "/abs/layers/x.mov"


def test_alpha_mode_and_codec_are_passed_through():
    _, argv, _ = _argv(text_overlay_element(), alpha=("qtrle", "premultiplied"))
    assert argv[argv.index("--alpha-codec") + 1] == "qtrle" and argv[argv.index("--alpha-mode") + 1] == "premultiplied"


# ---- the checkout
def _tools(**extra):
    tools = {t: {"input_schema": {"properties": {f: {} for f in fl + ("quality",)}}} for t, fl in adapter.REQUIRED_FLAGS.items()}
    tools["background"] = {"input_schema": {"properties": {f: {} for f in adapter.GENERATOR_TOOLS["background"]}}}
    for tool, props in extra.items():
        tools[tool]["input_schema"]["properties"].update(props)
    return tools


def test_check_tools_detects_the_alpha_flags():
    feature = adapter.check_tools(_tools())[1]["layer_export"]
    assert feature["supported"] is False and "background.transparent" in feature["missing"] and "graphics.keep_alpha" in feature["missing"]
    codecs = {"enum": ["png", "prores4444", "qtrle", "vp9"]}
    full = {tool: {f: (codecs if f == "alpha_codec" else {}) for f in flags} for tool, flags in LAYER_EXPORT_FLAGS.items()}
    feature = adapter.check_tools(_tools(**full))[1]["layer_export"]
    assert feature == {"supported": True, "missing": [], "alpha_codecs": ["png", "prores4444", "qtrle", "vp9"]}
    # a tool offering fewer codecs narrows the list; a tool missing one flag makes the feature unsupported
    full["overlay"]["alpha_codec"] = {"enum": ["prores4444", "qtrle"]}
    assert adapter.check_tools(_tools(**full))[1]["layer_export"]["alpha_codecs"] == ["prores4444", "qtrle"]
    del full["graphics"]["keep_alpha"]
    assert adapter.check_tools(_tools(**full))[1]["layer_export"] == {"supported": False, "missing": ["graphics.keep_alpha"], "alpha_codecs": None}


def test_alpha_codec_choices_are_read_from_help():
    props = adapter.flags_from_help("usage: graphics.py [--keep-alpha] [--alpha-codec {png,prores4444,qtrle,vp9}]\n")
    assert props["keep_alpha"] == {} and props["alpha_codec"] == {"enum": ["png", "prores4444", "qtrle", "vp9"]}


def _info(supported=True, codecs=("png", "prores4444", "qtrle", "vp9")):
    feature = {"supported": supported, "missing": [] if supported else ["background.transparent", "graphics.keep_alpha"],
               "alpha_codecs": list(codecs) if supported else None}
    return SimpleNamespace(directory="/x", version="3.2.2", template_support=lambda t: True, tool_support=lambda t: True, takes_place=lambda: True,
                           takes_overwrite=lambda tool: True, takes_platform=lambda tool: True, takes_flag=lambda tool, flag: True,
                           layer_export=lambda: dict(feature), supported=True)


def test_an_old_checkout_refuses_a_layer_export_before_rendering():
    ex = Executor(PathPolicy("."), skill=SimpleNamespace(info=lambda: _info(False)))
    with pytest.raises(MotionGraphicsError) as e:
        ex._check_checkout(parse_request(request_doc([title_element()], options=_options())))
    assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_flag" and e.value.retryable is False
    assert e.value.details["flags"] == ["background.transparent", "graphics.keep_alpha"]
    assert "ffmpeg-skill/background --transparent" in e.value.message and "ffmpeg-skill/graphics --keep-alpha" in e.value.message
    ex._check_checkout(parse_request(request_doc([title_element()])))  # without the option nothing changes


def test_a_codec_the_checkout_does_not_offer_is_refused():
    ex = Executor(PathPolicy("."), skill=SimpleNamespace(info=lambda: _info(codecs=("prores4444", "qtrle"))))
    with pytest.raises(MotionGraphicsError) as e:
        ex._check_checkout(parse_request(request_doc([title_element()], options=_options(alpha_codec="vp9"))))
    assert e.value.details["reason"] == "ffmpeg_skill_lacks_alpha_codec" and e.value.details["alpha_codec"] == "vp9"
    ex._check_checkout(parse_request(request_doc([title_element()], options=_options(alpha_codec="qtrle"))))
    # the canvas is always drawn in CANVAS_ALPHA_CODEC, so a checkout without it is refused whatever the layers' codec
    ex = Executor(PathPolicy("."), skill=SimpleNamespace(info=lambda: _info(codecs=("qtrle",))))
    with pytest.raises(MotionGraphicsError) as e:
        ex._check_checkout(parse_request(request_doc([title_element()], options=_options(alpha_codec="qtrle"))))
    assert e.value.details["alpha_codec"] == CANVAS_ALPHA_CODEC


def test_doctor_reports_the_feature():
    assert capability_status(_info(True), {"_unavailable": True})["ffmpeg-skill-feature:layer_export"] == "supported"
    assert capability_status(_info(False), {"_unavailable": True})["ffmpeg-skill-feature:layer_export"] == "unsupported"
    assert capability_status(None, {})["ffmpeg-skill-feature:layer_export"] == "unsupported"


# ---- where the files go
def test_existing_layer_files_need_overwrite(tmp_path):
    (tmp_path / "layers").mkdir()
    (tmp_path / "layers" / "t1.mov").write_bytes(b"x")
    ex = Executor(PathPolicy(str(tmp_path)), skill=None)
    doc = parse_request(request_doc([title_element()], options=_options()))
    with pytest.raises(MotionGraphicsError) as e:
        ex._layer_paths(doc, tmp_path / "video.mp4", tmp_path / "out.mp4")
    assert e.value.code == "OUTPUT_ERROR" and e.value.details["reason"] == "exists"
    paths = ex._layer_paths(parse_request(request_doc([title_element()], overwrite=True, options=_options())), tmp_path / "video.mp4", tmp_path / "out.mp4")
    assert paths["files"]["t1"] == (tmp_path / "layers" / "t1.mov").resolve() and paths["manifest"].name == "manifest.json"


def test_a_layer_file_may_not_be_the_programme_or_leave_the_workspace(tmp_path):
    ex = Executor(PathPolicy(str(tmp_path)), skill=None)
    doc = parse_request(request_doc([title_element()], output="layers/t1.mov", options=_options()))
    with pytest.raises(MotionGraphicsError) as e:
        ex._layer_paths(doc, tmp_path / "video.mp4", (tmp_path / "layers" / "t1.mov").resolve())
    assert e.value.details["reason"] == "layer_is_output"
    with pytest.raises(MotionGraphicsError) as e:
        ex._layer_paths(parse_request(request_doc([title_element()], options={"layer_export": {"directory": "../elsewhere"}})),
                        tmp_path / "video.mp4", tmp_path / "out.mp4")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_a_failed_layer_leaves_no_earlier_manifest_behind(tmp_path):
    """With overwrite, the manifest of an earlier run goes before the first layer is drawn: a failure part-way leaves no manifest that
    describes layer files which were removed or rewritten since."""
    (tmp_path / "layers").mkdir()
    (tmp_path / "layers" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "layers" / "t1.mov").write_bytes(b"old")

    def fail(*a, **k):
        raise MotionGraphicsError("TOOL_ERROR", "drawing failed", {"reason": "tool_failed"})
    skill = SimpleNamespace(info=lambda: _info(), run_tool=fail)
    ex = Executor(PathPolicy(str(tmp_path)), skill=skill)
    ex._canvas = lambda work_dir, params: {"path": str(tmp_path / "canvas.mov"), "record": {"parameters": params}}
    doc = parse_request(request_doc([title_element()], overwrite=True, options=_options()))
    paths = ex._layer_paths(doc, tmp_path / "video.mp4", tmp_path / "out.mp4")
    with pytest.raises(MotionGraphicsError):
        ex._export_layers(doc, doc.ordered_elements(), paths, {}, {}, {}, 320, 180, 6.0, 25.0, tmp_path, ".mp4", 18, "medium", None,
                          tmp_path / "out.mp4", {"sha256": "x", "duration": 6.0})
    assert not (tmp_path / "layers" / "manifest.json").exists() and not (tmp_path / "layers" / "t1.mov").exists()


# ---- the manifest and the contract
def test_manifest_document_shape():
    layer = {"id": "t1", "type": "title", "start": 0.0, "end": 2.0, "box": {"x": 1, "y": 2, "width": 3, "height": 4}, "box_precision": "estimated",
             "file": "t1.mov", "path": "/abs/layers/t1.mov", "sha256": "ab", "size": 10, "duration": 6.0, "alpha_codec": "prores4444",
             "alpha_mode": "straight", "verified": True, "alpha": {"step": "alpha", "ok": True}, "seconds": 1.0, "tool_commands_observed": ["x"]}
    m = manifest_document({"file": "out.mp4"}, {"alpha_codec": "prores4444", "alpha_mode": "straight"}, {"width": 320}, [layer])
    assert m["schema"] == LAYERS_MANIFEST_SCHEMA and m["verified"] is True
    assert "path" not in m["layers"][0] and "tool_commands_observed" not in m["layers"][0] and m["layers"][0]["file"] == "t1.mov"
    assert manifest_document({}, {"alpha_codec": "qtrle", "alpha_mode": "straight"}, {}, [dict(layer, verified=None)])["verified"] is False


def test_alpha_step_is_read_from_the_tool_result():
    assert alpha_step({"verification": [{"step": "probe", "ok": True}, {"step": "alpha", "ok": False, "reason": "no_alpha_plane"}]})["reason"] == "no_alpha_plane"
    assert alpha_step({"verification": [{"step": "probe", "ok": True}]}) is None and alpha_step(None) is None


def test_contract_declares_the_layer_export():
    c = skill_contract()
    assert c["response"]["conditional"]["layer_export"] == {"in": ["run", "plan"], "when": "options.layer_export is given", "shape": "see layer_export.result"}
    assert c["layer_export"]["request_field"] == "options.layer_export"
    assert c["layer_export"]["requires"]["ffmpeg_skill_flags"]["background"] == ["transparent", "fps", "alpha_codec", "alpha_mode"]
    assert "layer_export?" in c["request"]["shape"]["options?"]
    for tool in ("graphics", "overlay"):
        assert {"keep_alpha", "alpha_codec", "alpha_mode"} <= set(c["ffmpeg_skill"]["flags_used"][tool])
    # an option, not an element type: no element type, parameter type or provides entry changes
    assert all(t["type"] != "layer_export" for t in c["element_types"])


# ---- real renders
def _supported(skill_dir):
    return _skill_info(skill_dir).layer_export()["supported"]


def _psnr(a, b):
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "psnr", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.search(r"PSNR y:([\d.inf]+) u:([\d.inf]+) v:([\d.inf]+) average:([\d.inf]+) min:([\d.inf]+)", out)
    assert m, out[-2000:]
    return float(m.group(4))


def _psnr_by_half_second(a, b):
    """The mean per-frame PSNR of each half second (25 fps: 12-13 frames), reported with a failing PSNR."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(a), "-i", str(b), "-lavfi", "psnr=stats_file=-",
                          "-f", "null", "-"], capture_output=True, text=True).stdout
    frames = [float(m.group(1)) if m.group(1) != "inf" else 99.0 for m in re.finditer(r"psnr_avg:([\d.]+|inf)", out)]
    return [round(sum(frames[i:i + 12]) / len(frames[i:i + 12]), 1) for i in range(0, len(frames), 12)]


def _stream(path):
    """pix_fmt, range, matrix, primaries and transfer of a file's video stream (reported with a failing PSNR)."""
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=pix_fmt,color_range,color_space,color_primaries,color_transfer,start_pts,start_time,r_frame_rate,nb_frames", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True).stdout.strip()


def _rgba(path, t, crop):
    """Mean R, G, B, A of a cropped region of the frame at time t."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"format=rgba,crop={crop},scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    assert len(out) == 4
    return tuple(out)


def _composite(skill_dir, workspace, manifest):
    """The layers over the clean video, in the manifest's order, each with ffmpeg-skill/overlay at 0,0 (how an NLE stacks them).
    Returns every step's file: step i is the clean video with layers 0..i on it."""
    skill = FfmpegSkill(skill_dir)
    base, steps = workspace / "video.mp4", []
    for i, layer in enumerate(manifest["layers"]):
        out = workspace / f"comp{i}.mp4"
        skill.run_tool("overlay", [str(base), "--video", str(workspace / "layers" / layer["file"]), "--position", "top-left", "--margin", "0",
                                   "-o", str(out), "--overwrite"])
        steps.append(out)
        base = out
    return steps


def test_layers_composited_over_the_clean_video_give_the_programme_back(skill_dir, workspace):
    """Roadmap step 8's check: each element alone on transparent, the same timing, one file each and a manifest; the layers laid over the
    clean programme equal the burned programme within MIN_PSNR_DB."""
    elements = [title_element("t1", start=0.0, end=2.0, title="Layers"), lower_third_element("lt1", start=0.5, end=3.5),
                text_overlay_element("txt1", start=1.0, end=4.0, text="Hello", position="bottom-right"),
                image_overlay_element("img1", start=2.0, end=5.0, position="top-left", margin=8),
                shape_element("box1", start=3.0, end=5.5, position="top-right", margin=10)]
    elements[2]["animation"] = {"kind": "fade", "parameters": {"duration": 0.4}}
    doc = request_doc(elements, options=_options())
    if not _supported(skill_dir):
        with pytest.raises(MotionGraphicsError) as e:
            _executor(skill_dir, workspace).response(doc)
        assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
        assert not (workspace / "out" / "out.mp4").exists() and not (workspace / "layers").exists()  # refused before anything rendered
        return
    plan = Executor(PathPolicy(str(workspace)), _executor(skill_dir, workspace).skill, dry_run=True).response(doc)
    assert [lay["file"] for lay in plan["layer_export"]["layers"]] == ["t1.mov", "lt1.mov", "txt1.mov", "img1.mov", "box1.mov"]
    assert not (workspace / "layers").exists()  # a plan writes nothing

    res = _executor(skill_dir, workspace).response(doc)
    le = res["layer_export"]
    assert le["verified"] is True and le["alpha_codec"] == "prores4444" and le["alpha_mode"] == "straight"
    assert le["canvas"]["tool"] == "ffmpeg-skill/background" and le["canvas"]["parameters"]["alpha_codec"] == CANVAS_ALPHA_CODEC
    manifest = json.loads((workspace / "layers" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == LAYERS_MANIFEST_SCHEMA and manifest["verified"] is True
    assert manifest["programme"]["sha256"] == res["output"]["sha256"]
    assert [(lay["id"], lay["type"], lay["start"], lay["end"]) for lay in manifest["layers"]] == [(e["id"], e["type"], e["start"], e["end"]) for e in
                                                                                                 sorted(elements, key=lambda e: (e["start"], e["id"]))]
    for lay in le["layers"]:
        assert lay["verified"] is True and lay["alpha"]["ok"] is True and lay["alpha"]["written"]["alpha_plane"] is True, lay
        assert lay["alpha_codec"] == "prores4444" and abs(lay["duration"] - 6.0) <= 0.25
        assert (workspace / "layers" / lay["file"]).is_file()
    boxes = {lay["id"]: lay for lay in manifest["layers"]}
    assert boxes["box1"]["box"] == {"x": 320 - 10 - 160, "y": 10, "width": 160, "height": 60} and boxes["box1"]["box_precision"] == "exact"
    assert boxes["img1"]["box"] == {"x": 8, "y": 8, "width": 64, "height": 64} and boxes["img1"]["box_precision"] == "exact"

    # a layer is transparent outside its element's window and where nothing is drawn, and carries the graphic inside it
    box_layer = workspace / "layers" / "box1.mov"
    assert _rgba(box_layer, 1.0, "40:20:200:20")[3] == 0               # before the shape's window
    r, g, b, a = _rgba(box_layer, 4.0, "40:20:200:20")
    assert a >= 250 and b > r + 60, (r, g, b, a)                       # navy, opaque, inside it
    assert _rgba(box_layer, 4.0, "40:20:20:120")[3] == 0               # away from the shape

    steps = _composite(skill_dir, workspace, manifest)
    burned = workspace / "out" / "out.mp4"
    with_layers, without = _psnr(steps[-1], burned), _psnr(workspace / "video.mp4", burned)
    # each composite step against the programme stage that burned the same elements, so a failure names the layer that differs
    work = workspace / ".motion-graphics" / res["provenance"]["document_id"]
    stages = [work / f"{op['operation_id'][:16]}.mp4" for op in res["operations"][:-1]] + [burned]
    per_layer = {lay["id"]: _psnr(step, stage) for lay, step, stage in zip(manifest["layers"], steps, stages)}
    if not (with_layers >= MIN_PSNR_DB and with_layers > without + 5):
        pytest.fail(json.dumps({"with_layers": with_layers, "without": without, "per_layer": per_layer,
                                "streams": {lay["id"]: _stream(workspace / "layers" / lay["file"]) for lay in manifest["layers"]},
                                "clean": _stream(workspace / "video.mp4"), "burned": _stream(burned),
                                "by_half_second": {lay["id"]: _psnr_by_half_second(step, stage) for lay, step, stage in zip(manifest["layers"], steps, stages)},
                                # what each pass changed where its own layer is transparent: composite step i-1 -> i, programme stage i-1 -> i
                                "composite_pass": [_psnr_by_half_second(a, b)[:1] for a, b in zip(steps, steps[1:])],
                                "programme_pass": [_psnr_by_half_second(a, b)[:1] for a, b in zip(stages, stages[1:])],
                                "step_streams": [_stream(x) for x in steps], "stage_streams": [_stream(x) for x in stages]}))

    # again with overwrite: the canvas is reused, the layers are written again
    res2 = _executor(skill_dir, workspace).response(request_doc(elements, overwrite=True, options=_options()))
    assert res2["layer_export"]["canvas"]["status"] == "reused" and res2["layer_export"]["verified"] is True


def test_a_lossless_layer_in_another_codec(skill_dir, workspace):
    doc = request_doc([text_overlay_element("cap", start=1.0, end=3.0, text="Lossless")], output="out/one.mp4",
                      options=_options(alpha_codec="qtrle", alpha_mode="premultiplied"))
    if not _supported(skill_dir):
        with pytest.raises(MotionGraphicsError) as e:
            _executor(skill_dir, workspace).response(doc)
        assert e.value.details["reason"] == "ffmpeg_skill_lacks_flag"
        return
    res = _executor(skill_dir, workspace).response(doc)
    lay = res["layer_export"]["layers"][0]
    assert lay["file"] == "cap.mov" and lay["alpha_codec"] == "qtrle" and lay["alpha_mode"] == "premultiplied" and lay["verified"] is True
    assert lay["alpha"]["written"]["codec"] == "qtrle"
    # an existing layer without overwrite is refused before anything renders
    (workspace / "out" / "one.mp4").unlink()
    with pytest.raises(MotionGraphicsError) as e:
        _executor(skill_dir, workspace).response(doc)
    assert e.value.details["reason"] == "exists" and not (workspace / "out" / "one.mp4").exists()
