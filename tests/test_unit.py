"""Model validation, timeline rules, canonical JSON / identity determinism, the error table, PathPolicy, fonts.
No ffmpeg, no ffmpeg-skill, no file system access beyond tmp_path -- these tests always run."""
import pytest

from motion_graphics.canonical import canonical_json, sha256_text, stable_hash
from motion_graphics.errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES, MotionGraphicsError
from motion_graphics.fonts import DEFAULT_FONT_ID, FONT_REGISTRY, resolve_font
from motion_graphics.model import ELEMENT_TYPES, UNSUPPORTED_ANIMATIONS, UNSUPPORTED_ELEMENT_TYPES, parse_request
from motion_graphics.security import PathPolicy, check_filename

from conftest import bug_element, chapter_element, request_doc, text_overlay_element, title_element


# ---- request parsing / structure
def test_minimal_valid_document_parses():
    doc = parse_request(request_doc([title_element()]))
    assert doc.video_path == "video.mp4"
    assert doc.output_path == "out/out.mp4"
    assert len(doc.elements) == 1
    assert doc.elements[0].type == "title"


def test_unknown_top_level_field_rejected():
    d = request_doc([title_element()])
    d["bogus"] = 1
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_missing_required_field_rejected():
    d = request_doc([title_element()])
    del d["output"]
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_empty_elements_rejected():
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([]))
    assert e.value.code == "INVALID_REQUEST"


def test_too_many_elements_rejected():
    els = [title_element(element_id=f"t{i}", start=float(i), end=float(i) + 1) for i in range(65)]
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc(els))
    assert e.value.code == "INVALID_REQUEST"


# ---- element type
def test_unsupported_element_type_rejected_with_reason():
    d = request_doc([{"id": "s1", "type": "shape", "start": 0, "end": 1, "parameters": {}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"
    assert "shape" in e.value.message


def test_unknown_element_type_rejected():
    d = request_doc([{"id": "s1", "type": "not_a_real_type", "start": 0, "end": 1, "parameters": {}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"


def test_all_unsupported_element_types_declared_and_rejected():
    for etype in UNSUPPORTED_ELEMENT_TYPES:
        assert etype not in ELEMENT_TYPES
        d = request_doc([{"id": "s1", "type": etype, "start": 0, "end": 1, "parameters": {}}])
        with pytest.raises(MotionGraphicsError) as e:
            parse_request(d)
        assert e.value.code == "UNSUPPORTED_OPERATION"


def test_missing_required_parameter_rejected():
    d = request_doc([{"id": "t1", "type": "title", "start": 0, "end": 1, "parameters": {}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


# ---- timeline
@pytest.mark.parametrize("start,end", [(-1, 2), (0, 0), (2, 1), (float("nan"), 2), (0, float("inf")), (0, float("nan"))])
def test_invalid_time_range_rejected(start, end):
    d = request_doc([title_element(start=start, end=end)])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code in ("INVALID_TIME_RANGE",)


def test_duplicate_element_id_rejected():
    d = request_doc([title_element(element_id="dup", start=0, end=1), title_element(element_id="dup", start=2, end=3)])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "DEPENDENCY_ERROR"


def test_invalid_id_pattern_rejected():
    d = request_doc([title_element(element_id="has a space", start=0, end=1)])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_ordering_is_by_start_then_id_not_list_order():
    doc = parse_request(request_doc([
        title_element(element_id="second", start=5, end=6),
        title_element(element_id="first", start=0, end=1),
    ]))
    ordered = doc.ordered_elements()
    assert [e.element_id for e in ordered] == ["first", "second"]


def test_ordering_breaks_ties_by_id():
    doc = parse_request(request_doc([
        title_element(element_id="b", start=0, end=1),
        title_element(element_id="a", start=0, end=1),
    ]))
    ordered = doc.ordered_elements()
    assert [e.element_id for e in ordered] == ["a", "b"]


# ---- animation
def test_title_does_not_accept_configurable_animation():
    d = request_doc([{"id": "t1", "type": "title", "start": 0, "end": 2, "parameters": {"title": "x"},
                       "animation": {"kind": "fade", "parameters": {"duration": 0.3}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"


def test_text_overlay_accepts_fade_animation():
    d = request_doc([{**text_overlay_element(start=0, end=4), "animation": {"kind": "fade", "parameters": {"duration": 0.5}}}])
    doc = parse_request(d)
    assert doc.elements[0].animation.kind == "fade"
    assert doc.elements[0].animation.parameters["duration"] == 0.5


def test_fade_duration_must_fit_in_element_window():
    d = request_doc([{**text_overlay_element(start=0, end=1), "animation": {"kind": "fade", "parameters": {"duration": 10}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_TIME_RANGE"


@pytest.mark.parametrize("kind", sorted(UNSUPPORTED_ANIMATIONS))
def test_unsupported_animation_kinds_rejected(kind):
    d = request_doc([{**text_overlay_element(start=0, end=4), "animation": {"kind": kind, "parameters": {}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"


# ---- bug
def test_bug_parses_with_default_position():
    doc = parse_request(request_doc([bug_element()]))
    assert doc.elements[0].type == "bug"
    assert doc.elements[0].parameters["title"] == "LIVE"
    assert doc.elements[0].parameters["position"] == "top-right"


def test_bug_accepts_a_corner_position():
    doc = parse_request(request_doc([bug_element(position="bottom-left")]))
    assert doc.elements[0].parameters["position"] == "bottom-left"


@pytest.mark.parametrize("bad", ["top", "center", "middle", ""])
def test_bug_rejects_a_non_corner_position(bad):
    d = request_doc([bug_element(position=bad)])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_bug_rejects_xy_position_object():
    # Unlike text_overlay/image_overlay's `position` type, bug's is a closed-vocabulary string: ffmpeg-skill/
    # graphics's "bug" template has no {x,y} support at all (argparse choices only).
    d = request_doc([bug_element(position={"x": 10, "y": 10})])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_bug_does_not_accept_configurable_animation():
    d = request_doc([{**bug_element(), "animation": {"kind": "fade", "parameters": {"duration": 0.3}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"


# ---- chapter
def test_chapter_parses_with_default_position():
    doc = parse_request(request_doc([chapter_element()]))
    assert doc.elements[0].type == "chapter"
    assert doc.elements[0].parameters["title"] == "Part 2"
    assert doc.elements[0].parameters["position"] == "bottom-left"


def test_chapter_accepts_a_corner_position():
    doc = parse_request(request_doc([chapter_element(position="top-right")]))
    assert doc.elements[0].parameters["position"] == "top-right"


@pytest.mark.parametrize("bad", ["top", "center", "middle", ""])
def test_chapter_rejects_a_non_corner_position(bad):
    d = request_doc([chapter_element(position=bad)])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_chapter_has_no_text_color_parameter():
    # Unlike `bug`, `chapter`'s exposed color is `primary_color` (the chip background); ffmpeg-skill/graphics's
    # chapter branch always uses the brand background for text, so `text_color` would silently do nothing --
    # it must not even be an accepted parameter name.
    d = request_doc([chapter_element(text_color="red")])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


def test_chapter_accepts_primary_color():
    doc = parse_request(request_doc([chapter_element(primary_color="00FF00")]))
    assert doc.elements[0].parameters["primary_color"] == "00FF00"


def test_chapter_does_not_accept_configurable_animation():
    d = request_doc([{**chapter_element(), "animation": {"kind": "fade", "parameters": {"duration": 0.3}}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"


# ---- position / color
@pytest.mark.parametrize("position", ["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"])
def test_named_positions_accepted(position):
    d = request_doc([text_overlay_element(position=position)])
    parse_request(d)


def test_explicit_xy_position_accepted():
    d = request_doc([text_overlay_element(position={"x": 10, "y": -20})])
    doc = parse_request(d)
    assert doc.elements[0].parameters["position"] == "10,-20"


def test_bad_named_position_rejected():
    d = request_doc([text_overlay_element(position="middle-ish")])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("color", ["white", "black", "ff00aa", "black@0.5", "FF00AA@1.0"])
def test_valid_colors_accepted(color):
    d = request_doc([text_overlay_element(font_color=color)])
    parse_request(d)


@pytest.mark.parametrize("color", ["red;drop table", "not a color", "black@2.0", "#ff00aa", "red:evil=1"])
def test_invalid_colors_rejected(color):
    d = request_doc([text_overlay_element(font_color=color)])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


# ---- Unicode
def test_unicode_text_accepted():
    d = request_doc([text_overlay_element(text="こんにちは、世界 🎬")])
    doc = parse_request(d)
    assert doc.elements[0].parameters["text"] == "こんにちは、世界 🎬"


def test_multiline_text_accepted():
    d = request_doc([text_overlay_element(text="line one\nline two")])
    doc = parse_request(d)
    assert "\n" in doc.elements[0].parameters["text"]


# ---- fonts (pure, no file system for font_id)
def test_default_font_used_when_absent():
    from motion_graphics.security import PathPolicy
    resolved = resolve_font(None, PathPolicy("."))
    assert resolved.font_id == DEFAULT_FONT_ID
    assert resolved.kind == "system"


def test_unknown_font_id_rejected():
    with pytest.raises(MotionGraphicsError) as e:
        resolve_font({"font_id": "system:comic-sans-ms"}, PathPolicy("."))
    assert e.value.code == "MISSING_INPUT"


def test_font_id_and_font_file_together_rejected():
    with pytest.raises(MotionGraphicsError) as e:
        resolve_font({"font_id": DEFAULT_FONT_ID, "font_file": "x.ttf"}, PathPolicy("."))
    assert e.value.code == "INVALID_REQUEST"


def test_font_registry_ids_stable():
    assert DEFAULT_FONT_ID in FONT_REGISTRY
    assert all(fid.startswith("system:") for fid in FONT_REGISTRY)


# ---- canonical json / identity
def test_canonical_json_is_key_order_independent():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b


def test_stable_hash_deterministic():
    obj = {"type": "title", "parameters": {"title": "x"}}
    assert stable_hash(obj) == stable_hash(dict(sorted(obj.items())))


def test_stable_hash_changes_with_content():
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


def test_sha256_text_matches_hashlib():
    import hashlib
    assert sha256_text("hello") == hashlib.sha256(b"hello").hexdigest()


# ---- error table
def test_error_table_matches_error_codes():
    assert set(ERROR_CODES) == set(ERROR_TABLE)
    assert set(EXIT_CODES) == set(ERROR_TABLE)


def test_error_table_has_the_specified_codes():
    expected = {"INVALID_REQUEST", "INVALID_INPUT", "UNSUPPORTED_OPERATION", "UNSUPPORTED_FORMAT", "INVALID_TIME_RANGE",
                "DEPENDENCY_ERROR", "PATH_NOT_ALLOWED", "MISSING_INPUT", "OUTPUT_ERROR", "VALIDATION_ERROR", "TOOL_ERROR",
                "CANCELLED", "INTERNAL_ERROR"}
    assert set(ERROR_CODES) == expected


def test_error_exit_codes_are_unique_and_nonzero():
    codes = list(EXIT_CODES.values())
    assert len(codes) == len(set(codes))
    assert all(c != 0 for c in codes)


def test_motion_graphics_error_rejects_unknown_code():
    with pytest.raises(ValueError):
        MotionGraphicsError("NOT_A_CODE", "x")


def test_error_to_dict_shape():
    e = MotionGraphicsError("INVALID_REQUEST", "bad", {"field": "x"})
    d = e.to_dict()
    assert d == {"code": "INVALID_REQUEST", "message": "bad", "retryable": False, "details": {"field": "x"}}


@pytest.mark.parametrize("code", ["TOOL_ERROR", "CANCELLED"])
def test_retryable_codes(code):
    assert ERROR_TABLE[code][1] is True


@pytest.mark.parametrize("code", [c for c in ERROR_TABLE if c not in ("TOOL_ERROR", "CANCELLED")])
def test_non_retryable_codes(code):
    assert ERROR_TABLE[code][1] is False


# ---- PathPolicy (pure filesystem checks, no ffmpeg needed)
def test_path_policy_rejects_missing_input(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_input("does-not-exist.mp4")
    assert e.value.code == "INVALID_INPUT"


def test_path_policy_resolves_existing_input(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"data")
    policy = PathPolicy(str(tmp_path))
    assert policy.resolve_input(str(f)) == f.resolve()


def test_path_policy_rejects_traversal_in_output(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_write_path("../escape.mp4")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_path_policy_rejects_input_outside_allowed_roots(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    f = other / "a.mp4"
    f.write_bytes(b"data")
    policy = PathPolicy(str(tmp_path), allowed_input_roots=[str(allowed)])
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_input(str(f))
    assert e.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.parametrize("name", ["CON", "con.txt", "NUL", "a\x01b", "trailing.", "trailing ", "-flag-like", "", ".", ".."])
def test_check_filename_rejects_unsafe_names(name):
    with pytest.raises(MotionGraphicsError):
        check_filename(name)


@pytest.mark.parametrize("name", ["a.mp4", "out.png", "my-file_v2.ttf"])
def test_check_filename_accepts_safe_names(name):
    check_filename(name)  # must not raise


# ---- doctor: pure capability/element-type/animation status logic (no ffmpeg-skill needed; regression coverage
# for two precision bugs found in review: a reliably-absent filter must read "unsupported" not "unknown", and an
# animation's status must reflect every element type it actually applies to, not just one of them)
from motion_graphics.doctor import animation_status, capability_status, element_type_status  # noqa: E402


def test_capability_reliably_absent_filter_is_unsupported_not_unknown():
    skill_info = type("Info", (), {"supported": True})()
    # ffmpeg-skill reliably detected filters (several are present) AND positively lists filter:drawtext in
    # "missing" -- it actually checked for this one and confirmed it absent, not merely undetected.
    ffdoc = {"ffmpeg": "6.0.0", "ffprobe": "6.0.0", "available": ["filter:overlay", "filter:scale", "encoder:libx264", "encoder:aac"],
             "missing": ["filter:drawtext"], "unknown": []}
    caps = capability_status(skill_info, ffdoc)
    assert caps["filter:drawtext"] == "unsupported"


def test_capability_unreliable_filter_detection_is_unknown():
    skill_info = type("Info", (), {"supported": True})()
    # No filter: entries at all in "available" -> ffmpeg-skill's filter detection itself is unreliable here
    # (e.g. FFmpeg 8+ output format it cannot parse), so an undetected filter is "unknown", not "unsupported",
    # even if (mistakenly, given the unreliable detection) it were also listed in "missing".
    ffdoc = {"ffmpeg": "8.0.0", "ffprobe": "8.0.0", "available": ["encoder:libx264", "encoder:aac"], "missing": ["filter:drawtext"], "unknown": []}
    caps = capability_status(skill_info, ffdoc)
    assert caps["filter:drawtext"] == "unknown"


def test_capability_missing_encoder_is_unsupported():
    skill_info = type("Info", (), {"supported": True})()
    ffdoc = {"ffmpeg": "6.0.0", "ffprobe": "6.0.0", "available": ["filter:drawtext"], "missing": ["encoder:aac"], "unknown": []}
    caps = capability_status(skill_info, ffdoc)
    assert caps["encoder:aac"] == "unsupported"


def test_capability_not_tracked_by_ffmpeg_skill_at_all_is_unknown_not_unsupported():
    # Regression: ffmpeg-skill's own doctor only tracks capabilities *its own* tools need. Core filters this
    # skill needs (overlay, drawbox, color, scale, colorchannelmixer) are common enough that ffmpeg-skill never
    # mentions them in available/missing/unknown at all -- absence-from-available alone must never be read as
    # "confirmed unsupported" (it previously was, incorrectly, until this was caught by manually inspecting a
    # real doctor run against a real ffmpeg-skill checkout).
    skill_info = type("Info", (), {"supported": True})()
    ffdoc = {"ffmpeg": "6.1.1", "ffprobe": "6.1.1", "available": ["filter:drawtext", "filter:loudnorm", "encoder:libx264", "encoder:aac"], "missing": [], "unknown": []}
    caps = capability_status(skill_info, ffdoc)
    assert caps["filter:overlay"] == "unknown"
    assert caps["filter:drawbox"] == "unknown"
    assert caps["filter:color"] == "unknown"
    assert caps["filter:scale"] == "unknown"
    assert caps["filter:colorchannelmixer"] == "unknown"
    assert caps["filter:drawtext"] == "supported"


def test_animation_status_reflects_worst_of_every_applicable_element_type():
    # fade applies to both text_overlay and image_overlay (model.ELEMENT_TYPES); if either is unsupported the
    # animation itself must be reported unsupported, not just reflect text_overlay's own status.
    fully_supported = {t: {"status": "supported"} for t in ELEMENT_TYPES}
    assert animation_status(fully_supported)["fade"]["status"] == "supported"

    image_overlay_broken = dict(fully_supported, image_overlay={"status": "unsupported"})
    assert animation_status(image_overlay_broken)["fade"]["status"] == "unsupported"

    text_overlay_unknown = dict(fully_supported, text_overlay={"status": "unknown"})
    assert animation_status(text_overlay_unknown)["fade"]["status"] == "unknown"


def test_animation_status_applies_to_matches_contract():
    from motion_graphics.contract import animation_specs
    fully_supported = {t: {"status": "supported"} for t in ELEMENT_TYPES}
    doctor_applies_to = sorted(animation_status(fully_supported)["fade"]["applies_to"])
    contract_applies_to = sorted(next(a["applies_to"] for a in animation_specs() if a["kind"] == "fade"))
    assert doctor_applies_to == contract_applies_to == ["image_overlay", "text_overlay"]


def test_element_type_status_escalates_to_worst_required_capability():
    caps = {"ffmpeg-skill": "supported", "ffmpeg": "supported", "ffprobe": "supported",
            "filter:drawtext": "supported", "filter:drawbox": "unsupported", "encoder:libx264": "supported"}
    statuses = element_type_status(caps)
    assert statuses["title"]["status"] == "unsupported"
    assert "filter:drawbox" in statuses["title"]["missing"]


# ---- executor: ADR-9 regression -- a custom font_file must render with cwd set to its own directory and a bare
# file name, never a full path, on every platform (not just the Windows build where the original bug appeared)
from motion_graphics.executor import Executor  # noqa: E402
from motion_graphics.fonts import ResolvedFont  # noqa: E402
from motion_graphics.model import parse_request as _parse_request  # noqa: E402


def _text_overlay_element_obj(**extra):
    doc = _parse_request(request_doc([text_overlay_element(**extra)]))
    return doc.elements[0]


def test_argv_for_custom_font_file_uses_bare_name_and_sets_cwd(tmp_path):
    ex = Executor(PathPolicy(str(tmp_path)), skill=None)
    font_dir = tmp_path / "somewhere" / "nested"
    font_dir.mkdir(parents=True)
    font_path = font_dir / "MyFont.ttf"
    font_path.write_bytes(b"not a real font, just needs to exist for the path")
    font = ResolvedFont("file", None, "MyFont", {}, font_file_hash="deadbeef", font_file_path=str(font_path))

    el = _text_overlay_element_obj(text="hi")
    tool, argv, cwd = ex._argv(el, "in.mp4", "out.mp4", None, font, crf=18, preset="medium")

    assert tool == "overlay"
    assert cwd == str(font_dir)
    i = argv.index("--font-file")
    font_arg = argv[i + 1]
    assert font_arg == "MyFont.ttf"
    assert "/" not in font_arg and "\\" not in font_arg and ":" not in font_arg


def test_argv_for_system_font_sets_no_cwd():
    ex = Executor(PathPolicy("."), skill=None)
    font = ResolvedFont("system", DEFAULT_FONT_ID, "DejaVu Sans", {"font": "DejaVu Sans"})
    el = _text_overlay_element_obj(text="hi")
    tool, argv, cwd = ex._argv(el, "in.mp4", "out.mp4", None, font, crf=18, preset="medium")
    assert cwd is None
    assert "--font" in argv and "DejaVu Sans" in argv


def test_argv_for_title_never_sets_cwd():
    ex = Executor(PathPolicy("."), skill=None)
    doc = _parse_request(request_doc([title_element()]))
    tool, argv, cwd = ex._argv(doc.elements[0], "in.mp4", "out.mp4", None, None, crf=18, preset="medium")
    assert tool == "graphics"
    assert cwd is None


# ---- deterministic identity (STEP 13 / review section 7): same input -> same identity, and every field the
# spec says matters (asset content, font choice, animation) actually changes it. Pure -- no ffmpeg-skill needed.
def _image_overlay_element_obj(**extra):
    doc = _parse_request(request_doc([{"id": "img1", "type": "image_overlay", "start": 0, "end": 2, "parameters": {"image_path": "logo.png", **extra}}]))
    return doc.elements[0]


def test_identity_parameters_never_contain_a_raw_path():
    el = _image_overlay_element_obj()
    asset = {"sha256": "a" * 64, "size": 123}
    params = Executor._identity_parameters(el, asset, None)
    assert params["image_path"] == {"sha256": "a" * 64, "size": 123}
    assert "logo.png" not in str(params)


def test_identity_parameters_differ_for_different_image_content():
    el = _image_overlay_element_obj()
    params_a = Executor._identity_parameters(el, {"sha256": "a" * 64, "size": 100}, None)
    params_b = Executor._identity_parameters(el, {"sha256": "b" * 64, "size": 100}, None)
    assert stable_hash(params_a) != stable_hash(params_b)


def test_identity_parameters_differ_for_different_font():
    el = _text_overlay_element_obj(text="hi")
    font_a = ResolvedFont("system", "system:dejavu-sans", "DejaVu Sans", {"font": "DejaVu Sans"})
    font_b = ResolvedFont("system", "system:dejavu-serif", "DejaVu Serif", {"font": "DejaVu Serif"})
    params_a = Executor._identity_parameters(el, None, font_a)
    params_b = Executor._identity_parameters(el, None, font_b)
    assert stable_hash(params_a) != stable_hash(params_b)


def test_identity_parameters_same_content_same_hash():
    el = _text_overlay_element_obj(text="hi")
    font = ResolvedFont("system", DEFAULT_FONT_ID, "DejaVu Sans", {"font": "DejaVu Sans"})
    params_a = Executor._identity_parameters(el, None, font)
    params_b = Executor._identity_parameters(el, None, font)
    assert stable_hash(params_a) == stable_hash(params_b)


def test_stage_identity_differs_for_different_animation():
    base = {"skill_version": "0.1.0", "tool_versions": {}, "index": 0, "previous": "x", "type": "text_overlay",
            "start": 0.0, "end": 2.0, "parameters": {}, "crf": 18, "preset": "medium"}
    no_fade = stable_hash({**base, "animation": None})
    fade_1s = stable_hash({**base, "animation": {"kind": "fade", "parameters": {"duration": 1.0}}})
    fade_2s = stable_hash({**base, "animation": {"kind": "fade", "parameters": {"duration": 2.0}}})
    assert len({no_fade, fade_1s, fade_2s}) == 3  # all three distinct


def test_stage_identity_chains_to_previous_stage():
    base = {"skill_version": "0.1.0", "tool_versions": {}, "index": 1, "type": "title", "start": 0.0, "end": 2.0,
            "animation": None, "parameters": {}, "crf": 18, "preset": "medium"}
    from_a = stable_hash({**base, "previous": "identity-of-stage-a"})
    from_b = stable_hash({**base, "previous": "identity-of-stage-b"})
    assert from_a != from_b  # a document that differs only in an earlier stage must not collide downstream
