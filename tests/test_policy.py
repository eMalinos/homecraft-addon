import json

import pytest

from connector.policy import file_allowed, http_target, ws_allowed

WS_ALLOWED = [
    {"type": "get_states"},
    {"type": "get_config"},
    {"type": "lovelace/config/save", "config": {}},
    {"type": "lovelace/dashboards/create", "url_path": "hfb"},
    {"type": "scheduler"},
    {"type": "zeroconf/subscribe_discovery"},
    {"type": "config/entity_registry/remove", "entity_id": "sensor.x"},
    {"type": "config/label_registry/delete", "label_id": "x"},
    {"type": "config/area_registry/create", "name": "x"},
    {"type": "input_boolean/delete", "input_boolean_id": "x"},
    {"type": "schedule/list"},
    {"type": "call_service", "domain": "automation", "service": "reload"},
    {"type": "call_service", "domain": "frontend", "service": "reload_themes"},
    {"type": "call_service", "domain": "homeassistant", "service": "reload_custom_templates"},
    {"type": "call_service", "domain": "scheduler", "service": "edit", "service_data": {}},
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_nodered/info", "method": "get"},
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_nodered/info"},  # get by default
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_grafana/options", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/addons/a889bffc_go2rtc/start", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/store/addons/a0d7b954_influxdb", "method": "get"},
    {
        "type": "supervisor/api",
        "endpoint": "/store/addons/a0d7b954_influxdb/install",
        "method": "post",
    },
    {"type": "supervisor/api", "endpoint": "/ingress/session", "method": "post"},
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
    {"type": "input_boolean/list/x"},
    {"type": "call_service", "domain": "shell_command", "service": "x"},
    {"type": "call_service", "domain": "homeassistant", "service": "restart"},
    {"type": "call_service", "domain": "hassio", "service": "addon_stdin"},
    {"type": "call_service", "domain": "scheduler", "service": "remove"},
    {"type": "call_service", "domain": "../x", "service": "reload"},
    {"type": "call_service", "service": "reload"},
    {"type": "supervisor/api", "endpoint": "/addons/core_ssh/start", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/addons/core_ssh/info", "method": "get"},
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_nodered/stdin", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_nodered/info", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_nodered/start", "method": "get"},
    {"type": "supervisor/api", "endpoint": "/store/addons/core_ssh/install", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/host/reboot", "method": "post"},
    {"type": "supervisor/api", "endpoint": "/backups", "method": "get"},
    {"type": "supervisor/api", "endpoint": "/addons/a0d7b954_nodered/info/../../core_ssh"},
]


@pytest.mark.parametrize("msg", WS_ALLOWED)
def test_ws_allowed(msg):
    assert ws_allowed(msg)


@pytest.mark.parametrize("msg", WS_REFUSED)
def test_ws_refused(msg):
    assert not ws_allowed(msg)


INFLUX = json.dumps({"handler": "influxdb"}).encode()

HTTP = [
    ("GET", "/api/", None, ("core", "/api/")),
    ("POST", "/api/config/core/check_config", None, ("core", "/api/config/core/check_config")),
    (
        "POST",
        "/api/config/config_entries/flow",
        INFLUX,
        ("core", "/api/config/config_entries/flow"),
    ),
    ("POST", "/api/config/config_entries/flow/abc123", b"{}", ("core", ...)),
    ("DELETE", "/api/config/config_entries/flow/abc123", None, ("core", ...)),
    ("POST", "/api/config/config_entries/entry/01JABC/reload", None, ("core", ...)),
    ("GET", "/api/config/automation/config/1712", None, ("core", ...)),
    ("POST", "/api/config/script/config/morning", b"{}", ("core", ...)),
    ("GET", "/api/hassio_ingress/tok_EN-1/flows", None, ("ingress", "tok_EN-1")),
    ("POST", "/api/hassio_ingress/tok/api/config?x=1", b"", ("ingress", "tok")),
    # refused
    ("POST", "/api/", None, None),
    ("GET", "/api/states", None, None),
    ("POST", "/api/services/shell_command/x", None, None),
    ("GET", "/api/config/core/check_config", None, None),
    ("POST", "/api/config/config_entries/flow", json.dumps({"handler": "ssh"}).encode(), None),
    ("POST", "/api/config/config_entries/flow", b"not json", None),
    ("POST", "/api/config/config_entries/entry/x/delete", None, None),
    ("POST", "/api/config/config_entries/entry/../reload", None, None),
    ("GET", "/api/config/automation/config/%2e%2e", None, None),
    ("GET", "/api/hassio_ingress/tok/../../states", None, None),
    ("GET", "/api/hassio_ingress/tok/%2E%2E/x", None, None),
    ("GET", "/api/hassio_ingress/tok", None, None),
    ("TRACE", "/api/hassio_ingress/tok/", None, None),
    ("GET", "/auth/token", None, None),
    ("GET", "http://evil/api/", None, None),
]


@pytest.mark.parametrize(("method", "path", "body", "expected"), HTTP)
def test_http_target(method, path, body, expected):
    got = http_target(method, path, body)
    if expected is not None and expected[1] is ...:
        expected = ("core", path)
    assert got == expected


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
