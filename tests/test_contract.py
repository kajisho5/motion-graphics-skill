"""Contract <-> implementation consistency; contract/doctor are deterministic and valid JSON; `skill` is an alias
of `contract`. Pure / structural where possible; doctor's environment-dependent fields are only checked for
shape, not for specific values (those are covered by test_integration.py against a real ffmpeg-skill)."""
import json

from motion_graphics import VERSION
from motion_graphics.contract import skill_contract
from motion_graphics.doctor import doctor_report
from motion_graphics.errors import ERROR_CODES
from motion_graphics.model import ANIMATION_KINDS, ELEMENT_TYPES, UNSUPPORTED_ANIMATIONS, UNSUPPORTED_ELEMENT_TYPES

from conftest import run_cli, one_json


def test_contract_is_deterministic():
    a = json.dumps(skill_contract(), sort_keys=True)
    b = json.dumps(skill_contract(), sort_keys=True)
    assert a == b


def test_contract_version_matches_package_version():
    c = skill_contract()
    assert c["version"] == VERSION
    assert c["tools"][0]["version"] == VERSION


def test_contract_element_types_match_implementation_table():
    c = skill_contract()
    contract_types = {t["type"] for t in c["element_types"]}
    assert contract_types == set(ELEMENT_TYPES)


def test_contract_unsupported_element_types_do_not_overlap_supported():
    c = skill_contract()
    supported = {t["type"] for t in c["element_types"]}
    unsupported = {t["type"] for t in c["unsupported_element_types"]}
    assert supported.isdisjoint(unsupported)
    assert unsupported == set(UNSUPPORTED_ELEMENT_TYPES)


def test_contract_animations_match_implementation_table():
    c = skill_contract()
    assert {a["kind"] for a in c["animations"]} == set(ANIMATION_KINDS)
    assert {a["kind"] for a in c["unsupported_animations"]} == set(UNSUPPORTED_ANIMATIONS)


def test_contract_error_codes_match_error_table():
    c = skill_contract()
    assert set(c["errors"]["codes"]) == set(ERROR_CODES)


def test_contract_json_is_valid_json():
    text = json.dumps(skill_contract())
    json.loads(text)  # must not raise


def test_contract_every_element_type_names_a_delegate_tool():
    c = skill_contract()
    for t in c["element_types"]:
        assert t["tool"].startswith("ffmpeg-skill/")


def test_contract_documents_all_three_response_shapes():
    # An agent parsing only the contract must be able to tell `run`, `plan` (dry-run), and `validate` responses
    # apart -- they have different top-level keys, and only `run`'s shape was documented before this check existed.
    c = skill_contract()
    shapes = c["response"]["success"]
    assert set(shapes) == {"run", "plan", "validate"}
    assert "output" in shapes["run"] and "operations" in shapes["run"]
    assert "plan" in shapes["plan"] and "output" not in shapes["plan"]
    assert "validation" in shapes["validate"] and "output" not in shapes["validate"]


def test_contract_provides_one_capability_entry_per_element_type():
    # `provides` is read by kajisho5/AI-video-production-OS's cross-repository Capability registry (its
    # `registry.contract.validate_provides_entry` requires exactly `id` / `tool_id` (non-empty strings) and a
    # `lifecycle` from a fixed enum; extra fields are permitted and ignored). One entry per ELEMENT_TYPES key;
    # `text_overlay` and `image_overlay` intentionally share one capability id (see ADR-10).
    c = skill_contract()
    provides = c["provides"]
    assert {p["element_type"] for p in provides} == set(ELEMENT_TYPES)
    for p in provides:
        assert isinstance(p["id"], str) and p["id"]
        assert p["tool_id"] == "motion-graphics/run"
        assert p["lifecycle"] in ("PROPOSED", "EXPERIMENTAL", "STABLE", "DEPRECATED", "RETIRED")
    by_type = {p["element_type"]: p["id"] for p in provides}
    assert by_type["title"] == "motion_graphics.title_card"
    assert by_type["lower_third"] == "motion_graphics.lower_third"
    assert by_type["text_overlay"] == by_type["image_overlay"] == "motion_graphics.overlay"


def test_contract_forbidden_fields_present():
    c = skill_contract()
    for key in ("filter", "filter_complex", "shell", "command", "argv", "executable", "env"):
        assert key in c["request"]["forbidden_fields"]


# ---- CLI-level: skill/contract alias, and both are exactly one JSON document
def test_cli_skill_and_contract_are_identical(tmp_path):
    code1, out1, _ = run_cli(["skill", "--json"], cwd=str(tmp_path))
    code2, out2, _ = run_cli(["contract", "--json"], cwd=str(tmp_path))
    assert code1 == 0 and code2 == 0
    assert one_json(out1) == one_json(out2)


def test_cli_contract_stdout_is_exactly_one_json_document(tmp_path):
    code, out, err = run_cli(["contract", "--json"], cwd=str(tmp_path))
    assert code == 0
    one_json(out)  # raises if there is more than a single JSON document / trailing garbage


def test_cli_doctor_reports_shape(tmp_path):
    code, out, err = run_cli(["doctor", "--json"], cwd=str(tmp_path))
    doc = one_json(out)
    assert doc["schema"].startswith("motion-graphics/doctor@")
    assert doc["status"] in ("ok", "degraded", "fail")
    assert set(doc["checks"]["element_types"]) == set(ELEMENT_TYPES)
    assert doc["secrets_shown"] is False


def test_doctor_report_never_claims_support_for_unsupported_element_types():
    doc = doctor_report()
    for etype in UNSUPPORTED_ELEMENT_TYPES:
        assert etype not in doc["checks"]["element_types"]
    assert set(doc["checks"]["unsupported_element_types"]) == set(UNSUPPORTED_ELEMENT_TYPES)
