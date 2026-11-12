"""The current HFB library passes the content check (#557):
HFB_REPO=~/Projects/ha-features-builder pytest -q tests/test_library_passes.py
Needs HFB_REPO/backend/.venv (uv sync); skipped without the checkout."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from connector import content

HFB = Path(os.environ.get("HFB_REPO", Path(__file__).parents[2] / "ha-features-builder"))
PYTHON = HFB / "backend" / ".venv" / "bin" / "python"


@pytest.fixture(scope="module")
def rendered() -> list[dict]:
    if not PYTHON.exists():
        pytest.skip(f"no HFB checkout with backend/.venv at {HFB}")
    out = subprocess.run(
        [PYTHON, Path(__file__).with_name("render_library.py"), HFB / "library"],
        cwd=HFB / "backend",
        env={**os.environ, "APP_ENV": "dev", "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def test_the_features_with_checked_content_are_all_seen(rendered):
    assert {r["feature"] for r in rendered} == {
        "bento",
        "cameras",
        "energy",
        "go2rtc",
        "heating",
        "homecraft",
        "internet",
        "notifications",
        "observability",
    }


def test_packages_pass(rendered):
    packages = [r for r in rendered if r["name"].startswith("packages/")]
    assert len(packages) >= 9
    for r in packages:
        assert content.check_yaml(r["name"], r["content"]) is None, r["name"]


def test_themes_pass(rendered):
    themes = [r for r in rendered if r["name"].startswith("themes/")]
    assert themes
    for r in themes:
        assert content.check_tags(r["content"]) is None, r["name"]


def test_dashboard_sections_pass(rendered):
    """Unexpanded: every $switch case and $each card is walked."""
    sections = [r for r in rendered if r["kind"] == "dashboard_section"]
    assert len(sections) >= 4
    for r in sections:
        assert content.check_dashboard(json.loads(r["content"])) is None, r["name"]


USER = [  # the user's own flow next to ours, a server node and an exec node
    {"id": "u_tab", "type": "tab"},
    {"id": "u_srv", "type": "server"},
    {"id": "u_exec", "type": "exec", "z": "u_tab", "wires": [[]]},
]
HA_SERVER = '"$ha_server"'  # node_red.HA_SERVER, resolved at Apply


def _flows(r: dict) -> list[dict]:
    return json.loads(r["content"].replace(HA_SERVER, '"u_srv"'))


def test_node_red_flows_pass_as_a_new_tab_and_as_an_update(rendered):
    flows = [r for r in rendered if r["kind"] == "node_red_flow"]
    assert flows
    for r in flows:
        nodes = _flows(r)
        body = json.dumps({"rev": "1", "flows": USER + nodes}).encode()
        assert content.check_nodered_flows(body, {"rev": "1", "flows": USER}) is None
        changed = [{**n, "name": "x"} for n in nodes]  # every node, the tab and subflows too
        body = json.dumps({"rev": "2", "flows": USER + changed}).encode()
        live = {"rev": "2", "flows": USER + nodes}
        assert content.check_nodered_flows(body, live) is None, r["feature"]


def test_node_types_are_the_librarys(rendered):
    """NODE_TYPES = exactly what the library deploys: a new type shows up here."""
    used = {
        n["type"]
        for r in rendered
        if r["kind"] == "node_red_flow"
        for n in _flows(r)
        if not n["type"].startswith("subflow:")
    }
    assert used == content.NODE_TYPES


def test_go2rtc_keys_are_the_librarys(rendered):
    assert {r["name"] for r in rendered if r["kind"] == "go2rtc_config"} == content.GO2RTC_KEYS
