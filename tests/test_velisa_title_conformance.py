"""The title.json reader (ADR-33) against the Velisa spec owner's conformance fixtures (Velisa ADR-024).

tests/fixtures/velisa/ is a copy of Velisa registry/ for `title`, made in a Velisa checkout with
`python3 -m registry.spec_conformance --export title --to <this repo>/tests/fixtures/velisa`; manifest.json records the
Velisa commit. Re-export to update, never edit by hand. Every valid fixture must be read and mapped by the spec's table;
every invalid one must be either refused here or listed below with the reason this reader leaves it to another check.
When jsonschema is installed, the fixtures are also checked against the copied schema (the Velisa CI always does)."""
import hashlib
import json
from pathlib import Path

import pytest

from motion_graphics.errors import MotionGraphicsError
from motion_graphics.security import PathPolicy
from motion_graphics.titlejson import map_title, resolve_title_json

VELISA = Path(__file__).resolve().parent / "fixtures" / "velisa"
MANIFEST = json.loads((VELISA / "manifest.json").read_text(encoding="utf-8"))
FIXTURES = MANIFEST["specs"]["title"]["fixtures"]
VALID = [f for f in FIXTURES if f["expect"] == "valid"]
INVALID = [f for f in FIXTURES if f["expect"] == "invalid"]

# Invalid fixtures map_title does not refuse, and why. A new invalid fixture that is neither refused nor listed fails
# test_every_invalid_fixture_is_refused_or_explained, so each one is a decision.
NOT_REFUSED_BY_MAP_TITLE = {
    "conformance/title/invalid/sha256-uppercase.json": "sha256 is compared with the clip's case-insensitively (resolve_title_json)",
    "conformance/title/invalid/segment-not-whole-frames.json": "frame_rate is not read; the segments are checked against the clip's "
                                                                "probed length within one frame (title_json_segments_mismatch)",
    "conformance/title/invalid/segment-frames-disagree.json": "segment_frames is not read (not in titlejson.READS)",
}


def _doc(f):
    return json.loads((VELISA / f["path"]).read_text(encoding="utf-8"))


def _fps(doc):
    num, den = doc.get("frame_rate", "30/1").split("/")
    return int(num) / int(den)


def test_the_copy_is_velisas():
    assert len(MANIFEST["source"]["commit"]) == 40
    entry = MANIFEST["specs"]["title"]
    assert hashlib.sha256((VELISA / entry["schema"]).read_bytes()).hexdigest() == entry["schema_sha256"]
    for f in FIXTURES:
        assert hashlib.sha256((VELISA / f["path"]).read_bytes()).hexdigest() == f["sha256"], f["path"]
    assert VALID and INVALID


@pytest.mark.parametrize("f", VALID, ids=lambda f: f["path"].rsplit("/", 1)[1])
def test_a_valid_title_json_is_read_and_mapped_by_the_spec_table(f, tmp_path):
    doc = _doc(f)
    seg = doc["segments"]
    title_json = tmp_path / "clip.title.json"
    title_json.write_text(json.dumps(doc), encoding="utf-8")
    clip = tmp_path / doc.get("file", "clip.mov")
    clip_sha = doc.get("sha256", "ab" * 32)
    params, record, _ = resolve_title_json(PathPolicy(str(tmp_path)), "lyr1", {"video_path": clip.name, "title_json": title_json.name},
                                           clip, clip_sha, seg["in"] + seg["loop"] + seg["out"], _fps(doc))
    # docs/specs/title-and-stinger.md: segments.in -> in_duration, segments.out -> out_duration, segments.loop > 0 -> loop
    assert params["in_duration"] == seg["in"] and params["out_duration"] == seg["out"] and params["loop"] == (seg["loop"] > 0)
    assert record["sources"] == {"in_duration": "title_json", "out_duration": "title_json", "loop": "title_json"}
    assert record["layer_matched_by"] == ("sha256" if "sha256" in doc else "file" if "file" in doc else None)


@pytest.mark.parametrize("f", INVALID, ids=lambda f: f["path"].rsplit("/", 1)[1])
def test_every_invalid_fixture_is_refused_or_explained(f):
    if f["path"] in NOT_REFUSED_BY_MAP_TITLE:
        map_title(_doc(f), "title_json")  # read without complaint; another check (or none) catches it, as listed
        return
    with pytest.raises(MotionGraphicsError) as e:
        map_title(_doc(f), "title_json")
    assert e.value.code == "INVALID_INPUT"


def test_the_explained_list_names_only_existing_fixtures():
    assert set(NOT_REFUSED_BY_MAP_TITLE) <= {f["path"] for f in INVALID}


def test_the_fixtures_match_the_copied_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((VELISA / MANIFEST["specs"]["title"]["schema"]).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for f in FIXTURES:
        assert validator.is_valid(_doc(f)) == ("schema" not in f["fails"]), f["path"]
