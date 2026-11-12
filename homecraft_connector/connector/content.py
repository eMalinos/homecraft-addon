"""The content check (Security, level 2): what Homecraft may write, on top of
where (policy.py). Nothing that runs commands or sends data out gets into HA:
YAML packages, automation/script configs over REST, Node-RED flows and the
go2rtc config through ingress. Conservative on purpose, never widened by the
options. Pure functions, no I/O; each check returns the refused item or None."""

import json
import re
from collections import Counter

import yaml

from .policy import OpError

# Top-level integrations that run commands, send data out or serve code.
FORBIDDEN_KEYS = frozenset(
    {
        "shell_command",
        "command_line",
        "python_script",
        "pyscript",
        "rest_command",
        "rest",
        "scrape",
        "panel_custom",
        "panel_iframe",
        "http",
        "frontend",  # extra_module_url: JS into every browser
        "lovelace",  # resources: the same
        "ffmpeg",  # its binary
        "logger",
    }
)
# Not forbidden, the library needs them: `recorder` (observability's
# purge_keep_days) and `influxdb`, but that only as a filter (INFLUXDB_KEYS):
# where the data goes comes from the integration's config flow.
INFLUXDB_KEYS = frozenset({"include", "exclude", "ignore_attributes"})
# homeassistant: customize stays; these widen what HA loads or lets in.
HOMEASSISTANT_KEYS = frozenset(
    {
        "auth_providers",
        "packages",
        "allowlist_external_dirs",
        "allowlist_external_urls",
        "media_dirs",
        "customize_glob",
    }
)
FORBIDDEN_PLATFORMS = frozenset({"command_line", "shell_command", "rest", "scrape"})
# Every service of these domains; `hassio` as a whole: add-on stdin/start/stop,
# host reboot/shutdown, backups (the library calls none of it).
FORBIDDEN_DOMAINS = frozenset(
    {"shell_command", "rest_command", "python_script", "pyscript", "hassio", "downloader", "backup"}
)
# Single services (`*`: a prefix). The library calls homeassistant.update_entity
# and recorder.get_statistics, so those domains stay; `notify` too (mobile_app).
FORBIDDEN_SERVICES = frozenset(
    {
        "update.install",
        "homeassistant.stop",
        "homeassistant.restart",
        "recorder.disable",
        "recorder.purge*",
    }
)
SERVICE_KEYS = ("action", "service", "service_template", "perform_action")

# Node-RED node types the library deploys (library/heating, node_red_flow),
# plus subflow instances (`subflow:<id>`); tests/test_library_passes.py keeps
# this in step with the library. `http request` is there because the gateway
# watchdog asks Shelly gateways for their components (a known limit, DOCS.md).
NODE_TYPES = frozenset(
    {
        "tab",
        "subflow",
        "comment",
        "inject",
        "catch",
        "function",
        "switch",
        "split",
        "delay",
        "http request",
        "server-state-changed",
        "server-events",
        "ha-get-entities",
        "api-render-template",
        "api-call-service",
        "ha-fire-event",
    }
)
# api-call-service as the library writes it: the service fixed in the node,
# never taken from the message.
CALL_SERVICE_VERSION = 7
# go2rtc.yaml keys the library writes (go2rtc_config components): only
# `webrtc`. `streams` stays the user's, so no stream source (exec:, echo:,
# expr: run commands) ever changes through Homecraft.
GO2RTC_KEYS = frozenset({"webrtc"})

NODE_RED = "a0d7b954_nodered"
GO2RTC = "a889bffc_go2rtc"


def refused(where: str, what: str) -> OpError:
    return OpError("refused_content", f"{where}: {what} není přes Homecraft Connector povolený")


def _load(text: str) -> tuple[object, str | None]:
    """(data, None) or (None, why). No HA tag is accepted (the library uses
    none): `!include*` would pull in a file this check never saw, `!secret`
    and `!env_var` read values from outside the file."""
    try:
        return yaml.safe_load(text), None
    except yaml.constructor.ConstructorError as e:
        return None, f"tag {e.problem.split()[-1] if e.problem else '?'}"
    except yaml.YAMLError:
        return None, "neplatný YAML"


def service_refused(name: object) -> bool:
    """A forbidden service, or one a template could make forbidden: a templated
    domain, or a template after a domain with forbidden services.
    `script.hfb_notify_{{ channel }}` keeps its domain and passes."""
    if not isinstance(name, str):
        return False
    lowered = name.strip().lower()
    fixed = re.split(r"\{[{%#]", lowered, maxsplit=1)[0]
    templated = fixed != lowered
    if "." not in fixed:
        return templated  # a template before the dot
    domain = fixed.split(".", 1)[0]
    if domain in FORBIDDEN_DOMAINS:
        return True
    for s in FORBIDDEN_SERVICES:
        if templated and s.startswith(domain + "."):
            return True
        if fixed == s or (s.endswith("*") and fixed.startswith(s[:-1])):
            return True
    return False


def _findings(node: object, config: bool, seen: set[int], out: list[str]) -> list[str]:
    """Every forbidden service in the tree, and with `config` (HA
    configuration, not a dashboard) every forbidden key or platform. A key
    counts by its domain: `shell_command 2:` is `shell_command`."""
    if isinstance(node, dict | list):
        if id(node) in seen:  # YAML aliases: each shared node once (no alias bomb)
            return out
        seen.add(id(node))
    if isinstance(node, list):
        for item in node:
            _findings(item, config, seen, out)
    elif isinstance(node, dict):
        for key, value in node.items():
            name = str(key).split(" ", 1)[0].lower()
            if config and name in FORBIDDEN_KEYS:
                out.append(name)
            if config and name == "homeassistant" and isinstance(value, dict):
                bad = HOMEASSISTANT_KEYS & {str(k).lower() for k in value}
                out += [f"homeassistant: {k}" for k in sorted(bad)]
            if config and name == "influxdb":
                keys = {str(k).lower() for k in value} if isinstance(value, dict) else {"?"}
                out += [f"influxdb: {k}" for k in sorted(keys - INFLUXDB_KEYS)]
            if config and name == "platform" and value in FORBIDDEN_PLATFORMS:
                out.append(f"platform: {value}")
            if name in SERVICE_KEYS and service_refused(value):
                out.append(f"{name}: {value}")
            _findings(value, config, seen, out)
    return out


def _new_finding(new: object, live: object = None, config: bool = True) -> str | None:
    """The first finding in `new` that `live` (what HA has now) does not
    already have as often: the user's own content never blocks Homecraft."""
    left = Counter(_findings(live, config, set(), []))
    for found in _findings(new, config, set(), []):
        if left[found]:
            left[found] -= 1
        else:
            return found
    return None


def check_yaml(path: str, text: str) -> str | None:
    """A package file (`path` only names it): the refused item, None when it passes."""
    data, why = _load(text)
    return why or _new_finding(data)


def check_tags(text: str) -> str | None:
    """A theme: no code runs, but HA's loader resolves the tags (`!include`,
    `!secret`, `!env_var`) in themes/ too."""
    return _load(text)[1]


def check_dashboard(config: object, live: object = None) -> str | None:
    """lovelace/config/save: tap/hold actions (`perform_action`, `service`)
    new against `live` (the dashboard's config in HA now). Card options are
    not HA configuration: a card key named `rest` is fine."""
    return _new_finding(config, live, config=False)


def check_automation_body(body: bytes | None, live: object = None) -> str | None:
    """POST /api/config/(automation|script)/config/<id>: the package check,
    against `live` (GET of the same id; None when it is new)."""
    try:
        data = json.loads(body or b"")
    except ValueError:
        return "neplatný JSON"
    return _new_finding(data, live)


def _nodes(data: object) -> list | None:
    """Node-RED's flows, API v1 (a list) or v2 ({"rev", "flows"})."""
    if isinstance(data, dict):
        data = data.get("flows")
    return data if isinstance(data, list) else None


def _call_service_refused(node: dict) -> str | None:
    t = node["type"]
    if node.get("version") != CALL_SERVICE_VERSION or "service_domain" in node:
        return f"uzel {t} verze {node.get('version')}"
    if node.get("blockInputOverrides") is not True:
        return f"uzel {t} bez blockInputOverrides"  # msg.payload could pick the service
    action, domain, service = node.get("action"), node.get("domain"), node.get("service")
    pair = f"{domain}.{service}" if domain and service else None
    if not (action or pair) or (action and pair and action != pair):
        return f"uzel {t} bez jednoznačné služby"
    for name in (action, pair):
        if name and service_refused(name):
            return f"uzel {t} se službou {name}"
    return None


def _node_refused(node: dict) -> str | None:
    t = node["type"]
    if t not in NODE_TYPES and not t.startswith("subflow:"):
        return f"uzel {t}"
    if t == "api-call-service":
        return _call_service_refused(node)
    if t == "function" and node.get("libs"):
        return f"uzel {t} s moduly (libs)"
    return None


def _targets(node: dict) -> list[object]:
    """IDs the node sends to: `wires`, and a subflow's `in`/`out` ports."""
    ids = [w for port in node.get("wires") or [] if isinstance(port, list) for w in port]
    for port in (node.get("in") or []) + (node.get("out") or []):
        if isinstance(port, dict):
            ids += [w.get("id") if isinstance(w, dict) else w for w in port.get("wires") or []]
    return ids


def check_nodered_flows(body: bytes | None, current: object) -> str | None:
    """POST /flows sends every flow, the user's too: nodes it adds or changes
    against `current` (GET /flows just before) must pass, and so must every
    node they reach (wires, subflow instances and their insides). Untouched
    user nodes nothing new reaches go through as they are, an exec node too.
    A user's own tab or subflow (not Homecraft's) and everything in it must
    arrive exactly as it is live, and nothing new may go into it."""
    try:
        new = _nodes(json.loads(body or b""))
    except ValueError:
        new = None
    if new is None:
        return "neplatný JSON flows"
    if not all(isinstance(n, dict) and isinstance(n.get("id"), str) for n in new):
        return "uzel bez id"
    if not all(isinstance(n.get("type"), str) for n in new):
        return "uzel bez typu"
    by_id = {n["id"]: n for n in new}
    live = {n.get("id"): n for n in _nodes(current) or [] if isinstance(n, dict)}
    ours = {  # Homecraft's tabs (node_red.py: the `ha-builder:` marker in info)
        i
        for i, n in live.items()
        if n.get("type") == "tab" and str(n.get("info") or "").startswith("ha-builder:")
    }
    users = {  # the user's tabs and subflows
        i
        for i, n in live.items()
        if n.get("type") in ("tab", "subflow")
        and not any(i == t or str(i).startswith(t + "_") for t in ours)
    }
    for i, n in live.items():
        if (i in users or n.get("z") in users) and by_id.get(i) != n:
            return f"změna uživatelova obsahu {i}"
    todo = [n for n in new if live.get(n["id"]) != n]
    for node in todo:
        if node.get("z") in users:
            return f"uzel {node['id']} v uživatelově {live[node['z']].get('type')} {node['z']}"
    seen: set[str] = set()
    while todo:
        node = todo.pop()
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        if found := _node_refused(node):
            return found
        for target in _targets(node):
            if not isinstance(target, str) or target not in by_id:
                return f"drát z {node['id']} do neznámého uzlu"
            todo.append(by_id[target])
        if node["type"].startswith("subflow:"):
            sid = node["type"][len("subflow:") :]
            if by_id.get(sid, {}).get("type") != "subflow":
                return f"uzel {node['type']} bez definice"
            todo += [by_id[sid], *(n for n in new if n.get("z") == sid)]
    return None


_VAR_RE = re.compile(r"\$\{[^}]*\}")


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _webrtc_ok(value: object) -> bool:
    """`webrtc` as library/go2rtc writes it: listen, candidates, ice_servers[].urls."""
    if not isinstance(value, dict) or not value.keys() <= {"listen", "candidates", "ice_servers"}:
        return False
    servers = value.get("ice_servers", [])
    return (
        isinstance(value.get("listen", ""), str)
        and _strings(value.get("candidates", []))
        and isinstance(servers, list)
        and all(
            isinstance(s, dict) and s.keys() == {"urls"} and _strings(s["urls"]) for s in servers
        )
    )


def check_go2rtc_config(body: bytes | None, current: str) -> str | None:
    """POST /api/config rewrites the whole go2rtc.yaml: only GO2RTC_KEYS may
    differ from `current` (GET /api/config just before, "" when there is none);
    any other key the user has stays exactly as it is (not dropped, not nulled)."""
    try:
        new = yaml.safe_load((body or b"").decode()) or {}
        old = yaml.safe_load(current) or {}
    except (yaml.YAMLError, UnicodeDecodeError):
        return "neplatný YAML"
    if not isinstance(new, dict) or not isinstance(old, dict):
        return "go2rtc.yaml, který není mapa"
    if set(_VAR_RE.findall((body or b"").decode())) - set(_VAR_RE.findall(current)):
        return "proměnná ${…}"  # go2rtc expands ${ENV} from the add-on's environment
    if "webrtc" in new and new["webrtc"] != old.get("webrtc") and not _webrtc_ok(new["webrtc"]):
        return "tvar klíče webrtc"
    for key in [*old, *(k for k in new if k not in old)]:
        if key not in GO2RTC_KEYS and (key not in new or key not in old or new[key] != old[key]):
            return f"klíč {key}"
    return None
