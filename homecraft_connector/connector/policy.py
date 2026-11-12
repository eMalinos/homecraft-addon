"""The allowlist (Security, level 1): what Homecraft may ask of this HA.

It lives only here, in the add-on code: never in the options, never sent by
the server, so a compromised Homecraft cannot widen it. Pure functions, no I/O."""

import json
import re
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
        "lovelace/dashboards/create",
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
# Registries: label/area delete, entity remove (ha_registry_applicators,
# config_file_applicator). Helpers: storage collections, delete included.
_WS_RE = re.compile(
    r"config/(area|device|entity|label|floor)_registry/(list|create|update|delete|remove)"
    r"|(input_boolean|input_number|input_select|input_text|input_datetime|input_button"
    r"|counter|timer|schedule)/(list|create|update|delete)"
)
# config_file_applicator reloads what a written file configures.
RELOAD_SERVICES = frozenset(
    {"reload", "reload_themes", "reload_core_config", "reload_custom_templates"}
)
# The add-ons features install or reach through ingress
# (library/*/build.py `"type": "addon"`).
ADDONS = frozenset({"a0d7b954_nodered", "a889bffc_go2rtc", "a0d7b954_influxdb", "a0d7b954_grafana"})
_SUPERVISOR = [
    (re.compile(r"/addons/([a-z0-9_]+)/info"), "get"),
    (re.compile(r"/addons/([a-z0-9_]+)/(options|start)"), "post"),
    (re.compile(r"/store/addons/([a-z0-9_]+)"), "get"),
    (re.compile(r"/store/addons/([a-z0-9_]+)/install"), "post"),
    (re.compile(r"/ingress/session()"), "post"),
]


def _supervisor_allowed(endpoint: object, method: object) -> bool:
    if not isinstance(endpoint, str):
        return False
    method = method if method is not None else "get"  # HA's default
    for pattern, wanted in _SUPERVISOR:
        m = pattern.fullmatch(endpoint)
        if m and method == wanted:
            return m.group(1) == "" or m.group(1) in ADDONS
    return False


def ws_allowed(msg: object) -> bool:
    if not isinstance(msg, dict) or not isinstance(t := msg.get("type"), str):
        return False
    if t in WS_TYPES or _WS_RE.fullmatch(t):
        return True
    if t == "call_service":
        domain, service = msg.get("domain"), msg.get("service")
        if not (isinstance(domain, str) and re.fullmatch(r"[a-z0-9_]+", domain)):
            return False
        return service in RELOAD_SERVICES or (domain, service) == ("scheduler", "edit")
    if t == "supervisor/api":
        return _supervisor_allowed(msg.get("endpoint"), msg.get("method"))
    return False


# --- HTTP ------------------------------------------------------------------

_ID = r"[A-Za-z0-9_-]+"
_FLOW = "/api/config/config_entries/flow"
# Config flows Homecraft may start (influxdb_admin).
FLOW_HANDLERS = frozenset({"influxdb"})
_CORE = [
    (re.compile(r"/api/"), {"GET"}),  # connection test
    (re.compile(r"/api/config/core/check_config"), {"POST"}),
    (re.compile(rf"{_FLOW}/{_ID}"), {"GET", "POST", "DELETE"}),
    (re.compile(rf"/api/config/config_entries/entry/{_ID}/reload"), {"POST"}),
    # references.py: where an entity is used, and fixing it on a rename
    (re.compile(rf"/api/config/(automation|script)/config/{_ID}"), {"GET", "POST"}),
]
INGRESS_RE = re.compile(r"/api/hassio_ingress/([A-Za-z0-9_-]+)/.*")
INGRESS_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def _flow_handler(body: bytes | None) -> object:
    try:
        return json.loads(body or b"").get("handler")
    except (ValueError, AttributeError):
        return None


def http_target(method: str, path: str, body: bytes | None = None) -> tuple[str, str] | None:
    """-> ("core", path) or ("ingress", ingress token), None when refused.
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
        return ("ingress", m.group(1)) if method in INGRESS_METHODS else None
    if route == _FLOW:
        ok = method == "POST" and _flow_handler(body) in FLOW_HANDLERS
        return ("core", path) if ok else None
    for pattern, methods in _CORE:
        if pattern.fullmatch(route) and method in methods:
            return ("core", path)
    return None


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
