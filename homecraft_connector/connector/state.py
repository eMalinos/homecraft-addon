"""The panel's switches that survive a restart: /data/state.json (#556)."""

import json

from . import settings
from .files import write_private


def _path():
    return settings.DATA_DIR / "state.json"


def load_paused() -> bool:
    try:
        return json.loads(_path().read_text()).get("paused") is True
    except (FileNotFoundError, ValueError, AttributeError):
        return False


def save_paused(paused: bool) -> None:
    write_private(_path(), json.dumps({"paused": bool(paused)}).encode())
