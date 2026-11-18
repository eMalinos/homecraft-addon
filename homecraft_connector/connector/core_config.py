"""configuration.yaml through the Connector. Never a free write: `homeassistant:
auth_providers`, `http:` or an `!include` there would hand HA's login to
Homecraft. One operation adds the packages include the library needs
(`config.packages_include`), checks it with HA and restores the file when HA
rejects it. Same rules as Homecraft's config_file_applicator._with_packages_include."""

import asyncio
import base64
import json
import os
import re
import stat

import yaml

from . import files, policy, settings

FILE = "configuration.yaml"
PACKAGES_RE = re.compile(
    r"^[ \t]+packages:[ \t]*!include_dir_named[ \t]+packages[ \t]*\r?$", re.MULTILINE
)
_HA_KEY_RE = re.compile(r"^homeassistant:[ \t]*(#[^\r\n]*)?(?=\r?$)", re.MULTILINE)
SNIPPET = "homeassistant:\n  packages: !include_dir_named packages"


class _Tagged:
    """A value behind an HA tag (`!include`, `!secret` ...): never a plain block."""


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: _Tagged())


def with_packages_include(configuration: str) -> str | None:
    """configuration.yaml with the packages include added, or None when it
    cannot be added safely (unparsable, `homeassistant:` not a plain block,
    an own `packages:`)."""
    try:
        data = yaml.load(configuration, Loader=_Loader)  # noqa: S506 - safe loader subclass
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    nl = "\r\n" if "\r\n" in configuration else "\n"
    if "homeassistant" not in data:
        return configuration.rstrip("\r\n") + nl + nl + SNIPPET.replace("\n", nl) + nl
    core = data["homeassistant"]
    key = _HA_KEY_RE.search(configuration)
    if not (core is None or isinstance(core, dict)) or not key or "packages" in (core or {}):
        return None
    rest = configuration[key.end() :]
    child = re.search(r"^([ \t]+)[^\s#]", rest, re.MULTILINE) if core else None
    indent = child.group(1) if child else "  "
    return configuration[: key.end()] + f"{nl}{indent}packages: !include_dir_named packages" + rest


def _path():
    path = settings.CONFIG_ROOT / FILE
    if path.is_symlink():  # the include goes into HA's own file, nowhere else
        raise policy.not_allowed(f"{FILE} is a symlink")
    return path


def _read() -> str:
    path = _path()
    if not path.is_file():
        raise policy.OpError("not_found", FILE)
    if path.stat().st_size > files.MAX_READ:
        raise policy.OpError("too_large", f"{FILE} is over {files.MAX_READ} bytes")
    with open(path, encoding="utf-8", newline="") as f:  # keeps CRLF
        return f.read()


def _write(text: str) -> None:
    """Atomic, keeping the file's permissions."""
    path = _path()
    mode = stat.S_IMODE(path.stat().st_mode)
    tmp = path.with_name(FILE + files.TMP_SUFFIX)
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    os.fchmod(fd, mode)  # past the umask
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


async def _check(http) -> str | None:
    """None when HA's check_config says valid, else what it reported."""
    reply = await http.core("POST", "/api/config/core/check_config")
    try:
        result = json.loads(base64.b64decode(reply["body_b64"]) or b"null")
    except ValueError:
        result = None
    if reply["status"] == 200 and isinstance(result, dict) and result.get("result") == "valid":
        return None
    if isinstance(result, dict) and result.get("errors"):
        return str(result["errors"])[:500]
    return f"HTTP {reply['status']}"


_lock = asyncio.Lock()  # one at a time: a restore must never undo another call's include


async def packages_include(http) -> str:
    async with _lock:
        return await _packages_include(http)


async def _packages_include(http) -> str:
    """`present` or `added`; `http.core(method, path)` calls HA's REST API."""
    text = await asyncio.to_thread(_read)
    if PACKAGES_RE.search(text):
        return "present"
    updated = with_packages_include(text)
    if updated is None:
        raise policy.OpError(
            "not_supported",
            f"{FILE}: homeassistant: is not a plain block or has its own packages:",
        )
    await asyncio.to_thread(_write, updated)
    try:
        problem = await _check(http)
    except BaseException:
        await asyncio.to_thread(_write, text)
        raise
    if problem:
        await asyncio.to_thread(_write, text)
        raise policy.OpError("check_failed", f"HA rejected {FILE} (restored): {problem}")
    return "added"
