"""Security boundary tests (docs/security.md). Pure / structural tests run always; the payload-as-content test at
the bottom needs real ffmpeg-skill + ffmpeg and is skipped-by-failure like the rest of the integration suite via
the `workspace`/`skill_dir` fixtures (see conftest.py: nothing here silently skips)."""
import inspect
import json

import pytest

from motion_graphics import adapter
from motion_graphics.errors import MotionGraphicsError
from motion_graphics.model import FORBIDDEN_KEYS, parse_request
from motion_graphics.security import PathPolicy

from conftest import bug_element, chapter_element, countdown_element, progress_element, request_doc, run_cli, text_overlay_element, title_element, one_json


# ---- no shell, ever
def test_no_shell_true_anywhere_in_source():
    import motion_graphics
    import pathlib
    pkg_dir = pathlib.Path(motion_graphics.__file__).parent
    for py in pkg_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "shell=True" not in text, f"shell=True found in {py}"


def test_popen_called_with_a_list_not_a_string():
    src = inspect.getsource(adapter.FfmpegSkill._popen)
    assert "subprocess.Popen(list(argv)" in src


# ---- executable allowlist
def test_tool_allowlist_is_closed():
    assert adapter.TOOLS_USED == ("probe", "graphics", "overlay")
    skill = adapter.FfmpegSkill.__new__(adapter.FfmpegSkill)
    with pytest.raises(MotionGraphicsError) as e:
        skill.script("cut")  # not on the allowlist, even though ffmpeg-skill itself has this tool
    assert e.value.code == "INTERNAL_ERROR"


# ---- forbidden fields, recursively, anywhere
@pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
def test_forbidden_keys_rejected_at_top_level(key):
    d = request_doc([title_element()])
    d[key] = "x"
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"
    assert e.value.details.get("reason") == "forbidden_field"


@pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
def test_forbidden_keys_rejected_nested_in_element_parameters(key):
    d = request_doc([{"id": "t1", "type": "title", "start": 0, "end": 1, "parameters": {"title": "x", key: "y"}}])
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code in ("INVALID_REQUEST",)


@pytest.mark.parametrize("make_element", [title_element, bug_element, chapter_element, progress_element, countdown_element])
def test_forbidden_field_rejected_for_every_element_type(make_element):
    # _reject_forbidden() runs on the raw document before any type-specific parameter parsing, so it should catch
    # a forbidden field identically regardless of which element type carries it -- this pins that generality down
    # explicitly for every type, rather than leaving it as an implicit assumption verified only for `title` above.
    el = make_element()
    el["parameters"]["filter"] = "y"
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(request_doc([el]))
    assert e.value.code == "INVALID_REQUEST"
    assert e.value.details.get("reason") == "forbidden_field"


@pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
def test_forbidden_keys_rejected_nested_in_animation(key):
    d = request_doc([{**text_overlay_element(start=0, end=4), "animation": {"kind": "fade", "parameters": {"duration": 0.5}, key: "y"}}])
    with pytest.raises(MotionGraphicsError):
        parse_request(d)


def test_forbidden_keys_case_insensitive():
    d = request_doc([title_element()])
    d["Filter_Complex"] = "x"
    with pytest.raises(MotionGraphicsError) as e:
        parse_request(d)
    assert e.value.code == "INVALID_REQUEST"


# ---- path traversal / symlink escape
def test_path_traversal_rejected(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_write_path("sub/../../escape.mp4")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path.parent / "outside_escape_target"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.mp4"
    secret.write_bytes(b"data")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "link.mp4"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem/platform")
    policy = PathPolicy(str(workspace), allowed_input_roots=[str(workspace)])
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_input(str(link))
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_symlinked_directory_escape_rejected_for_output(tmp_path):
    outside = tmp_path.parent / "outside_escape_dir"
    outside.mkdir(exist_ok=True)
    workspace = tmp_path / "ws2"
    workspace.mkdir()
    link_dir = workspace / "linked"
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem/platform")
    policy = PathPolicy(str(workspace))
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_write_path("linked/out.mp4")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_output_cannot_be_input(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"data")
    policy = PathPolicy(str(tmp_path))
    video = policy.resolve_input(str(f))
    output = policy.resolve_write_path(str(f))
    assert video == output  # this is exactly what executor.response() checks and rejects as OUTPUT_ERROR


# ---- environment allowlist
def test_env_allowlist_excludes_arbitrary_variables(monkeypatch):
    monkeypatch.setenv("SOME_RANDOM_SECRET", "leak-me")
    env = adapter._clean_env()
    assert "SOME_RANDOM_SECRET" not in env
    assert "PATH" in env


# ---- request document cannot smuggle a shell metacharacter into argv positions we control
def test_null_byte_in_path_rejected(tmp_path):
    policy = PathPolicy(str(tmp_path))
    with pytest.raises(MotionGraphicsError) as e:
        policy.resolve_input("bad\x00path.mp4")
    assert e.value.code == "PATH_NOT_ALLOWED"


def test_argv_with_null_byte_rejected():
    skill = adapter.FfmpegSkill(__import__("pathlib").Path("."))
    with pytest.raises(MotionGraphicsError) as e:
        skill._popen(["python3", "bad\x00arg"], 5)
    assert e.value.code == "INTERNAL_ERROR"


# ---- no HTML/CSS/JS execution engine exists in this codebase
def test_no_html_css_js_engine_in_source():
    import motion_graphics
    import pathlib
    pkg_dir = pathlib.Path(motion_graphics.__file__).parent
    banned = ("BeautifulSoup", "selenium", "playwright", "puppeteer", "eval(", "exec(", "__import__(")
    for py in pkg_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{token!r} found in {py}"


# ---- CLI-level: forbidden field via the real process boundary
def test_cli_run_rejects_forbidden_field(tmp_path):
    doc = request_doc([title_element()])
    doc["shell"] = "rm -rf /"
    code, out, err = run_cli(["run", "-", "--json", "--workspace", str(tmp_path)], stdin_text=json.dumps(doc))
    result = one_json(out)
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert code != 0


def test_cli_validate_rejects_shell_injection_in_filter_field(tmp_path):
    doc = request_doc([title_element()])
    doc["elements"][0]["parameters"]["filter"] = "x=1[out]"
    code, out, err = run_cli(["validate", "-", "--json"], stdin_text=json.dumps(doc), cwd=str(tmp_path))
    result = one_json(out)
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
