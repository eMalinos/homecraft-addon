"""The Cloudflare Access service token (`<client_id>:<client_secret>`).

Kept in /data/cf_access.json (0600), never in the options (their YAML view
shows values) and never logged. Only `load` hands it out, to the client."""

import json
import os

from . import settings


def _path():
    return settings.DATA_DIR / "cf_access.json"


def save(token: str) -> None:
    """ValueError unless the token is `<client_id>:<client_secret>`, both non-empty."""
    client_id, sep, secret = token.strip().partition(":")
    if not (sep and client_id and secret):
        raise ValueError("expected <client_id>:<client_secret>")
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)  # a leftover could carry looser permissions
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": client_id, "client_secret": secret}, f)
    os.replace(tmp, path)


def load() -> tuple[str, str] | None:
    """(client_id, client_secret), or None when not set (or unreadable)."""
    try:
        data = json.loads(_path().read_text())
        return data["client_id"], data["client_secret"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def clear() -> None:
    _path().unlink(missing_ok=True)
