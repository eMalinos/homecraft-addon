import json

import pytest

from connector.policy import (
    file_allowed,
    flow_step_host,
    http_target,
    ingress_allowed,
    ws_allowed,
)


def sup(endpoint, method=None):
    msg = {"type": "supervisor/api", "endpoint": endpoint}
    return msg if method is None else {**msg, "method": method}


def svc(domain, service, **extra):
    return {"type": "call_service", "domain": domain, "service": service, **extra}


def edit(*actions, **overrides):
    data = {
        "entity_id": "switch.schedule_abcd",
        "timeslots": [{"start": "06:00:00", "stop": "08:00:00", "actions": list(actions)}],
        "tags": ["kitchen_valve"],
        **overrides,
    }
    return svc("scheduler", "edit", service_data=data)


SET_TEMP = {
    "service": "climate.set_temperature",
    "entity_id": "climate.kitchen_valve",
    "service_data": {"temperature": 21},
}

WS_ALLOWED = [
    {"type": "get_states"},
    {"type": "get_config"},
    {"type": "lovelace/config/save", "config": {}},
    {"type": "lovelace/dashboards/list"},
    {"type": "lovelace/dashboards/delete", "dashboard_id": "x"},
    {"type": "lovelace/dashboards/create", "url_path": "hfb-features", "title": "Features"},
    {"type": "scheduler"},
    {"type": "zeroconf/subscribe_discovery"},
    {"type": "config/floor_registry/list"},
    {"type": "config/area_registry/create", "name": "x"},
    {"type": "config/area_registry/delete", "area_id": "x"},
    {"type": "config/label_registry/update", "label_id": "x"},
    {"type": "config/label_registry/delete", "label_id": "x"},
    {"type": "config/device_registry/list"},
    {"type": "config/device_registry/update", "device_id": "x"},
    {"type": "config/entity_registry/update", "entity_id": "sensor.x"},
    {"type": "config/entity_registry/remove", "entity_id": "sensor.x"},
    {"type": "input_boolean/delete", "input_boolean_id": "x"},
    {"type": "schedule/list"},
    {"type": "counter/create", "name": "x"},
    svc("automation", "reload"),
    svc("frontend", "reload_themes"),
    svc("homeassistant", "reload_core_config"),
    svc("homeassistant", "reload_custom_templates"),
    edit(SET_TEMP),
    edit({**SET_TEMP, "service": None, "action": "climate.set_temperature"}),
    edit(SET_TEMP, tags=[]),
    sup("/addons/a0d7b954_nodered/info", "get"),
    sup("/addons/a0d7b954_nodered/info"),  # get by default
    sup("/addons/a889bffc_go2rtc/info", "get"),
    sup("/store/addons/a0d7b954_nodered", "get"),
    sup("/store/addons/a0d7b954_influxdb/install", "post"),
    sup("/store/addons/a0d7b954_grafana/install", "post"),
    sup("/addons/a0d7b954_influxdb/start", "post"),
    sup("/addons/a0d7b954_grafana/start", "post"),
    sup("/ingress/session", "post"),
]

WS_REFUSED = [
    None,
    "get_states",
    {},
    {"type": 1},
    {"type": "auth", "access_token": "x"},
    {"type": "render_template", "template": "{{ 1 }}"},
    {"type": "subscribe_events"},
    {"type": "execute_script", "sequence": []},
    {"type": "config/auth/create"},
    {"type": "config/entity_registry/list_for_display"},
    {"type": "config/floor_registry/create", "name": "x"},
    {"type": "config/floor_registry/delete", "floor_id": "x"},
    {"type": "config/device_registry/remove_config_entry", "device_id": "x"},
    {"type": "config/device_registry/delete", "device_id": "x"},
    {"type": "config/entity_registry/delete", "entity_id": "x"},
    {"type": "config/area_registry/remove", "area_id": "x"},
    {"type": "config/user_registry/list"},
    {"type": "input_boolean/list/x"},
    {"type": "person/create", "name": "x"},
    {"type": "zone/delete", "zone_id": "x"},
    {"type": "lovelace/dashboards/create", "url_path": "lovelace-mine"},
    {"type": "lovelace/dashboards/create"},
    {"type": "lovelace/dashboards/update", "dashboard_id": "x"},
    svc("shell_command", "x"),
    svc("homeassistant", "restart"),
    svc("hassio", "addon_stdin"),
    svc("scheduler", "remove"),
    svc("../x", "reload"),
    {"type": "call_service", "service": "reload"},
    svc("automation", "reload", service_data={"x": 1}),
    svc("automation", "reload", target={"entity_id": "automation.x"}),
    svc("scheduler", "edit"),
    edit({**SET_TEMP, "service": "shell_command.x"}),
    edit({**SET_TEMP, "service": "climate.set_hvac_mode"}),
    edit({**SET_TEMP, "service": "script.turn_on", "entity_id": "script.x"}),
    edit({**SET_TEMP, "entity_id": "switch.boiler"}),
    edit({**SET_TEMP, "service": None, "action": "notify.x"}),
    edit(SET_TEMP, entity_id="climate.x"),
    edit(SET_TEMP, extra="x"),
    edit(SET_TEMP, tags="kitchen"),
    edit(SET_TEMP, timeslots="x"),
    edit("not an action"),
    sup("/addons/core_ssh/start", "post"),
    sup("/addons/core_ssh/info", "get"),
    sup("/addons/a0d7b954_nodered/stdin", "post"),
    sup("/addons/a0d7b954_nodered/info", "post"),
    sup("/addons/a0d7b954_grafana/options", "post"),
    sup("/addons/a0d7b954_nodered/start", "post"),  # not installed by any feature
    sup("/addons/a889bffc_go2rtc/start", "post"),
    sup("/store/addons/a0d7b954_nodered/install", "post"),
    sup("/addons/a0d7b954_influxdb/start", "get"),
    sup("/store/addons/core_ssh/install", "post"),
    sup("/store/addons/core_ssh", "get"),
    sup("/ingress/session", "get"),
    sup("/host/reboot", "post"),
    sup("/backups", "get"),
    sup("/addons/a0d7b954_nodered/info/../../core_ssh"),
]


@pytest.mark.parametrize("msg", WS_ALLOWED)
def test_ws_allowed(msg):
    assert ws_allowed(msg)


@pytest.mark.parametrize("msg", WS_REFUSED)
def test_ws_refused(msg):
    assert not ws_allowed(msg)


INFLUX = json.dumps({"handler": "influxdb"}).encode()
FLOW = "/api/config/config_entries/flow"

HTTP = [
    ("GET", "/api/", None, ("core", "/api/", "")),
    ("POST", "/api/config/core/check_config", None, ("core", "/api/config/core/check_config", "")),
    ("POST", FLOW, INFLUX, ("core", FLOW, "")),
    ("POST", f"{FLOW}/abc123", b"{}", ("flow", "abc123", "")),
    ("DELETE", f"{FLOW}/abc123", None, ("flow", "abc123", "")),
    ("POST", "/api/config/config_entries/entry/01JABC/reload", None, ("core", ...)),
    ("GET", "/api/config/automation/config/1712", None, ("core", ...)),
    ("POST", "/api/config/script/config/morning", b"{}", ("core", ...)),
    ("GET", "/api/hassio_ingress/tok_EN-1/flows", None, ("ingress", "tok_EN-1", "/flows")),
    ("POST", "/api/hassio_ingress/tok/api/config?x=1", b"", ("ingress", "tok", "/api/config")),
    ("GET", "/api/hassio_ingress/tok", None, ("ingress", "tok", "/")),
    # refused
    ("POST", "/api/", None, None),
    ("GET", "/api/states", None, None),
    ("POST", "/api/services/shell_command/x", None, None),
    ("GET", "/api/config/core/check_config", None, None),
    ("POST", FLOW, json.dumps({"handler": "ssh"}).encode(), None),
    ("POST", FLOW, b"not json", None),
    ("POST", FLOW, b"[]", None),
    ("GET", f"{FLOW}/abc123", None, None),
    ("PUT", f"{FLOW}/abc123", b"{}", None),
    ("POST", "/api/config/config_entries/entry/x/delete", None, None),
    ("POST", "/api/config/config_entries/entry/../reload", None, None),
    ("GET", "/api/config/automation/config/%2e%2e", None, None),
    ("GET", "/api/hassio_ingress/tok/../../states", None, None),
    ("GET", "/api/hassio_ingress/tok/%2E%2E/x", None, None),
    ("GET", "/auth/token", None, None),
    ("GET", "http://evil/api/", None, None),
]


@pytest.mark.parametrize(("method", "path", "body", "expected"), HTTP)
def test_http_target(method, path, body, expected):
    got = http_target(method, path, body)
    if expected is not None and expected[1] is ...:
        expected = ("core", path, "")
    assert got == expected


NODERED, GO2RTC = "a0d7b954_nodered", "a889bffc_go2rtc"
GRAFANA, INFLUXDB = "a0d7b954_grafana", "a0d7b954_influxdb"
INGRESS = [
    (NODERED, "GET", "/flows", True),
    (NODERED, "POST", "/flows", True),
    (NODERED, "GET", "/nodes", True),
    (NODERED, "DELETE", "/flows", False),
    (NODERED, "POST", "/nodes", False),  # installing palette modules
    (NODERED, "GET", "/flow/abc", False),
    (NODERED, "GET", "/settings", False),
    (GO2RTC, "GET", "/api", True),
    (GO2RTC, "GET", "/api/streams", True),
    (GO2RTC, "GET", "/api/config", True),
    (GO2RTC, "POST", "/api/config", True),
    (GO2RTC, "POST", "/api/restart", True),
    (GO2RTC, "PUT", "/api/streams", False),
    (GO2RTC, "POST", "/api/exit", False),
    (GRAFANA, "GET", "/api/health", True),
    (GRAFANA, "GET", "/api/datasources/name/HFB%20LTM", True),
    (GRAFANA, "GET", "/api/datasources/uid/abc", True),
    (GRAFANA, "GET", "/api/datasources/uid/abc/health", True),
    (GRAFANA, "POST", "/api/datasources", True),
    (GRAFANA, "PUT", "/api/datasources/uid/abc", True),
    (GRAFANA, "GET", "/api/dashboards/uid/abc", True),
    (GRAFANA, "POST", "/api/dashboards/db", True),
    (GRAFANA, "DELETE", "/api/datasources/uid/abc", False),
    (GRAFANA, "GET", "/api/datasources", False),
    (GRAFANA, "POST", "/api/admin/users", False),
    (GRAFANA, "GET", "/api/dashboards/uid/a/b", False),
    (INFLUXDB, "GET", "/chronograf/v1/sources", True),
    (INFLUXDB, "POST", "/chronograf/v1/sources/1/proxy", True),
    (INFLUXDB, "DELETE", "/chronograf/v1/sources/1", False),
    (INFLUXDB, "POST", "/chronograf/v1/users", False),
    ("core_ssh", "GET", "/", False),
    (NODERED, "GET", "/api/streams", False),  # another add-on's route
]


@pytest.mark.parametrize(("slug", "method", "subpath", "expected"), INGRESS)
def test_ingress_allowed(slug, method, subpath, expected):
    assert ingress_allowed(slug, method, subpath) is expected


def creds(url, **extra):
    data = {"url": url, "verify_ssl": False, "database": "homeassistant", "username": "u"}
    return json.dumps({**data, "password": "p", **extra}).encode()


FLOW_STEPS = [
    (json.dumps({"next_step_id": "configure_v1"}).encode(), None),
    (creds("http://a0d7b954-influxdb:8086"), "a0d7b954-influxdb"),
    (creds("http://influxdb.lan:8086"), "influxdb.lan"),  # the caller resolves it
    (json.dumps({"next_step_id": "configure_v2"}).encode(), False),
    (json.dumps({"next_step_id": "configure_v1", "x": 1}).encode(), False),
    (creds("ftp://a0d7b954-influxdb"), False),
    (creds("http://user:pw@a0d7b954-influxdb:8086"), False),
    (creds("http://a0d7b954-influxdb:8086", host="evil.example"), False),
    (json.dumps({"database": "x"}).encode(), False),
    (b"not json", False),
    (None, False),
]


@pytest.mark.parametrize(("body", "expected"), FLOW_STEPS)
def test_flow_step_host(body, expected):
    assert flow_step_host(body) == expected


FILES = [
    ("read", "configuration.yaml", True),
    ("read", "packages/hfb_x.yaml", True),
    ("write", "packages/hfb_x.yaml", True),
    ("write", "custom_templates/hfb.jinja", True),
    ("delete", "themes/hfb.yaml", True),
    ("list", "packages", True),
    ("list", "themes/sub", True),
    # refused
    ("write", "configuration.yaml", False),
    ("delete", "configuration.yaml", False),
    ("read", "secrets.yaml", False),
    ("write", "../secrets.yaml", False),
    ("write", "packages/../secrets.yaml", False),
    ("write", "custom_components/x.py", False),
    ("write", "custom_components/hfb_connector_test/x.txt", False),
    ("write", "/homeassistant/packages/x.yaml", False),
    ("write", "packages\\x.yaml", False),
    ("write", "packages", False),
    ("write", "packages//x.yaml", False),
    ("write", "./packages/x.yaml", False),
    ("list", "", False),
    ("list", ".storage", False),
    ("chmod", "packages/x.yaml", False),
    ("read", None, False),
]


@pytest.mark.parametrize(("op", "path", "expected"), FILES)
def test_file_allowed(op, path, expected):
    assert file_allowed(op, path) is expected
