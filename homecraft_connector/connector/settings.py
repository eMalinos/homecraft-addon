"""Where things are. Defaults are HA OS (under the Supervisor); env overrides
them for the dev run next to HA Container, where there is no Supervisor."""

import ipaddress
import json
import os
import urllib.parse
from pathlib import Path

# CONNECTOR_DEV=1 allows a ws:// server without Cloudflare Access (and the panel
# without ingress, #556). Any other value is production.
DEV = os.environ.get("CONNECTOR_DEV") == "1"

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_WS_URL = os.environ.get("CORE_WS_URL", "ws://supervisor/core/websocket")
CORE_HTTP_URL = os.environ.get("CORE_HTTP_URL", "http://supervisor/core")
# "" = no Supervisor (dev): its checks and `supervisor/api` answer `unsupported`.
SUPERVISOR_URL = os.environ.get("SUPERVISOR_URL", "http://supervisor")
# HA core directly (ingress needs it): on HA OS the `homeassistant` hostname.
HA_URL = os.environ.get("HA_URL", "http://homeassistant:8123")
CONFIG_ROOT = Path(os.environ.get("CONFIG_ROOT", "/homeassistant"))
# `data.uuid` of .storage/core.uuid, read by run.sh (the agent's own AppArmor
# profile denies .storage); "" when unknown.
HA_INSTALL_UUID = os.environ.get("HA_INSTALL_UUID", "").strip()


def options() -> dict:
    """The add-on options (`/data/options.json`, written by the Supervisor);
    env SERVER_URL overrides `server_url`."""
    try:
        opts = json.loads((DATA_DIR / "options.json").read_text())
    except FileNotFoundError:
        opts = {}
    opts.setdefault("server_url", "wss://agent.homecraft.cz/api/agent/ws")
    opts.setdefault("paused", False)
    if os.environ.get("SERVER_URL"):
        opts["server_url"] = os.environ["SERVER_URL"]
    return opts


def lan_ws(url: str) -> bool:
    """A ws:// URL whose host is a literal private address (RFC 1918, ULA,
    link-local): a dev Homecraft on the LAN without Cloudflare. Hostnames never
    (DNS can point anywhere), loopback never (it is the add-on itself)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "ws":
        return False
    try:
        ip = ipaddress.ip_address(parts.hostname or "")
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_private and not (ip.is_loopback or ip.is_unspecified)


def version() -> str:
    """The add-on version, baked in at build time (BUILD_VERSION from the builder)."""
    return os.environ.get("CONNECTOR_VERSION") or "dev"
