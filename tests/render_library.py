"""Run by test_library_passes.py with the HFB backend's Python (cwd = backend):
prints, as JSON, what the library's features send that the content check
reads: packages/ and themes/ files, dashboard sections, Node-RED flows and
go2rtc keys, rendered with dummy parameter values (defaults where the
feature has them)."""

import json
import sys
from pathlib import Path

from application.services.feature_plan_service import _fan_out
from application.services.rendering_service import RenderingService, UnknownPlaceholderError
from domain.entities import Component
from domain.feature_plan import DesiredObject
from infrastructure.node_red import NodeRedApplicator

DUMMY = {
    "number": "1",
    "boolean": "true",
    "entity_id": "sensor.x",
    "switch": "switch.x",
    "entity_list": '["sensor.a", "sensor.b"]',
    "device_list": '["dev_a", "dev_b"]',
    "device_groups": '{"g1": ["dev_a", "binary_sensor.w"]}',
    "dashboard_view": '{"dashboard": null, "view": "v"}',
    "go2rtc_camera_list": '["cam_a"]',
    "price_lists": "[]",
}
KINDS = ("node_red_flow", "go2rtc_config", "dashboard_section")


def _retry(render, values: dict[str, str]) -> str:
    while True:
        try:
            return render(values)
        except UnknownPlaceholderError as e:  # a linked feature's value (feature_instance)
            values[e.placeholder_name] = "sensor.x"


def _checked(c: dict) -> bool:
    return c["component_type"] in KINDS or c["filename"].startswith(("packages/", "themes/"))


out = []
for path in sorted(Path(sys.argv[1]).glob("*/*.json")):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or "components" not in data:
        continue
    values = {"instance": "i1"}
    for p in data["parameters"]:
        values[p["name"]] = p.get("default_value") or DUMMY.get(p["param_type"], "x")
    for c in filter(_checked, data["components"]):
        for each in _fan_out(c["filename"] + "\n" + c["content"], values):
            each = dict(each)
            component = Component(c["component_type"], c["filename"], c["content"])
            name = _retry(lambda v, f=c["filename"]: RenderingService().render_text(f, v, ""), each)
            text = _retry(
                lambda v, k=component: RenderingService().render_component(k, [], v).content, each
            )
            if c["component_type"] == "node_red_flow":  # as Apply sends it: IDs and marker
                marker = f"ha-builder: {data['id']}@{data['version']}"
                obj = DesiredObject("node_red_flow", name, text, data["id"], marker=marker)
                text = NodeRedApplicator(None).normalize(obj)
            out.append(
                {"feature": data["id"], "kind": c["component_type"], "name": name, "content": text}
            )
json.dump(out, sys.stdout)
