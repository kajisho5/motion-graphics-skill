"""The version lives in exactly one place: pyproject.toml (ADR-21). Everything that reports a version -- the package,
the contract (top level and its tool spec), the CLI -- must say the same thing, and the release workflow's own bump
script must be enough to move all of them."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import motion_graphics
from motion_graphics import VERSION
from motion_graphics.contract import skill_contract

from conftest import one_json, run_cli

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version(root: Path = ROOT) -> str:
    found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert found, "pyproject.toml has no version line"
    return found.group(1)


def test_version_is_the_pyproject_version():
    assert VERSION == _pyproject_version()
    assert VERSION != "0.0.0+unknown"


def test_contract_reports_the_pyproject_version():
    c = skill_contract()
    assert c["version"] == _pyproject_version()
    assert {t["version"] for t in c["tools"]} == {_pyproject_version()}


def test_cli_contract_and_skill_report_the_pyproject_version(tmp_path):
    for command in ("contract", "skill"):
        code, out, err = run_cli([command, "--json"], cwd=str(tmp_path))
        assert code == 0, err
        assert one_json(out)["version"] == _pyproject_version()
    code, out, _ = run_cli(["--version"], cwd=str(tmp_path))
    assert code == 0 and out.strip() == f"motion-graphics-skill {_pyproject_version()}"


def _version_in(python_path: Path, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(python_path)
    code = ("import motion_graphics; from motion_graphics.contract import skill_contract; c = skill_contract(); "
            "assert c['version'] == motion_graphics.VERSION == c['tools'][0]['version']; print(c['version'])")
    # -S: no site-packages, so an installed (e.g. editable) motion-graphics-skill cannot answer in place of `python_path`
    proc = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, env=env, cwd=str(cwd))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_release_bump_script_moves_every_reported_version(tmp_path):
    """Simulate release.yml's auto-bump on a copy of this checkout: bump_version.py rewrites only pyproject.toml, and
    the package, the contract and its tool spec all follow (the script itself also checks this before exiting)."""
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    new_version = "9.8.7"
    env = dict(os.environ, NEW_VERSION=new_version)
    proc = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "bump_version.py")], capture_output=True, text=True,
                          env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert _pyproject_version(tmp_path) == new_version
    assert _version_in(tmp_path / "src", tmp_path) == new_version


def test_installed_copy_without_pyproject_reads_distribution_metadata(tmp_path):
    """An installed wheel has no pyproject.toml next to it: the version then comes from the distribution metadata
    (built from that same pyproject.toml), and with no metadata either it is 0.0.0+unknown, never a guess."""
    site = tmp_path / "site"
    shutil.copytree(Path(motion_graphics.__file__).parent, site / "motion_graphics", ignore=shutil.ignore_patterns("__pycache__"))
    assert _version_in(site, tmp_path) == "0.0.0+unknown"
    dist_info = site / "motion_graphics_skill-4.5.6.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: motion-graphics-skill\nVersion: 4.5.6\n", encoding="utf-8")
    assert _version_in(site, tmp_path) == "4.5.6"
