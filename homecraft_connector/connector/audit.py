"""What Homecraft did: the last operations, for the panel (#556).

Kept in memory (the last `MAXLEN`) and appended to /data/audit.jsonl, which
holds the last `KEEP` lines (trimmed at start and once it passes 2 × KEEP).
A record names the operation and its target (WS command type, HTTP method +
path, file path), never a payload, file content or token."""

import json
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import settings
from .files import write_private

logger = logging.getLogger(__name__)

MAXLEN = 200
KEEP = 1000
MAX_TARGET = 200

_recent: deque[dict] = deque(maxlen=MAXLEN)
# One worker: the file is written off the event loop, in order.
_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit")
_lines = 0  # in the file; touched by the writer only (and by load before it runs)


def _path() -> Path:
    return settings.DATA_DIR / "audit.jsonl"


def _trim(path: Path) -> list[str]:
    """Keep the file's last KEEP lines (atomic rewrite); returns them."""
    global _lines
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    lines = lines[-KEEP:]
    try:
        write_private(path, "".join(line + "\n" for line in lines).encode())
    except OSError as e:
        logger.warning("Audit nelze zkrátit: %s", type(e).__name__)
    _lines = len(lines)
    return lines


def load() -> None:
    """At start: trim the file and fill memory from it."""
    _recent.clear()
    for line in _trim(_path())[-MAXLEN:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            _recent.append(entry)


def _append(path: Path, line: str) -> None:
    global _lines
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("Audit nelze zapsat: %s", type(e).__name__)
        return
    _lines += 1
    if _lines > 2 * KEEP:
        _trim(path)


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
    _writer.submit(_append, _path(), json.dumps(entry) + "\n")


def flush() -> None:
    """Wait for the pending writes (tests)."""
    _writer.submit(lambda: None).result()


def recent() -> list[dict]:
    """Newest first."""
    return list(reversed(_recent))
