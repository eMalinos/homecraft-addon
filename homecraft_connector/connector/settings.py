"""Where things are. Defaults are HA OS (under the Supervisor); env overrides
them for the dev run next to HA Container, where there is no Supervisor."""

import json
import os
import re
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

_CONFIG_YAML = Path(__file__).resolve().parent.parent / "config.yaml"


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


def version() -> str:
    """The add-on version from config.yaml (no YAML parser for one line)."""
    try:
        m = re.search(r'^version:\s*"?([^"\s]+)', _CONFIG_YAML.read_text(), re.M)
    except FileNotFoundError:
        m = None
    return m.group(1) if m else "dev"
