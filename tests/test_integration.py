"""Real ffmpeg-skill + real ffmpeg media end-to-end tests (STEP 19 / STEP 20). Every assertion checks something
more than an exit code: file existence, non-zero size, sha256, ffprobe-reported resolution/duration/video-stream
presence, and -- for animation/overlay -- objective pixel statistics (signalstats YAVG) on a cropped region,
never a subjective "looks right" judgement.

conftest.py fails (not skips) the whole session if ffmpeg-skill / ffmpeg / ffprobe are not available."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from motion_graphics.adapter import FfmpegSkill
from motion_graphics.doctor import runtime_context
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.executor import Executor
from motion_graphics.security import PathPolicy

from conftest import bug_element, image_overlay_element, request_doc, text_overlay_element, title_element, run_cli, one_json


def _executor(skill_dir, workspace) -> Executor:
    skill, versions, caps = runtime_context(str(skill_dir), 120.0)
    policy = PathPolicy(str(workspace))
    return Executor(policy, skill, timeout=120.0, tool_versions=versions, capabilities=caps)


def _luma(path: str, t: float, crop: str) -> float:
    """Average luma (YAVG) of a cropped region at time t, via ffmpeg's own signalstats filter."""
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "info", "-ss", str(t), "-i", str(path),
           "-frames:v", "1", "-vf", f"crop={crop},signalstats,metadata=print", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"YAVG=([\d.]+)", r.stdout + r.stderr)
    assert m, f"no YAVG found for {path} @ {t}s: stdout={r.stdout!r} stderr={r.stderr[-2000:]!r}"
    return float(m.group(1))


def _probe(skill_dir, path: str) -> dict:
    r = subprocess.run(["python3", str(Path(skill_dir) / "scripts" / "probe.py"), str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


# ---- title
def test_title_renders_valid_video(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([title_element(title="Episode 12", subtitle="The math of video", start=0, end=3)], output="out/title.mp4"))
    assert resp["ok"] is True
    out = Path(resp["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    assert resp["output"]["sha256"] == __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    meta = _probe(skill_dir, str(out))
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180
    assert abs(meta["duration"] - 6.0) < 0.3  # source video.mp4 is 6s; the title template does not trim it


# ---- lower third
def test_lower_third_renders_valid_video(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([{"id": "lt1", "type": "lower_third", "start": 1, "end": 4, "parameters": {"name": "Ada Lovelace", "title": "Analyst"}}], output="out/lt.mp4"))
    assert resp["ok"] is True
    out = Path(resp["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    meta = _probe(skill_dir, str(out))
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180


# ---- text overlay, including Unicode / multiline
@pytest.mark.parametrize("text", ["Hello", "こんにちは、世界", "line one\nline two", "emoji 🎬🎥"])
def test_text_overlay_renders_with_various_text(skill_dir, workspace, text):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([text_overlay_element(text=text, start=0, end=2)], output="out/text.mp4"))
    assert resp["ok"] is True
    assert Path(resp["output"]["path"]).stat().st_size > 0


def test_text_overlay_with_custom_font_file(skill_dir, workspace):
    font_path = workspace / "font.ttf"
    if not font_path.is_file():
        pytest.fail("font.ttf fixture missing: fonts-dejavu-core must be installed for this test to run (it is not skipped)")
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([text_overlay_element(text="Custom Font", font={"font_file": "font.ttf"})], output="out/font.mp4"))
    assert resp["ok"] is True
    assert resp["provenance"]["fonts"]["txt1"]["kind"] == "file"
    assert "font_file_hash" in resp["provenance"]["fonts"]["txt1"]


# ---- image / logo overlay with objective pixel-statistics verification (STEP 20)
def test_image_overlay_changes_pixels_at_its_position(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([image_overlay_element(position="top-right", margin=0, opacity=1.0, start=0, end=4)], output="out/logo.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    crop = "64:64:256:0"  # top-right corner of the 320x180 source, matching the 64x64 logo at margin 0
    luma_with_overlay = _luma(out, 1.0, crop)
    luma_source = _luma(str(workspace / "video.mp4"), 1.0, crop)
    assert abs(luma_with_overlay - luma_source) > 3.0, "overlay did not measurably change the pixels at its declared position"


def test_image_overlay_fade_changes_opacity_over_time(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([{**image_overlay_element(position="top-right", margin=0, opacity=1.0, start=0, end=4),
                                      "animation": {"kind": "fade", "parameters": {"duration": 1.5}}}], output="out/fade.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    crop = "64:64:256:0"
    luma_source = _luma(str(workspace / "video.mp4"), 0.1, crop)
    luma_near_start = _luma(out, 0.1, crop)   # fade barely begun: close to un-overlaid source
    luma_mid = _luma(out, 2.0, crop)          # fade complete, opacity 1.0: fully composited overlay
    assert abs(luma_mid - luma_source) > abs(luma_near_start - luma_source), "fade did not change the overlay's opacity over time"


def test_image_overlay_bad_extension_rejected(skill_dir, workspace):
    (workspace / "logo.svg").write_bytes(b"<svg/>")
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([image_overlay_element(image_path="logo.svg")], output="out/x.mp4"))
    assert e.value.code == "UNSUPPORTED_FORMAT"


# ---- bug
def test_bug_renders_valid_video(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([bug_element(title="LIVE", start=0, end=4)], output="out/bug.mp4"))
    assert resp["ok"] is True
    out = Path(resp["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    assert resp["output"]["sha256"] == __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    meta = _probe(skill_dir, str(out))
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180


@pytest.mark.parametrize("position,crop", [("top-right", "30:14:255:45"), ("bottom-left", "30:14:45:118")])
def test_bug_draws_at_its_declared_corner(skill_dir, workspace, position, crop):
    # Crop windows are sized to ffmpeg-skill/graphics's actual "bug" layout on the 320x180 fixture video (a small
    # fontsize=5 text+box near each margin, verified empirically): tight, not a generous corner region, since
    # averaging in unaffected pixels would dilute the signal below any reasonable threshold at this font size.
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([bug_element(title="LIVE", position=position, start=0, end=4)], output=f"out/bug_{position}.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    luma_with_bug = _luma(out, 1.0, crop)
    luma_source = _luma(str(workspace / "video.mp4"), 1.0, crop)
    assert abs(luma_with_bug - luma_source) > 2.0, f"bug did not measurably change the pixels at its declared {position} corner"


def test_missing_image_asset_fails(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([image_overlay_element(image_path="does-not-exist.png")], output="out/x.mp4"))
    assert e.value.code == "INVALID_INPUT"


# ---- multi-element pipeline, ordering, reuse, tamper detection
def test_multi_element_pipeline_and_reuse(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    doc = request_doc([
        title_element(element_id="a_title", start=0, end=2),
        text_overlay_element(element_id="z_overlay", start=0, end=2, position="bottom"),
    ], output="out/pipeline.mp4")

    first = ex.response(doc)
    assert first["ok"] is True
    assert len(first["operations"]) == 2
    assert first["operations"][0]["status"] == "rendered"
    assert first["operations"][1]["status"] == "rendered"
    first_sha = first["output"]["sha256"]

    work_dirs = list((workspace / ".motion-graphics").glob("*"))
    assert len(work_dirs) == 1
    cached = list(work_dirs[0].glob("*.mp4"))
    assert len(cached) == 1  # exactly one non-final intermediate for a 2-element pipeline

    # second run, same spec, overwrite: the non-final stage must be reused, final stage always re-rendered
    doc2 = dict(doc)
    doc2["output"] = {"path": "out/pipeline.mp4", "overwrite": True}
    second = ex.response(doc2)
    assert second["ok"] is True
    assert second["reused"] is True
    assert second["operations"][0]["status"] == "reused"
    assert second["operations"][1]["status"] == "rendered"
    assert second["output"]["sha256"] == first_sha, "identical specification must render byte-identical output (determinism, STEP 13)"

    # tamper with the cached intermediate: it must be detected and re-rendered, not trusted
    cached[0].write_bytes(b"corrupted")
    doc3 = dict(doc)
    doc3["output"] = {"path": "out/pipeline3.mp4", "overwrite": False}
    third = ex.response(doc3)
    assert third["ok"] is True
    assert third["operations"][0]["status"] == "rendered", "a tampered cache entry must never be trusted"


# ---- output policy
def test_output_exists_without_overwrite(skill_dir, workspace):
    out = workspace / "out"
    out.mkdir()
    (out / "existing.mp4").write_bytes(b"already here")
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([title_element()], output="out/existing.mp4", overwrite=False))
    assert e.value.code == "OUTPUT_ERROR"


def test_output_cannot_equal_input(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([title_element()], video="video.mp4", output="video.mp4", overwrite=True))
    assert e.value.code == "OUTPUT_ERROR"


# ---- timeline vs. real media duration
def test_element_end_beyond_duration_rejected(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([title_element(start=0, end=999)], output="out/x.mp4"))
    assert e.value.code == "INVALID_TIME_RANGE"


def test_video_without_video_stream_rejected(skill_dir, workspace, tmp_path):
    audio_only = workspace / "audio.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", str(audio_only)], check=True)
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([title_element()], video="audio.mp4", output="out/x.mp4"))
    assert e.value.code == "INVALID_INPUT"


# ---- dry run / plan writes no media
def test_plan_writes_no_media(skill_dir, workspace):
    skill, versions, caps = runtime_context(str(skill_dir), 120.0)
    policy = PathPolicy(str(workspace))
    ex = Executor(policy, skill, dry_run=True, timeout=120.0, tool_versions=versions, capabilities=caps)
    resp = ex.response(request_doc([title_element()], output="out/never.mp4"))
    assert resp["ok"] is True
    assert resp["dry_run"] is True
    assert not (workspace / "out" / "never.mp4").exists()
    assert not (workspace / ".motion-graphics").exists()


# ---- cancellation
def test_cancel_before_start_raises_cancelled(skill_dir):
    skill = FfmpegSkill(Path(skill_dir))
    skill.cancel()
    with pytest.raises(MotionGraphicsError) as e:
        skill.run_tool("probe", ["nonexistent.mp4"])
    assert e.value.code == "CANCELLED"


# ---- CLI-level, real process boundary
def test_cli_run_end_to_end(skill_dir, workspace):
    doc = request_doc([title_element(title="CLI Test")], output="out/cli.mp4")
    code, out, err = run_cli(["run", "-", "--json", "--workspace", str(workspace), "--ffmpeg-skill", str(skill_dir)], stdin_text=json.dumps(doc))
    result = one_json(out)
    assert code == 0, err
    assert result["ok"] is True
    assert Path(result["output"]["path"]).is_file()


def test_cli_run_invalid_document_nonzero_exit(skill_dir, workspace):
    code, out, err = run_cli(["run", "-", "--json", "--workspace", str(workspace), "--ffmpeg-skill", str(skill_dir)], stdin_text="{not json")
    result = one_json(out)
    assert code != 0
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_cli_validate_does_not_require_workspace_or_media(tmp_path):
    doc = request_doc([title_element()])
    code, out, err = run_cli(["validate", "-", "--json"], stdin_text=json.dumps(doc), cwd=str(tmp_path))
    result = one_json(out)
    assert code == 0
    assert result["ok"] is True
    assert not list(tmp_path.iterdir())  # touched no files


def test_response_shapes_match_contract_documentation(skill_dir, workspace):
    from motion_graphics.contract import skill_contract
    documented = skill_contract()["response"]["success"]

    doc = request_doc([title_element()])
    code, out, err = run_cli(["validate", "-", "--json"], stdin_text=json.dumps(doc), cwd=str(workspace))
    validate_resp = one_json(out)
    assert set(documented["validate"]) <= set(validate_resp) | {"note"}

    code, out, err = run_cli(["plan", "-", "--json", "--workspace", str(workspace), "--ffmpeg-skill", str(skill_dir)], stdin_text=json.dumps(doc))
    plan_resp = one_json(out)
    assert set(documented["plan"]) <= set(plan_resp) | {"note"}

    doc2 = request_doc([title_element()], output="out/shapes.mp4")
    code, out, err = run_cli(["run", "-", "--json", "--workspace", str(workspace), "--ffmpeg-skill", str(skill_dir)], stdin_text=json.dumps(doc2))
    run_resp = one_json(out)
    assert set(documented["run"]) <= set(run_resp)


def test_doctor_cli_against_real_skill(skill_dir):
    code, out, err = run_cli(["doctor", "--json", "--ffmpeg-skill", str(skill_dir)])
    doc = one_json(out)
    assert doc["checks"]["ffmpeg_skill"]["status"] == "ok"
    assert doc["checks"]["ffmpeg"]["status"] == "ok"
    assert doc["status"] in ("ok", "degraded")
