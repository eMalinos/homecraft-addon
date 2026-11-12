import os
from pathlib import Path


def write_private(path: Path, data: bytes) -> None:
    """Atomically replace `path` with `data`, readable by the owner only (0600)."""
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)  # a leftover could carry looser permissions
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
