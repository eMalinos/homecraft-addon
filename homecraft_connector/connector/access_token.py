"""The Cloudflare Access service token (`<client_id>:<client_secret>`).

Kept in /data/cf_access.json (0600), never in the options (their YAML view
shows values) and never logged. Only `load` and `active` hand it out, to the
client.

A token pushed by Homecraft (`access_token.set`, #565) is only staged in
cf_access.pending.json. Every connection tries it first and falls back to the
active token when Access refuses it; the first connection Access lets through
`commit`s it. It stays staged until then, or until a new push or the panel
replaces it. A bad push never cuts the home off.

Writes hold one lock: the client commits from a worker thread while the panel
saves on the event loop."""

import hashlib
import json
import threading
import time
from pathlib import Path

from . import settings
from .files import write_private

Token = tuple[str, str]  # (client_id, client_secret)

_lock = threading.Lock()


def _path() -> Path:
    return settings.DATA_DIR / "cf_access.json"


def _pending_path() -> Path:
    return settings.DATA_DIR / "cf_access.pending.json"


def _visible_ascii(text: str) -> bool:
    return all(33 <= ord(c) <= 126 for c in text)


def parse(token: str) -> Token:
    """ValueError unless the token is `<client_id>:<client_secret>`, both non-empty
    visible ASCII (they become HTTP header values)."""
    client_id, sep, secret = token.partition(":")
    client_id, secret = client_id.strip(), secret.strip()
    if not (sep and client_id and secret and _visible_ascii(client_id + secret)):
        raise ValueError("expected <client_id>:<client_secret>")
    return client_id, secret


def fingerprint(token: Token) -> str:
    """What the hello reports (#565): Homecraft compares it with its own token's,
    so a rotated secret with the same client id counts as a new token too."""
    return hashlib.sha256(f"{token[0]}:{token[1]}".encode()).hexdigest()[:16]


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        if isinstance(data["client_id"], str) and isinstance(data["client_secret"], str):
            return data
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    return None


def _token(data: dict | None) -> Token | None:
    return (data["client_id"], data["client_secret"]) if data else None


def _write(path: Path, token: Token, **extra) -> None:
    data = {"client_id": token[0], "client_secret": token[1], **extra}
    write_private(path, json.dumps(data).encode())


def save(token: str) -> None:
    """Set in the panel: active at once; a staged push is dropped (the user wins)."""
    parsed = parse(token)
    with _lock:
        _write(_path(), parsed)
        _pending_path().unlink(missing_ok=True)


def stage(token: str) -> bool:
    """Pushed by Homecraft (ValueError when malformed). False when nothing
    changed: the same token is already staged, or it is the active one."""
    parsed = parse(token)
    with _lock:
        if _token(_read(_pending_path())) == parsed:
            return False
        if _token(_read(_path())) == parsed:
            _pending_path().unlink(missing_ok=True)
            return False
        _write(_pending_path(), parsed)
        return True


def commit(token: Token) -> bool:
    """`token`, staged, got through Access: make it the active one. False (and
    nothing written) when the staged token is no longer `token`: the panel or
    a newer push replaced it meanwhile."""
    with _lock:
        if _token(_read(_pending_path())) != token:
            return False
        _write(_path(), token, updated_at=round(time.time(), 3))
        _pending_path().unlink(missing_ok=True)
        return True


def load() -> tuple[Token | None, bool]:
    """(token to connect with, staged?): a staged token first, then the active one."""
    with _lock:
        staged = _token(_read(_pending_path()))
        return (staged, True) if staged else (_token(_read(_path())), False)


def active() -> Token | None:
    return _token(_read(_path()))


def pending() -> bool:
    return _read(_pending_path()) is not None


def updated_by_homecraft() -> float | None:
    """When a token pushed by Homecraft became the active one; None when it was set in the panel."""
    data = _read(_path())
    value = data.get("updated_at") if data else None
    return value if isinstance(value, (int, float)) else None


def clear() -> None:
    with _lock:
        _path().unlink(missing_ok=True)
        _pending_path().unlink(missing_ok=True)
