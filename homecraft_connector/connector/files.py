import os
from pathlib import Path

from . import policy, settings


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


# --- file.* operations under HA's config directory (#555) -------------------

TMP_SUFFIX = ".hfb-tmp"
MAX_READ = 12 * 1024 * 1024  # the reply must fit a 16 MiB frame, JSON escaping included


def _target(op: str, path: object) -> Path:
    """The real path for `op`, checked against the allowlist before and after
    resolving symlinks (a link must not lead out of the allowed directories)."""
    if not policy.file_allowed(op, path):
        raise policy.not_allowed(f"file.{op} {path}")
    root = settings.CONFIG_ROOT.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not policy.file_allowed(
        op, target.relative_to(root).as_posix()
    ):
        raise policy.not_allowed(f"file.{op} {path}")
    return target


def read(path: str) -> str | None:
    """The text, None when the file is missing (or a directory)."""
    target = _target("read", path)
    if not target.is_file():
        return None
    if target.stat().st_size > MAX_READ:
        raise policy.OpError("too_large", f"{path} is over {MAX_READ} bytes")
    return target.read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    target = _target("write", path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def delete(path: str) -> None:
    """A missing file is already deleted."""
    _target("delete", path).unlink(missing_ok=True)


def list_dir(path: str) -> list[str]:
    """Names of the directory's direct children, [] when it is missing."""
    target = _target("list", path)
    return sorted(p.name for p in target.iterdir()) if target.is_dir() else []
