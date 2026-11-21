import json

import pytest

from connector.policy import (
    SQL_QUERIES,
    file_allowed,
    flow_step_allowed,
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
    {"id": 3, "type": "config_entries/update", "entry_id": "01J", "pref_disable_polling": True},
    {"type": "get_config"},
    {"type": "lovelace/config/save", "config": {}},
    {"type": "lovelace/dashboards/list"},
    {"type": "lovelace/info"},
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
    {
        "id": 5,
        "type": "lovelace/resources/create",
        "res_type": "module",
        "url": "/local/homecraft/heating-card.js?v=0.2.0",
    },
    {
        "type": "lovelace/resources/update",
        "resource_id": "abc",
        "res_type": "module",
        "url": "/local/homecraft/heating-card.js?v=0.3.0",
    },
    {"type": "lovelace/resources/delete", "resource_id": "abc"},
    svc(
        "persistent_notification",
        "create",
        service_data={
            "notification_id": "homecraft_card_update_heating-card",
            "title": "Homecraft",
            "message": "Heating Card 0.3.0 je připravená.",
        },
    ),
    svc(
        "persistent_notification",
        "dismiss",
        service_data={"notification_id": "homecraft_card_update_heating-card"},
    ),
    {"type": "hacs/repositories/list"},
    {"id": 9, "type": "hacs/repository/download", "repository": "286186485", "version": "v3.3.9"},
]

HACS_DL = {"id": 9, "type": "hacs/repository/download", "repository": "286186485"}


def res(url="/local/homecraft/heating-card.js?v=0.2.0", type_="create", **extra):
    msg = {"type": f"lovelace/resources/{type_}", "res_type": "module", "url": url, **extra}
    return {k: v for k, v in msg.items() if v is not None}


def note(service="create", **data):
    base = {"notification_id": "homecraft_x", "message": "m"}
    data = {k: v for k, v in {**base, **data}.items() if v is not None}
    return svc("persistent_notification", service, service_data=data)


WS_REFUSED = [
    None,
    {"type": "config_entries/update", "entry_id": "01J", "pref_disable_polling": False},
    {"type": "config_entries/update", "entry_id": "01J", "pref_disable_new_entities": True},
    {"type": "config_entries/update", "entry_id": "01J", "title": "x"},
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
    res("https://evil.example/x.js"),
    res("/local/other.js"),
    res("/local/homecraft/heating-card.js"),
    res("/local/homecraft/heating-card.js?v=0.2.0&x=1"),
    res("/local/homecraft/../x.js?v=1.0.0"),
    res("/local/homecraft/heating-card.js?v=\u0661.0.0"),
    res(res_type="js"),
    res(extra="x"),
    res(type_="update", url=None, resource_id="abc"),
    res(type_="update"),
    {"type": "lovelace/resources/delete", "resource_id": 1},
    note(notification_id="other"),
    note(notification_id="homecraft"),
    note(notification_id="Homecraft_x"),
    note(notification_id=None),
    {**note(), "target": {"entity_id": "x"}},
    note(data="x"),
    note(message=None),
    note(message=1),
    svc("persistent_notification", "dismiss_all"),
    svc("persistent_notification", []),
    svc("automation", []),
    {**note(), "return_response": True},
    {**HACS_DL, "version": "v3.3.8"},
    {**HACS_DL, "repository": "1", "version": "v3.3.9"},
    HACS_DL,
    {**HACS_DL, "version": "v3.3.9", "category": "integration"},
    {**HACS_DL, "repository": 286186485, "version": "v3.3.9"},
    {**HACS_DL, "repository": "1", "version": None},
    {**HACS_DL, "repository": [], "version": "v3.3.9"},
    {**HACS_DL, "repository": {}, "version": "v3.3.9"},
    {"type": "hacs/repository/remove", "repository": "286186485"},
    {"type": "hacs/repositories/add", "repository": "x/y", "category": "integration"},
]


@pytest.mark.parametrize("msg", WS_ALLOWED)
def test_ws_allowed(msg):
    assert ws_allowed(msg)


@pytest.mark.parametrize("msg", WS_REFUSED)
def test_ws_refused(msg):
    assert not ws_allowed(msg)


INFLUX = json.dumps({"handler": "influxdb"}).encode()
FLOW = "/api/config/config_entries/flow"
CARD = "/local/homecraft/heating-card.js?v=0.2.0"

HTTP = [
    ("GET", "/api/", None, ("core", "/api/", "")),
    ("POST", "/api/config/core/check_config", None, ("core", "/api/config/core/check_config", "")),
    ("POST", FLOW, INFLUX, ("core", FLOW, "")),
    ("POST", FLOW, json.dumps({"handler": "sql"}).encode(), ("core", FLOW, "")),
    ("POST", FLOW, json.dumps({"handler": "scheduler"}).encode(), ("core", FLOW, "")),
    ("POST", f"{FLOW}/abc123", b"{}", ("flow", "abc123", "")),
    ("DELETE", f"{FLOW}/abc123", None, ("flow", "abc123", "")),
    ("POST", "/api/config/config_entries/entry/01JABC/reload", None, ("core", ...)),
    ("GET", "/api/config/automation/config/1712", None, ("core", ...)),
    ("POST", "/api/config/script/config/morning", b"{}", ("core", ...)),
    ("GET", "/api/hassio_ingress/tok_EN-1/flows", None, ("ingress", "tok_EN-1", "/flows")),
    ("POST", "/api/hassio_ingress/tok/api/config?x=1", b"", ("ingress", "tok", "/api/config")),
    ("GET", "/api/hassio_ingress/tok", None, ("ingress", "tok", "/")),
    ("HEAD", CARD, None, ("local", CARD, "")),
    # refused
    ("GET", CARD, None, None),
    ("HEAD", CARD, b"", None),
    ("HEAD", "/local/homecraft/heating-card.js", None, None),
    ("HEAD", "/local/other.js?v=1.0.0", None, None),
    ("HEAD", "/local/homecraft/../x.js?v=1.0.0", None, None),
    ("HEAD", CARD + "&x=1", None, None),
    ("POST", "/api/", None, None),
    ("GET", "/api/states", None, None),
    ("POST", "/api/services/shell_command/x", None, None),
    ("GET", "/api/config/core/check_config", None, None),
    ("POST", FLOW, json.dumps({"handler": "ssh"}).encode(), None),
    ("POST", FLOW, json.dumps({"handler": "hacs"}).encode(), None),
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


RECORDER_SIZE = next(iter(SQL_QUERIES))
OPTIONS = {"unit_of_measurement": "MiB", "device_class": "data_size", "state_class": "measurement"}


@pytest.mark.parametrize(
    ("handler", "body", "expected"),
    [
        ("speedtestdotnet", {}, True),
        ("ping", {"host": "1.1.1.1"}, True),
        ("ping", {"host": "10.0.0.1"}, False),  # no LAN scan
        ("holiday", {"country": "CZ"}, True),
        ("holiday", {"country": ["CZ"]}, False),
        ("sql", {"name": "HFB recorder database size"}, True),
        ("sql", {"query": RECORDER_SIZE, "column": "size", "additional_options": OPTIONS}, True),
        ("sql", {"query": "SELECT shared_attrs FROM state_attributes;"}, False),
        ("sql", {"query": ["SELECT 1"]}, False),
        ("sql", {"additional_options": {"value_template": "{{ states | list }}"}}, False),
        ("sql", {"name": "x", "db_url": "postgresql://evil/db"}, False),  # recorder only
        ("ping", {"next_step_id": "x"}, False),
        ("influxdb", {}, False),  # its steps go through flow_step_host
        ("ssh", {}, False),
    ],
)
def test_flow_step_allowed(handler, body, expected):
    assert flow_step_allowed(handler, json.dumps(body).encode()) is expected
    assert not flow_step_allowed(handler, b"not json")


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
    ("write", "www/homecraft/heating-card.js", True),
    ("delete", "www/homecraft/heating-schedule-card.js", True),
    ("list", "www/homecraft", True),
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
    ("write", "www/other.js", False),
    ("write", "www/homecraft/x.html", False),
    ("write", "www/homecraft/../secrets.yaml", False),
    ("write", "www/homecraft/sub/x.js", False),
    ("write", "www/homecraft/Heating.js", False),
    ("write", "www/homecraft/.js", False),
    ("write", "www/homecraft/x.js.hfb-tmp", False),
    ("read", "www/homecraft/heating-card.js", False),
    ("list", "www", False),
    ("write", "www/homecraft", False),
    ("delete", "www/homecraft", False),
]


@pytest.mark.parametrize(("op", "path", "expected"), FILES)
def test_file_allowed(op, path, expected):
    assert file_allowed(op, path) is expected
