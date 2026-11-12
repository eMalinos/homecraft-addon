"""A long-lived access token of a Home Assistant admin, for ingress only.

The Supervisor creates ingress sessions for HA core only, and core runs
`supervisor/api` for admins only, so the add-on asks core as that admin for
`/ingress/session` and nothing else. Entered write-only in the panel, kept in
/data/ha_token (0600), never in the options, never logged, never sent to
Homecraft."""

from pathlib import Path

from . import settings
from .files import write_private

MAX_LEN = 1024


def _path() -> Path:
    return settings.DATA_DIR / "ha_token"


def save(token: str) -> None:
    """ValueError unless visible ASCII (an HA token is a JWT)."""
    token = token.strip()
    if not token or len(token) > MAX_LEN or not all(33 <= ord(c) <= 126 for c in token):
        raise ValueError("not a token")
    write_private(_path(), token.encode())


def load() -> str | None:
    try:
        return _path().read_text().strip() or None
    except FileNotFoundError:
        return None


def clear() -> None:
    _path().unlink(missing_ok=True)
