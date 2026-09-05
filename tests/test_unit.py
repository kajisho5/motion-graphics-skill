"""Model validation, timeline rules, canonical JSON / identity determinism, the error table, PathPolicy, fonts.
No ffmpeg, no ffmpeg-skill, no file system access beyond tmp_path -- these tests always run."""
import pytest

from motion_graphics.canonical import canonical_json, sha256_text, stable_hash
from motion_graphics.errors import ERROR_CODES, ERROR_TABLE, EXIT_CODES, MotionGraphicsError
from motion_graphics.fonts import DEFAULT_FONT_ID, FONT_REGISTRY, resolve_font
from motion_graphics.model import ELEMENT_TYPES, UNSUPPORTED_ANIMATIONS, UNSUPPORTED_ELEMENT_TYPES, parse_request
from motion_graphics.security import PathPolicy, check_filename

from conftest import request_doc, text_overlay_element, title_element


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
