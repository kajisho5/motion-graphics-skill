import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from fixtures.generate import available, build_all  # noqa: E402
from motion_graphics.adapter import FfmpegSkill  # noqa: E402
from motion_graphics.errors import MotionGraphicsError  # noqa: E402


def ffmpeg_skill_dir() -> Path:
    """The ffmpeg-skill checkout the integration tests run against. Nothing is skipped: a missing checkout fails."""
    try:
        return FfmpegSkill.locate(os.environ.get("MOTION_GRAPHICS_FFMPEG_SKILL_DIR")).directory
    except MotionGraphicsError as e:
        pytest.fail(f"ffmpeg-skill checkout is required for the integration tests (set MOTION_GRAPHICS_FFMPEG_SKILL_DIR or clone ./vendor/ffmpeg-skill): {e.message} tried={e.details.get('tried')}")


@pytest.fixture(scope="session")
def skill_dir() -> Path:
    return ffmpeg_skill_dir()


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    if not available():
        pytest.fail("ffmpeg / ffprobe are required for the integration tests (install FFmpeg); they are not skipped")
    return build_all(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def workspace(tmp_path, media):
    """A fresh workspace with copies of the fixtures; the process cwd is moved there for the test."""
    ws = tmp_path / "ws"
    ws.mkdir()
    for p in media.values():
        if p.is_file():
            shutil.copy(p, ws / p.name)
    old = os.getcwd()
    os.chdir(ws)
    try:
        yield ws
    finally:
        os.chdir(old)


def request_doc(elements, video="video.mp4", output="out/out.mp4", overwrite=False, options=None):
    doc = {"schema": "motion-graphics/request@1", "video": {"path": video}, "output": {"path": output, "overwrite": overwrite}, "elements": elements}
    if options:
        doc["options"] = options
    return doc


def title_element(element_id="t1", start=0.0, end=2.0, title="Hello", **extra):
    return {"id": element_id, "type": "title", "start": start, "end": end, "parameters": {"title": title, **extra}}


def text_overlay_element(element_id="txt1", start=0.0, end=2.0, text="Hello", **extra):
    return {"id": element_id, "type": "text_overlay", "start": start, "end": end, "parameters": {"text": text, **extra}}


def image_overlay_element(element_id="img1", start=0.0, end=2.0, image_path="logo.png", **extra):
    return {"id": element_id, "type": "image_overlay", "start": start, "end": end, "parameters": {"image_path": image_path, **extra}}


def bug_element(element_id="bug1", start=0.0, end=2.0, title="LIVE", **extra):
    return {"id": element_id, "type": "bug", "start": start, "end": end, "parameters": {"title": title, **extra}}


def run_cli(args, stdin_text=None, cwd=None):
    """Run the CLI in a subprocess (the real process boundary) and return (exit, stdout, stderr)."""
    import subprocess
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE.parent / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("MOTION_GRAPHICS_FFMPEG_SKILL_DIR", str(ffmpeg_skill_dir()))
    proc = subprocess.run([sys.executable, "-m", "motion_graphics.cli", *args], input=stdin_text, capture_output=True, text=True, env=env, cwd=cwd)
    return proc.returncode, proc.stdout, proc.stderr


def one_json(text: str):
    """stdout must be exactly one JSON document."""
    doc = json.loads(text)
    assert isinstance(doc, dict)
    return doc
