"""The Cloudflare Access service token (`<client_id>:<client_secret>`).

Kept in /data/cf_access.json (0600), never in the options (their YAML view
shows values) and never logged. Only `load` hands it out, to the client.

A token pushed by Homecraft (`access_token.set`, #565) is only staged in
cf_access.pending.json: `load` hands it out first, so the next connection
tries it; Access letting that connection through `commit`s it, a refusal
`discard`s it and the active token is used again. A bad push never cuts the
home off."""

import json
import os
import time
from pathlib import Path

from . import settings
from .files import write_private


def _path() -> Path:
    return settings.DATA_DIR / "cf_access.json"


def _pending_path() -> Path:
    return settings.DATA_DIR / "cf_access.pending.json"


def _visible_ascii(text: str) -> bool:
    return all(33 <= ord(c) <= 126 for c in text)


def parse(token: str) -> dict:
    """ValueError unless the token is `<client_id>:<client_secret>`, both non-empty
    visible ASCII (they become HTTP header values)."""
    client_id, sep, secret = token.partition(":")
    client_id, secret = client_id.strip(), secret.strip()
    if not (sep and client_id and secret and _visible_ascii(client_id + secret)):
        raise ValueError("expected <client_id>:<client_secret>")
    return {"client_id": client_id, "client_secret": secret}


def save(token: str) -> None:
    """Set in the panel: active at once; a staged push is dropped (the user wins)."""
    write_private(_path(), json.dumps(parse(token)).encode())
    discard()


def stage(token: str) -> None:
    """Pushed by Homecraft: tried on the next connection (see the module doc)."""
    data = {**parse(token), "updated_at": round(time.time(), 3)}
    write_private(_pending_path(), json.dumps(data).encode())


def pending() -> bool:
    return _read(_pending_path()) is not None


def commit() -> None:
    """The staged token becomes the active one (atomic rename, keeps 0600)."""
    os.replace(_pending_path(), _path())


def discard() -> None:
    _pending_path().unlink(missing_ok=True)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        if isinstance(data["client_id"], str) and isinstance(data["client_secret"], str):
            return data
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    return None


def load() -> tuple[str, str] | None:
    """(client_id, client_secret), a staged one first; None when not set (or unreadable)."""
    data = _read(_pending_path()) or _read(_path())
    return (data["client_id"], data["client_secret"]) if data else None


def updated_by_homecraft() -> float | None:
    """When Homecraft pushed the active token; None when it was set in the panel."""
    data = _read(_path())
    value = data.get("updated_at") if data else None
    return value if isinstance(value, (int, float)) else None


def clear() -> None:
    _path().unlink(missing_ok=True)
    discard()
