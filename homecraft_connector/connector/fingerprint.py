"""What an unpaired agent tells Homecraft about itself in the hello, so the
operator can recognise the pairing request: the HA name and two fingerprints.

Fingerprints are the first 16 hex characters of sha256: the machine id and
the HA install uuid themselves never leave the add-on (nor reach the log or
the panel). A missing value is left out of the hello and an error only logged."""

import asyncio
import hashlib
import logging

import aiohttp

from . import selfcheck, settings

logger = logging.getLogger(__name__)

HA_NAME_MAX = 64
# Each lookup at most this long: they run before a connection attempt.
LOOKUP_TIMEOUT = 5.0


class LookupFailed(Exception):
    """A lookup failed with a message of our own (no values in it): logged verbatim."""


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


async def _machine_fp(http: aiohttp.ClientSession) -> str | None:
    """`machine_id` from the Supervisor's /info (in its api_bypass: any add-on may call it)."""
    if not settings.SUPERVISOR_URL:  # dev without a Supervisor
        return None
    async with http.get(
        f"{settings.SUPERVISOR_URL}/info",
        headers={"Authorization": f"Bearer {settings.SUPERVISOR_TOKEN}"},
    ) as resp:
        if resp.status != 200:
            raise LookupFailed(f"Supervisor /info: HTTP {resp.status}")
        value = (await resp.json()).get("data", {}).get("machine_id")
    return _fp(value) if isinstance(value, str) and value else None


async def _ha_name(http: aiohttp.ClientSession) -> str | None:
    """`location_name` from core's get_config."""
    async with selfcheck.core_ws(http) as ws:
        reply = await selfcheck.command(ws, 1, {"type": "get_config"})
    value = (reply.get("result") or {}).get("location_name")
    return value[:HA_NAME_MAX] if isinstance(value, str) and value else None


def _detail(e: BaseException) -> str:
    """Our own messages (HTTP status, HA's auth reply); else the type only."""
    if isinstance(e, (LookupFailed, selfcheck.CoreAuthError)):
        return str(e)
    return type(e).__name__


LOOKUPS = {"ha_name": _ha_name, "machine_fp": _machine_fp}


class Collector:
    """Called before every connection attempt of an unpaired agent: looks up only
    what is still missing (core or the Supervisor may not be ready at start),
    keeps what it found."""

    def __init__(self) -> None:
        # HA's install uuid (.storage/core.uuid) is read once by run.sh at start,
        # outside the agent's AppArmor subprofile: the agent never reads .storage.
        # No retry for it; a restart picks it up.
        self.found = {"ha_fp": _fp(settings.HA_INSTALL_UUID)} if settings.HA_INSTALL_UUID else {}
        self._warned: set[str] = set()  # one warning per value, not one per attempt

    async def __call__(self) -> dict:
        missing = [key for key in LOOKUPS if key not in self.found]
        if missing:
            async with aiohttp.ClientSession(timeout=selfcheck.TIMEOUT) as http:
                results = await asyncio.gather(
                    *(asyncio.wait_for(LOOKUPS[key](http), LOOKUP_TIMEOUT) for key in missing),
                    return_exceptions=True,
                )
            for key, value in zip(missing, results, strict=True):
                if isinstance(value, BaseException):
                    if key not in self._warned:
                        self._warned.add(key)
                        logger.warning("%s zatím nezjištěno: %s", key, _detail(value))
                elif value:
                    self.found[key] = value
        return dict(self.found)
