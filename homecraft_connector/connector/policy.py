"""The allowlist (Security, level 1): what Homecraft may ask of this HA.

It lives only here, in the add-on code: never in the options, never sent by
the server, so a compromised Homecraft cannot widen it. Least privilege: only
what the Homecraft backend and the library features send. Pure functions, no I/O."""

import json
import re
import urllib.parse
from pathlib import PurePosixPath


class OpError(Exception):
    """An operation answered `{"ok": false, "error": {"code", "message"}}`."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def not_allowed(what: str) -> OpError:
    return OpError("not_allowed", f"{what} is not allowed by Homecraft Connector")


# --- WebSocket -------------------------------------------------------------

WS_TYPES = frozenset(
    {
        "get_states",
        "get_services",
        "get_config",
        "lovelace/config",
        "lovelace/config/save",
        "lovelace/dashboards/list",
        # dashboard_applicator.save: the app's own dashboard once it is empty
        "lovelace/dashboards/delete",
        "lovelace/resources",
        "config_entries/get",
        "scheduler",
        "search/related",
        "history/history_during_period",
        "recorder/statistics_during_period",
        "dhcp/subscribe_discovery",
        "ssdp/subscribe_discovery",
        "zeroconf/subscribe_discovery",
        "unsubscribe_events",
    }
)
# Per registry, the verbs ha_registry_applicators, config_file_applicator and
# the inventory send.
REGISTRY_VERBS = {
    "floor": {"list"},
    "area": {"list", "create", "update", "delete"},
    "label": {"list", "create", "update", "delete"},
    "device": {"list", "update"},
    "entity": {"list", "update", "remove"},
}
# StorageHelperApplicator (domain.ha_inventory.HELPER_DOMAINS)
HELPER_DOMAINS = frozenset(
    {
        "input_boolean",
        "input_button",
        "input_datetime",
        "input_number",
        "input_select",
        "input_text",
        "schedule",
        "timer",
        "counter",
    }
)
HELPER_VERBS = frozenset({"list", "create", "update", "delete"})
# dashboard_applicator creates only the app's own dashboard.
OWN_DASHBOARD = "hfb-features"
# config_file_applicator reloads what a written file configures.
RELOAD_SERVICES = frozenset(
    {"reload", "reload_themes", "reload_core_config", "reload_custom_templates"}
)
# scheduler.edit (schedules.swap_schedule) rewrites a valve's schedules; the
# heating-schedule-card writes only this service into their actions.
SCHEDULE_SERVICES = frozenset({"climate.set_temperature"})

# Add-ons features reach (library `"type": "addon"`); the ones with
# `install: true` may also be installed and started (addon_install).
ADDONS = frozenset({"a0d7b954_nodered", "a889bffc_go2rtc", "a0d7b954_influxdb", "a0d7b954_grafana"})
INSTALLABLE = frozenset({"a0d7b954_influxdb", "a0d7b954_grafana"})
_SUPERVISOR = [
    (re.compile(r"/addons/([a-z0-9_]+)/info"), "get", ADDONS),
    (re.compile(r"/store/addons/([a-z0-9_]+)"), "get", ADDONS),  # file_channels.addon_info
    (re.compile(r"/store/addons/([a-z0-9_]+)/install"), "post", INSTALLABLE),
    (re.compile(r"/addons/([a-z0-9_]+)/start"), "post", INSTALLABLE),
    (re.compile(r"/ingress/session()"), "post", frozenset({""})),
]


def _supervisor_allowed(endpoint: object, method: object) -> bool:
    if not isinstance(endpoint, str):
        return False
    method = method if method is not None else "get"  # HA's default
    for pattern, wanted, slugs in _SUPERVISOR:
        m = pattern.fullmatch(endpoint)
        if m and method == wanted:
            return m.group(1) in slugs
    return False


def _schedule_edit_allowed(data: object) -> bool:
    """`{"entity_id", "timeslots", "tags"}` as swap_schedule builds it, every
    action a SCHEDULE_SERVICES call on a climate entity."""
    if not isinstance(data, dict) or not set(data) <= {"entity_id", "timeslots", "tags"}:
        return False
    if not isinstance(data.get("entity_id"), str) or not data["entity_id"].startswith("switch."):
        return False
    if not isinstance(tags := data.get("tags", []), list) or not all(
        isinstance(t, str) for t in tags
    ):
        return False
    slots = data.get("timeslots")
    if not isinstance(slots, list):
        return False
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("actions"), list):
            return False
        for action in slot["actions"]:
            if not isinstance(action, dict):
                return False
            service = action.get("service") or action.get("action")
            entity = action.get("entity_id")
            if service not in SCHEDULE_SERVICES:
                return False
            if not (isinstance(entity, str) and entity.startswith("climate.")):
                return False
    return True


def ws_allowed(msg: object) -> bool:
    if not isinstance(msg, dict) or not isinstance(t := msg.get("type"), str):
        return False
    if t in WS_TYPES:
        return True
    if m := re.fullmatch(r"config/([a-z]+)_registry/([a-z_]+)", t):
        return m.group(2) in REGISTRY_VERBS.get(m.group(1), ())
    if m := re.fullmatch(r"([a-z_]+)/([a-z_]+)", t):
        if m.group(1) in HELPER_DOMAINS and m.group(2) in HELPER_VERBS:
            return True
    if t == "lovelace/dashboards/create":
        return msg.get("url_path") == OWN_DASHBOARD
    if t == "call_service":
        domain, service = msg.get("domain"), msg.get("service")
        if not (isinstance(domain, str) and re.fullmatch(r"[a-z0-9_]+", domain)):
            return False
        if (domain, service) == ("scheduler", "edit"):
            return _schedule_edit_allowed(msg.get("service_data"))
        return service in RELOAD_SERVICES and "service_data" not in msg and "target" not in msg
    if t == "supervisor/api":
        return _supervisor_allowed(msg.get("endpoint"), msg.get("method"))
    return False


# --- HTTP ------------------------------------------------------------------

_ID = r"[A-Za-z0-9_-]+"
_SEG = r"[A-Za-z0-9_.~%-]+"  # one URL-quoted path segment ("." and ".." refused earlier)
FLOW = "/api/config/config_entries/flow"
_FLOW_STEP_RE = re.compile(rf"{FLOW}/({_ID})")
# Config flows Homecraft may start (influxdb_admin).
FLOW_HANDLERS = frozenset({"influxdb"})
INFLUXDB_ADDON_HOST = "a0d7b954-influxdb"
_CORE = [
    (re.compile(r"/api/"), {"GET"}),  # connection test
    (re.compile(r"/api/config/core/check_config"), {"POST"}),
    (re.compile(rf"/api/config/config_entries/entry/{_ID}/reload"), {"POST"}),
    # references.py: where an entity is used, and fixing it on a rename
    (re.compile(rf"/api/config/(automation|script)/config/{_ID}"), {"GET", "POST"}),
]
INGRESS_RE = re.compile(r"/api/hassio_ingress/([A-Za-z0-9_-]+)(/.*)?")
# What the backend's add-on clients send through ingress, per add-on.
INGRESS_ROUTES = {
    "a0d7b954_nodered": [  # node_red.py
        ("GET", "/flows"),
        ("POST", "/flows"),
        ("GET", "/nodes"),
    ],
    "a889bffc_go2rtc": [  # go2rtc.py
        ("GET", "/api"),
        ("GET", "/api/streams"),
        ("GET", "/api/config"),
        ("POST", "/api/config"),
        ("POST", "/api/restart"),
    ],
    "a0d7b954_grafana": [  # grafana.py
        ("GET", "/api/health"),
        ("GET", rf"/api/datasources/(name|uid)/{_SEG}"),
        ("GET", rf"/api/datasources/uid/{_SEG}/health"),
        ("POST", "/api/datasources"),
        ("PUT", rf"/api/datasources/uid/{_SEG}"),
        ("GET", rf"/api/dashboards/uid/{_SEG}"),
        ("POST", "/api/dashboards/db"),
    ],
    "a0d7b954_influxdb": [  # influxdb_admin.IngressInfluxAdmin (Chronograf)
        ("GET", "/chronograf/v1/sources"),
        ("POST", rf"/chronograf/v1/sources/{_SEG}/proxy"),
    ],
}


def _json(body: bytes | None) -> object:
    try:
        return json.loads(body or b"")
    except ValueError:
        return None


def http_target(method: str, path: str, body: bytes | None = None) -> tuple[str, str, str] | None:
    """Where an `http` request may go, None when refused:
    ("core", path, "") · ("flow", flow_id, "") for a step of a config flow
    (the caller checks the id and `flow_step_host`) · ("ingress", token, subpath)
    (the caller checks `ingress_allowed` for the token's add-on).
    `path` is `/api/...`, query included; the query is not checked."""
    if not isinstance(path, str) or not isinstance(method, str):
        return None
    route = path.split("?", 1)[0]
    lowered = route.lower()
    if "\\" in route or "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        return None
    if any(part in (".", "..") for part in route.split("/")):
        return None
    if m := INGRESS_RE.fullmatch(route):
        return ("ingress", m.group(1), m.group(2) or "/")
    if route == FLOW:
        data = _json(body)
        ok = method == "POST" and isinstance(data, dict) and data.get("handler") in FLOW_HANDLERS
        return ("core", path, "") if ok else None
    if m := _FLOW_STEP_RE.fullmatch(route):
        return ("flow", m.group(1), "") if method in ("POST", "DELETE") else None
    for pattern, methods in _CORE:
        if pattern.fullmatch(route) and method in methods:
            return ("core", path, "")
    return None


def ingress_allowed(slug: str, method: str, subpath: str) -> bool:
    return any(method == m and re.fullmatch(p, subpath) for m, p in INGRESS_ROUTES.get(slug, ()))


def flow_step_host(body: bytes | None) -> str | None | bool:
    """A step body of the InfluxDB config flow (influxdb_admin._create_entry):
    the menu choice -> None (nothing to check); the credentials -> the host of
    their `url` (and `host`, which must agree) for the caller to check; else False."""
    data = _json(body)
    if not isinstance(data, dict):
        return False
    if data == {"next_step_id": "configure_v1"}:
        return None
    url = data.get("url")
    if not isinstance(url, str):
        return False
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        return False
    host = data.get("host", parts.hostname)
    return parts.hostname if host == parts.hostname else False


# --- files -----------------------------------------------------------------

FILE_DIRS = ("packages", "custom_templates", "themes")  # config_file_applicator._DIRS
READ_ONLY_FILES = frozenset({"configuration.yaml"})


def file_allowed(op: str, path: object) -> bool:
    """`path` relative to HA's config directory, '/'-separated."""
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if any(p in ("", ".", "..") for p in path.split("/")):
        return False
    if op == "read" and path in READ_ONLY_FILES:
        return True
    if op not in ("read", "write", "delete", "list") or parts[0] not in FILE_DIRS:
        return False
    return len(parts) > 1 or op == "list"  # the directory itself: only list it
