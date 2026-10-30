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

from conftest import (bug_element, chapter_element, countdown_element, hook_element, image_overlay_element, meme_element, progress_element,
                      request_doc, sticker_element, text_overlay_element, title_element, video_overlay_element, run_cli, one_json)


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


def _luma_diff(path: str, reference: str, t: float, crop: str) -> float:
    """Mean absolute per-pixel luma difference between two videos in a cropped region at time t (blend=difference,
    then signalstats YAVG). Unlike comparing two region means, a bright fill and a dark border cannot cancel out.
    Both inputs get the same colour tags first: the stored pixel values are compared, and FFmpeg >= 8 would otherwise
    insert a colour conversion in front of blend when an output is tagged BT.709 and its source is untagged."""
    same = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    graph = (f"[0:v]{same},crop={crop},format=yuv420p[a];[1:v]{same},crop={crop},format=yuv420p[b];"
             "[a][b]blend=all_mode=difference,signalstats,metadata=print")
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "info", "-ss", str(t), "-i", str(path), "-ss", str(t), "-i", str(reference),
           "-filter_complex", graph, "-frames:v", "1", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"YAVG=([\d.]+)", r.stdout + r.stderr)
    assert m, f"no YAVG found for {path} vs {reference} @ {t}s: stdout={r.stdout!r} stderr={r.stderr[-2000:]!r}"
    return float(m.group(1))


def _probe(skill_dir, path: str) -> dict:
    r = subprocess.run(["python3", str(Path(skill_dir) / "scripts" / "probe.py"), str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _mean_volume(path: str) -> float:
    """Mean loudness (dB) of the whole file's audio track, via ffmpeg's own volumedetect filter -- an objective
    way to tell which *source* audio track survived --audio-stream (ffprobe on the output alone cannot: it only
    ever shows the one audio stream the tool actually mapped, never which source index it came from)."""
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", r.stdout + r.stderr)
    assert m, f"no mean_volume found for {path}: stderr={r.stderr[-2000:]!r}"
    return float(m.group(1))


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


# ---- video_overlay: video-on-video picture-in-picture / chroma-key (ffmpeg-skill/overlay --video, --chromakey*,
# ffmpeg-skill 0.11.0; issue #10 item 2)
def test_video_overlay_changes_pixels_at_its_position(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([video_overlay_element(video_path="video_short.mp4", position="top-left", margin=0, scale_width=100, start=0, end=2)],
                                    output="out/pip.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    crop = "50:40:0:0"  # safely inside the scaled-to-100px-wide PiP layer placed flush at the top-left corner
    luma_with_pip = _luma(out, 0.5, crop)
    luma_source = _luma(str(workspace / "video.mp4"), 0.5, crop)
    assert abs(luma_with_pip - luma_source) > 2.0, "video_overlay did not measurably change the pixels at its declared position"


def test_video_overlay_scale_and_opacity_are_applied(skill_dir, workspace):
    # Regression for the argv construction itself: a video_overlay element wires --scale/--opacity through, same
    # as image_overlay -- rendering must not error out and must still produce a valid, correctly-sized artifact.
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([video_overlay_element(video_path="video_short.mp4", position="bottom-right", scale_width=80, opacity=0.6, start=0, end=2)],
                                    output="out/pip_opacity.mp4"))
    assert resp["ok"] is True
    meta = _probe(skill_dir, resp["output"]["path"])
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180


def test_video_overlay_chromakey_reveals_the_base_video_underneath(skill_dir, workspace):
    # An opaque full-frame PiP layer hides the base video entirely (its luma stays close to solid green's luma
    # regardless of time); keying that exact colour out with --chromakey should instead reveal the base video's
    # own, time-varying content underneath -- an objective, measurable difference, not a "looks right" judgement.
    green = workspace / "green.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x00ff00:s=320x180:r=25", "-t", "2",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(green)], check=True)
    crop = "64:64:128:58"  # a fixed, fully-covered region near the centre of the full-frame PiP layer

    ex = _executor(skill_dir, workspace)
    opaque = ex.response(request_doc([video_overlay_element(video_path="green.mp4", position="top-left", margin=0, start=0, end=2)], output="out/opaque.mp4"))
    assert opaque["ok"] is True
    keyed = ex.response(request_doc([video_overlay_element(video_path="green.mp4", position="top-left", margin=0, chromakey="00ff00", start=0, end=2)],
                                     output="out/keyed.mp4"))
    assert keyed["ok"] is True

    luma_source = _luma(str(workspace / "video.mp4"), 1.0, crop)
    luma_opaque = _luma(opaque["output"]["path"], 1.0, crop)
    luma_keyed = _luma(keyed["output"]["path"], 1.0, crop)
    assert abs(luma_opaque - luma_source) > 10.0, "an opaque full-frame PiP layer should hide the base video, not match its luma"
    assert abs(luma_keyed - luma_source) < abs(luma_opaque - luma_source), "chroma-keying the PiP layer's own colour should reveal the base video, not hide it like the opaque case"


def test_missing_video_overlay_asset_fails(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([video_overlay_element(video_path="does-not-exist.mp4")], output="out/x.mp4"))
    assert e.value.code == "INVALID_INPUT"


def test_video_overlay_source_without_video_stream_rejected(skill_dir, workspace):
    audio_only = workspace / "audio_only.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", str(audio_only)], check=True)
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([video_overlay_element(video_path="audio_only.mp4")], output="out/x.mp4"))
    assert e.value.code == "INVALID_INPUT"


# ---- document.options.audio_stream (ffmpeg-skill/{graphics,overlay} --audio-stream, ffmpeg-skill 0.12.0;
# issue #10 item 3)
def test_audio_stream_option_selects_the_requested_source_track(skill_dir, workspace):
    # video_dual_audio.mp4's stream 0 is silent, stream 1 an audible 440Hz tone -- selecting each and measuring
    # the *rendered output's* mean_volume proves the flag actually reached ffmpeg-skill/graphics, not merely that
    # the request was accepted.
    ex = _executor(skill_dir, workspace)
    silent = ex.response(request_doc([title_element(start=0, end=2)], video="video_dual_audio.mp4", output="out/audio0.mp4", options={"audio_stream": 0}))
    tone = ex.response(request_doc([title_element(start=0, end=2)], video="video_dual_audio.mp4", output="out/audio1.mp4", options={"audio_stream": 1}))
    assert silent["ok"] is True and tone["ok"] is True
    vol_silent = _mean_volume(silent["output"]["path"])
    vol_tone = _mean_volume(tone["output"]["path"])
    assert vol_tone - vol_silent > 20.0, f"selecting the tone track should measurably increase mean_volume (silent={vol_silent}dB, tone={vol_tone}dB)"


def test_audio_stream_option_threaded_through_overlay_tool_too(skill_dir, workspace):
    # Same guarantee, but through ffmpeg-skill/overlay (a text_overlay element) rather than graphics -- the issue
    # specifically calls out that --audio-stream was added to *both* tools.
    ex = _executor(skill_dir, workspace)
    silent = ex.response(request_doc([text_overlay_element(text="x", start=0, end=2)], video="video_dual_audio.mp4", output="out/ov_audio0.mp4", options={"audio_stream": 0}))
    tone = ex.response(request_doc([text_overlay_element(text="x", start=0, end=2)], video="video_dual_audio.mp4", output="out/ov_audio1.mp4", options={"audio_stream": 1}))
    assert silent["ok"] is True and tone["ok"] is True
    assert _mean_volume(tone["output"]["path"]) - _mean_volume(silent["output"]["path"]) > 20.0


def test_audio_stream_out_of_range_for_real_input_is_a_tool_error(skill_dir, workspace):
    # document.options.audio_stream is only bounded structurally at [0, 63] (model.py) -- ffmpeg-skill/graphics
    # itself is what actually knows how many audio streams a given input has, and `die`s on an out-of-range value;
    # this Skill never guesses whether a real input has that many tracks (ADR-16).
    ex = _executor(skill_dir, workspace)
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(request_doc([title_element(start=0, end=2)], video="video_dual_audio.mp4", output="out/x.mp4", options={"audio_stream": 5}))
    assert e.value.code == "TOOL_ERROR"


# ---- dropped_non_av_streams surfaced (ffmpeg-skill 0.12.1; issue #10 item 4)
def test_dropped_non_av_streams_is_false_and_no_warning_on_a_normal_render(skill_dir, workspace):
    # None of this Skill's fixtures carry a subtitle/data stream, so ffmpeg-skill's own preservation attempt has
    # nothing to fall back from -- pinning down the ordinary, unsurprising case explicitly (a stray True or a
    # warning here would itself be the bug).
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([title_element(start=0, end=2)], output="out/normal.mp4"))
    assert resp["ok"] is True
    assert resp["operations"][0]["dropped_non_av_streams"] is False
    assert resp["warnings"] == []


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


# ---- chapter
def test_chapter_renders_valid_video(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([chapter_element(title="Part 2 -- Setup", start=0, end=4)], output="out/chapter.mp4"))
    assert resp["ok"] is True
    out = Path(resp["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    assert resp["output"]["sha256"] == __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    meta = _probe(skill_dir, str(out))
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180


@pytest.mark.parametrize("position,crop", [("bottom-left", "40:20:40:110"), ("top-right", "50:20:250:45")])
def test_chapter_draws_at_its_declared_corner(skill_dir, workspace, position, crop):
    # Crop windows sized empirically to ffmpeg-skill/graphics's actual "chapter" layout on the 320x180 fixture
    # video (fontsize=7, a strongly-opaque (0.9 alpha) brand-primary box -- a clearer signal than bug's, still
    # measured against the real rendered output rather than guessed from the filter expression).
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([chapter_element(title="Part 2", position=position, start=0, end=4)], output=f"out/chapter_{position}.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    luma_with_chapter = _luma(out, 1.0, crop)
    luma_source = _luma(str(workspace / "video.mp4"), 1.0, crop)
    assert abs(luma_with_chapter - luma_source) > 3.0, f"chapter did not measurably change the pixels at its declared {position} corner"


# ---- progress
def test_progress_renders_valid_video(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([progress_element(start=0, end=4)], output="out/progress.mp4"))
    assert resp["ok"] is True
    out = Path(resp["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    assert resp["output"]["sha256"] == __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    meta = _probe(skill_dir, str(out))
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180


def test_progress_bar_fills_left_to_right_over_time(skill_dir, workspace):
    # The bar is 3px tall along the very bottom row on this 320x180 fixture, filling left-to-right over
    # [start, end]. Crop windows (near the left edge, which fills almost immediately, vs. near the right edge,
    # which only fills near the end) and the thresholds below are measured empirically against the actual
    # rendered output at several points in time, not guessed from the filter expression alone.
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([progress_element(start=0, end=4)], output="out/progress_fill.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    early, late = "20:3:10:177", "20:3:250:177"
    src_early = _luma(str(workspace / "video.mp4"), 1.0, early)
    src_late = _luma(str(workspace / "video.mp4"), 1.0, late)
    early_at_1s = abs(_luma(out, 1.0, early) - src_early)
    late_at_1s = abs(_luma(out, 1.0, late) - src_late)
    late_at_39s = abs(_luma(out, 3.9, late) - src_late)
    assert early_at_1s > 50.0, "the bar had not measurably reached the early region 1s into a 4s fill"
    assert late_at_1s < 30.0, "the bar had already reached the late region well before it should have"
    assert late_at_39s > 50.0, "the bar had not measurably reached the late region by the very end of the fill"


# ---- countdown
def test_countdown_renders_valid_video(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([countdown_element(count_from=3, start=0, end=4)], output="out/countdown.mp4"))
    assert resp["ok"] is True
    out = Path(resp["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    assert resp["output"]["sha256"] == __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    meta = _probe(skill_dir, str(out))
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180


@pytest.mark.parametrize("t", [0.5, 3.5])
def test_countdown_draws_a_digit_throughout_its_window(skill_dir, workspace, t):
    # count_from=3 over [0, 4] splits into 4 equal 1s segments (one digit each, 3/2/1/0), each drawn centred at
    # fontsize=57 on this 320x180 fixture, so the 40x60 window at the centre lies inside every digit's glyph box.
    # Checked at the first and last segment to confirm a digit is drawn throughout the window, not only near the start.
    # The measure is the mean *absolute* per-pixel difference from the source: comparing the two windows' mean luma
    # let the digit's yellow fill and dark border cancel on the yellow/blue bar edge behind it (2.9 on FFmpeg 8.0, with
    # the digit correctly drawn). The control window sits on the same two bars below the digit, where nothing is drawn:
    # it takes whatever the re-encode does to the background (nothing, or with an older ffmpeg-skill on FFmpeg 8 a
    # whole-frame BT.601 -> BT.709 conversion of the untagged source, 8.5 there), so the margin over it is the digit.
    # Measured: digit window 27-36, control 0-8.5.
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([countdown_element(count_from=3, start=0, end=4)], output=f"out/countdown_{t}.mp4"))
    assert resp["ok"] is True
    out = resp["output"]["path"]
    source = str(workspace / "video.mp4")
    digit = _luma_diff(out, source, t, "40:60:140:60")
    control = _luma_diff(out, source, t, "40:30:140:145")
    assert digit - control > 15.0, f"countdown did not measurably draw a digit at t={t}s (mean |diff| {digit:.2f}, control {control:.2f})"


# ---- sticker / hook / meme: graphics templates newer than the oldest verified ffmpeg-skill (ADR-19). The CI matrix runs
# both the newest and the oldest verified checkout, so each test asserts what that checkout can actually do -- a render
# that is measured, or the documented up-front refusal -- instead of skipping on the old one.
def _skill_info(skill_dir):
    return FfmpegSkill(Path(skill_dir)).info()


def _render_or_refuse(skill_dir, workspace, template, element, output):
    ex = _executor(skill_dir, workspace)
    if _skill_info(skill_dir).template_support(template) is False:
        with pytest.raises(MotionGraphicsError) as e:
            ex.response(request_doc([element], output=output))
        assert e.value.code == "TOOL_ERROR" and e.value.details["reason"] == "ffmpeg_skill_lacks_template" and e.value.details["template"] == template
        assert not (workspace / output).exists()  # refused before anything rendered
        return None
    resp = ex.response(request_doc([element], output=output))
    assert resp["ok"] is True
    meta = _probe(skill_dir, resp["output"]["path"])
    assert meta["video"]["width"] == 320 and meta["video"]["height"] == 180
    return resp


# Crop windows below are measured on the 320x180 fixture, as the tests above are: each "drawn" window differs from the
# source by 40-116 mean |luma| (blend=difference), each control window beside it by under 1.
def test_sticker_draws_a_chip_in_its_corner_and_nothing_elsewhere(skill_dir, workspace):
    resp = _render_or_refuse(skill_dir, workspace, "sticker", sticker_element(text="NEW", start=0, end=4), "out/sticker.mp4")
    if resp is None:
        return
    out, source = resp["output"]["path"], str(workspace / "video.mp4")
    assert _luma_diff(out, source, 2.0, "40:16:240:44") > 25.0, "the sticker chip was not drawn in its top-right corner"
    assert _luma_diff(out, source, 2.0, "40:16:20:140") < 3.0, "pixels changed away from the sticker"


def test_hook_draws_a_title_band_and_a_bar_that_empties_across_the_window(skill_dir, workspace):
    resp = _render_or_refuse(skill_dir, workspace, "hook", hook_element(title="WATCH THIS", start=0, end=4), "out/hook.mp4")
    if resp is None:
        return
    out, source = resp["output"]["path"], str(workspace / "video.mp4")
    assert _luma_diff(out, source, 2.0, "100:20:110:78") > 40.0, "the hook's title band was not drawn mid-frame"
    assert _luma_diff(out, source, 2.0, "100:20:110:140") < 3.0, "pixels changed below the hook's band"
    # the 3px bar along the top is full-width at the start and slides out to the left over the element's own window: the
    # x=200..230 strip is covered a quarter of the way in and empty three quarters of the way in
    assert _luma_diff(out, source, 1.0, "30:3:200:0") > 50.0, "the hook's bar had already emptied a quarter of the way through"
    assert _luma_diff(out, source, 3.0, "30:3:200:0") < 5.0, "the hook's bar had not emptied three quarters of the way through"


def test_hook_card_lasts_the_whole_element_window_not_the_templates_default_three_seconds(skill_dir, workspace):
    # graphics.py's own --duration defaults to 3s; this skill passes the element's window, so a 5s element keeps the card up at 4.5s
    resp = _render_or_refuse(skill_dir, workspace, "hook", hook_element(title="WATCH THIS", start=0, end=5), "out/hook5.mp4")
    if resp is None:
        return
    assert _luma_diff(resp["output"]["path"], str(workspace / "video.mp4"), 4.5, "100:20:110:78") > 40.0


def test_meme_draws_its_top_and_bottom_lines(skill_dir, workspace):
    resp = _render_or_refuse(skill_dir, workspace, "meme", meme_element(top="WHEN THE RENDER", bottom="FINISHES", start=0, end=4), "out/meme.mp4")
    if resp is None:
        return
    out, source = resp["output"]["path"], str(workspace / "video.mp4")
    assert _luma_diff(out, source, 2.0, "120:14:100:47") > 40.0, "the meme's top line was not drawn"
    assert _luma_diff(out, source, 2.0, "70:14:125:118") > 40.0, "the meme's bottom line was not drawn"
    assert _luma_diff(out, source, 2.0, "100:14:110:85") < 3.0, "pixels changed between the meme's lines"


# ---- colours on the graphics templates (ADR-20). ffmpeg-skill/graphics takes --primary / --text-color as RRGGBB only
# (_common color_hex: "colour must be RRGGBB"), so a colour name used to pass validation here and fail inside the render.
# Each template element with a colour parameter is rendered twice, once with colour names and once with the RRGGBB
# those names stand for in FFmpeg's own colour table: the two outputs must match, and must differ from the source.
TEMPLATE_COLOUR_CASES = [
    ("title", "title", title_element(title="HELLO", start=0, end=4, text_color="yellow", primary_color="red"), {"text_color": "FFFF00", "primary_color": "FF0000"}),
    ("lower_third", "lower-third", {"id": "lt1", "type": "lower_third", "start": 0, "end": 4,
                                    "parameters": {"name": "Ada", "title": "Analyst", "text_color": "White", "primary_color": "navy"}},
     {"text_color": "FFFFFF", "primary_color": "000080"}),
    ("bug", "bug", bug_element(title="LIVE", start=0, end=4, text_color="lime"), {"text_color": "00FF00"}),
    ("chapter", "chapter", chapter_element(title="Part 2", start=0, end=4, primary_color="orange"), {"primary_color": "FFA500"}),
    ("progress", "progress", progress_element(start=0, end=4, primary_color="magenta"), {"primary_color": "FF00FF"}),
    ("countdown", "countdown", countdown_element(count_from=3, start=0, end=4, primary_color="cyan"), {"primary_color": "00FFFF"}),
    ("sticker", "sticker", sticker_element(text="NEW", start=0, end=4, primary_color="gold"), {"primary_color": "FFD700"}),
    ("hook", "hook", hook_element(title="WATCH THIS", start=0, end=4, text_color="black", primary_color="crimson"), {"text_color": "000000", "primary_color": "DC143C"}),
]


@pytest.mark.parametrize("etype,template,named,hexes", TEMPLATE_COLOUR_CASES, ids=[c[0] for c in TEMPLATE_COLOUR_CASES])
def test_template_colour_names_render_as_their_rrggbb(skill_dir, workspace, etype, template, named, hexes):
    with_hex = {**named, "parameters": {**named["parameters"], **hexes}}
    a = _render_or_refuse(skill_dir, workspace, template, named, f"out/{etype}_named.mp4")
    b = _render_or_refuse(skill_dir, workspace, template, with_hex, f"out/{etype}_hex.mp4")
    if a is None or b is None:
        return
    out_named, out_hex, source = a["output"]["path"], b["output"]["path"], str(workspace / "video.mp4")
    for t in (0.6, 2.0, 3.4):
        assert _luma_diff(out_named, out_hex, t, "320:180:0:0") < 0.5, f"{etype}: a colour name did not render as its RRGGBB at t={t}s"
    # sanity only (the per-element tests above measure where each template draws): the smallest, the bug, measured 0.45
    assert _luma_diff(out_named, source, 2.0, "320:180:0:0") > 0.2, f"{etype}: nothing was drawn"
    recorded = a["operations"][0]["parameters"]
    assert all(recorded[k] == named["parameters"][k] for k in hexes), "the stage records the request's colours, not the converted ones"


def test_a_template_colour_name_actually_changes_the_colour(skill_dir, workspace):
    # the comparison above would also pass if both renders ignored the colour; this one would not
    ex = _executor(skill_dir, workspace)
    yellow = ex.response(request_doc([progress_element(start=0, end=4, primary_color="yellow")], output="out/p_yellow.mp4"))["output"]["path"]
    blue = ex.response(request_doc([progress_element(start=0, end=4, primary_color="blue")], output="out/p_blue.mp4"))["output"]["path"]
    assert _luma_diff(yellow, blue, 3.9, "300:3:10:177") > 50.0, "the progress bar did not take the named colour"


def test_template_colour_alpha_is_refused_before_anything_renders(skill_dir, workspace):
    # graphics.py has no opacity flag for its colours, so @alpha cannot be honoured: refused up front, naming the element,
    # never sent (it would fail inside the render) and never dropped (it would render opaque)
    ex = _executor(skill_dir, workspace)
    doc = request_doc([text_overlay_element(start=0, end=1), progress_element(element_id="bar", start=0, end=4, primary_color="red@0.5")], output="out/alpha.mp4")
    with pytest.raises(MotionGraphicsError) as e:
        ex.response(doc)
    assert e.value.code == "UNSUPPORTED_OPERATION"
    assert e.value.details["element_id"] == "bar" and e.value.details["element_type"] == "progress"
    assert e.value.details["field"] == "elements[1].parameters.primary_color" and e.value.details["reason"] == "color_alpha_not_supported"
    assert not (workspace / "out").exists()


def test_overlay_colour_names_and_alpha_still_render(skill_dir, workspace):
    ex = _executor(skill_dir, workspace)
    resp = ex.response(request_doc([text_overlay_element(text="Hi", start=0, end=2, font_color="Yellow", box=True, box_color="navy@0.5")], output="out/ov_named.mp4"))
    assert resp["ok"] is True


# ---- document.options.platform: ffmpeg-skill --platform, so an omitted margin becomes the destination's safe zone
def _platform_response(skill_dir, workspace, elements, output, platform):
    d = request_doc(elements, output=output)
    if platform:
        d["options"] = {"platform": platform}
    return _executor(skill_dir, workspace).response(d)


def test_platform_moves_an_overlay_and_a_graphics_template_into_the_safe_zone(skill_dir, workspace):
    info = _skill_info(skill_dir)
    if not (info.takes_platform("overlay") and info.takes_platform("graphics")):
        with pytest.raises(MotionGraphicsError) as e:
            _platform_response(skill_dir, workspace, [text_overlay_element(position="top-left", font_size=20, start=0, end=4)], "out/p.mp4", "broadcast")
        assert e.value.details["reason"] == "ffmpeg_skill_lacks_platform_flag"
        return
    source = str(workspace / "video.mp4")
    # overlay (text at top-left): 24px margin by default; broadcast's 5% safe zone is 16px/9px, so the text sits nearer the corner
    ov = lambda platform, name: _platform_response(skill_dir, workspace, [text_overlay_element(position="top-left", font_size=20, start=0, end=4)], f"out/{name}.mp4", platform)["output"]["path"]
    plain, safe = ov(None, "ov_plain"), ov("broadcast", "ov_safe")
    assert _luma_diff(plain, source, 2.0, "40:14:24:24") > 30.0 and _luma_diff(plain, source, 2.0, "40:10:10:10") < 5.0
    assert _luma_diff(safe, source, 2.0, "40:14:24:24") < 10.0 and _luma_diff(safe, source, 2.0, "40:10:10:10") > 20.0
    # graphics template (bug at top-left): the brand's 48px safe margin by default, the broadcast safe zone under platform
    gx = lambda platform, name: _platform_response(skill_dir, workspace, [bug_element(position="top-left", start=0, end=4)], f"out/{name}.mp4", platform)["output"]["path"]
    plain, safe = gx(None, "bug_plain"), gx("broadcast", "bug_safe")
    assert _luma_diff(plain, source, 2.0, "20:14:44:46") > 8.0 and _luma_diff(plain, source, 2.0, "20:14:6:6") < 3.0
    assert _luma_diff(safe, source, 2.0, "20:14:44:46") < 3.0 and _luma_diff(safe, source, 2.0, "20:14:6:6") > 8.0


def test_an_explicit_margin_wins_over_platform(skill_dir, workspace):
    # 40, not 24: ffmpeg-skill/overlay tells "explicit" from "not given" by comparing --margin with its own default (24), so
    # an explicit 24 is indistinguishable from none and the safe zone applies to it (documented in ADR-19; not pinned here)
    if not _skill_info(skill_dir).takes_platform("overlay"):
        return  # nothing to override on a checkout without --platform (the refusal is covered above)
    source = str(workspace / "video.mp4")
    out = _platform_response(skill_dir, workspace, [text_overlay_element(position="top-left", font_size=20, margin=40, start=0, end=4)], "out/ov_explicit.mp4", "broadcast")["output"]["path"]
    assert _luma_diff(out, source, 2.0, "40:14:40:40") > 30.0 and _luma_diff(out, source, 2.0, "40:10:10:10") < 5.0


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
