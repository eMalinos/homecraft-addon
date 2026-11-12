"""The content check (Security, level 2): what Homecraft may write, on top of
where (policy.py). Nothing that runs commands or sends data out gets into HA:
YAML packages, automation/script configs over REST, Node-RED flows and the
go2rtc config through ingress. Conservative on purpose, never widened by the
options. Pure functions, no I/O; each check returns the refused item or None."""

import json
import re

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
    }
)
# homeassistant: customize stays; these widen what HA loads or lets in.
HOMEASSISTANT_KEYS = frozenset(
    {"auth_providers", "packages", "allowlist_external_dirs", "allowlist_external_urls"}
)
FORBIDDEN_PLATFORMS = frozenset({"command_line", "shell_command", "rest", "scrape"})
# Every service of these domains; `hassio` as a whole: add-on stdin/start/stop,
# host reboot/shutdown, backups (the library calls none of it).
FORBIDDEN_DOMAINS = frozenset(
    {"shell_command", "rest_command", "python_script", "pyscript", "hassio"}
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
    """A service name in a forbidden domain, or one whose domain is a template
    (could be any). `script.hfb_notify_{{ channel }}` keeps its domain."""
    if not isinstance(name, str):
        return False
    fixed = re.split(r"\{[{%#]", name.strip().lower(), maxsplit=1)[0]
    if "." not in fixed:
        return fixed != name.strip().lower()  # a template before the dot
    return fixed.split(".", 1)[0] in FORBIDDEN_DOMAINS


def _walk(node: object, seen: set[int], config: bool = True) -> str | None:
    """The first forbidden service anywhere in the tree, and with `config`
    (HA configuration, not a dashboard) the first forbidden key or platform."""
    if isinstance(node, dict | list):
        if id(node) in seen:  # YAML aliases: each shared node once (no alias bomb)
            return None
        seen.add(id(node))
    if isinstance(node, list):
        for item in node:
            if found := _walk(item, seen, config):
                return found
    elif isinstance(node, dict):
        for key, value in node.items():
            if config and key in FORBIDDEN_KEYS:
                return str(key)
            if config and key == "homeassistant" and isinstance(value, dict):
                if bad := HOMEASSISTANT_KEYS & value.keys():
                    return f"homeassistant: {sorted(bad)[0]}"
            if config and key == "platform" and value in FORBIDDEN_PLATFORMS:
                return f"platform: {value}"
            if key in SERVICE_KEYS and service_refused(value):
                return f"{key}: {value}"
            if found := _walk(value, seen, config):
                return found
    return None


def check_yaml(path: str, text: str) -> str | None:
    """A package file (`path` only names it): the refused item, None when it passes."""
    data, why = _load(text)
    return why or _walk(data, set())


def check_tags(text: str) -> str | None:
    """A theme: no code runs, but HA's loader resolves the tags (`!include`,
    `!secret`, `!env_var`) in themes/ too."""
    return _load(text)[1]


def check_dashboard(config: object) -> str | None:
    """lovelace/config/save: tap/hold actions (`perform_action`, `service`).
    Card options are not HA configuration: a card key named `rest` is fine."""
    return _walk(config, set(), config=False)


def check_automation_body(body: bytes | None) -> str | None:
    """POST /api/config/(automation|script)/config/<id>: the same check as a package."""
    try:
        data = json.loads(body or b"")
    except ValueError:
        return "neplatný JSON"
    return _walk(data, set())


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
    A user's own tab or subflow (not Homecraft's) must not change at all."""
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
    todo = [n for n in new if live.get(n["id"]) != n]
    for node in todo:
        old = live.get(node["id"])
        if old is not None and old.get("type") in ("tab", "subflow"):
            if not any(node["id"] == t or node["id"].startswith(t + "_") for t in ours):
                return f"změna uživatelova {old.get('type')} {node['id']}"
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
    for key in [*old, *(k for k in new if k not in old)]:
        if key not in GO2RTC_KEYS and (key not in new or key not in old or new[key] != old[key]):
            return f"klíč {key}"
    return None
