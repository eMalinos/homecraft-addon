"""What Homecraft did: the last operations, for the panel (#556).

Kept in memory (the last `MAXLEN`) and appended to /data/audit.jsonl, which
`load` trims to the last `KEEP` lines at start. A record names the operation
and its target (WS command type, HTTP method + path, file path), never a
payload, file content or token."""

import json
import logging
import time
from collections import deque

from . import settings

logger = logging.getLogger(__name__)

MAXLEN = 200
KEEP = 1000
MAX_TARGET = 200

_recent: deque[dict] = deque(maxlen=MAXLEN)


def _path():
    return settings.DATA_DIR / "audit.jsonl"


def load() -> None:
    """At start: trim the file to its last KEEP lines and fill memory from it."""
    _recent.clear()
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()[-KEEP:]
    except (FileNotFoundError, UnicodeDecodeError):
        return
    try:
        _path().write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    except OSError as e:
        logger.warning("Audit nelze zkrátit: %s", type(e).__name__)
    for line in lines[-MAXLEN:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            _recent.append(entry)


def record(
    op: str, target: str, ok: bool, code: str | None = None, duration_ms: float | None = None
) -> None:
    """One operation. `code` is the error code when not ok (`not_allowed`, `paused`, …)."""
    entry = {
        "ts": round(time.time(), 3),
        "op": str(op)[:32],
        "target": str(target)[:MAX_TARGET],
        "ok": bool(ok),
        "code": None if ok else str(code or "error")[:64],
        "ms": None if duration_ms is None else round(duration_ms, 1),
    }
    _recent.append(entry)
    # ponytail: one append per operation (sync, small); batch it if the disk shows up
    try:
        with _path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.warning("Audit nelze zapsat: %s", type(e).__name__)


def recent() -> list[dict]:
    """Newest first."""
    return list(reversed(_recent))
