"""The Cloudflare Access service token (`<client_id>:<client_secret>`).

Kept in /data/cf_access.json (0600), never in the options (their YAML view
shows values) and never logged. Only `load` hands it out, to the client."""

import json

from . import settings
from .files import write_private


def _path():
    return settings.DATA_DIR / "cf_access.json"


def _visible_ascii(text: str) -> bool:
    return all(33 <= ord(c) <= 126 for c in text)


def save(token: str) -> None:
    """ValueError unless the token is `<client_id>:<client_secret>`, both non-empty
    visible ASCII (they become HTTP header values)."""
    client_id, sep, secret = token.partition(":")
    client_id, secret = client_id.strip(), secret.strip()
    if not (sep and client_id and secret and _visible_ascii(client_id + secret)):
        raise ValueError("expected <client_id>:<client_secret>")
    write_private(_path(), json.dumps({"client_id": client_id, "client_secret": secret}).encode())


def load() -> tuple[str, str] | None:
    """(client_id, client_secret), or None when not set (or unreadable)."""
    try:
        data = json.loads(_path().read_text())
        return data["client_id"], data["client_secret"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def clear() -> None:
    _path().unlink(missing_ok=True)
