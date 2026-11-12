"""What an unpaired agent tells Homecraft about itself in the hello, so the
operator can recognise the pairing request: the HA name and two fingerprints.

Fingerprints are the first 16 hex characters of sha256: the machine id and
the HA uuid themselves never leave the add-on (nor reach the log or the panel).
Collected once at start; a missing value is left out, an error only logged."""

import hashlib
import json
import logging

import aiohttp

from . import settings

logger = logging.getLogger(__name__)

HA_NAME_MAX = 64


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _ha_uuid() -> str | None:
    """`data.uuid` from core.uuid: in no Supervisor or core API (AppArmor allows this one file)."""
    try:
        data = json.loads((settings.CONFIG_ROOT / ".storage/core.uuid").read_text())
        value = data["data"]["uuid"]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("core.uuid nejde přečíst: %s", type(e).__name__)
        return None
    return value if isinstance(value, str) and value else None


async def _machine_id(http: aiohttp.ClientSession) -> str | None:
    """`machine_id` from the Supervisor's /info (in its api_bypass: any add-on may call it)."""
    if not settings.SUPERVISOR_URL:  # dev without a Supervisor
        return None
    async with http.get(
        f"{settings.SUPERVISOR_URL}/info",
        headers={"Authorization": f"Bearer {settings.SUPERVISOR_TOKEN}"},
    ) as resp:
        if resp.status != 200:
            logger.warning("Supervisor /info: HTTP %s", resp.status)
            return None
        value = (await resp.json()).get("data", {}).get("machine_id")
    return value if isinstance(value, str) and value else None


async def _ha_name(http: aiohttp.ClientSession) -> str | None:
    """`location_name` from core's get_config (the self-check's connection)."""
    async with http.ws_connect(
        settings.CORE_WS_URL, timeout=aiohttp.ClientWSTimeout(ws_close=10)
    ) as ws:
        await ws.receive_json(timeout=10)  # auth_required
        await ws.send_json({"type": "auth", "access_token": settings.SUPERVISOR_TOKEN})
        if (await ws.receive_json(timeout=10)).get("type") != "auth_ok":
            logger.warning("get_config: přihlášení k HA selhalo")
            return None
        await ws.send_json({"id": 1, "type": "get_config"})
        while (reply := await ws.receive_json(timeout=10)).get("id") != 1:
            pass
    value = (reply.get("result") or {}).get("location_name")
    return value[:HA_NAME_MAX] if isinstance(value, str) and value else None


async def collect(http: aiohttp.ClientSession) -> dict:
    """`{"ha_name"?, "machine_fp"?, "ha_fp"?}`: only what was found."""
    out: dict = {}
    for key, get, fp in (
        ("ha_name", _ha_name, False),
        ("machine_fp", _machine_id, True),
    ):
        try:
            value = await get(http)
        except Exception as e:  # never stop the start-up for a fingerprint
            logger.warning("%s nezjištěno: %s", key, type(e).__name__)
            continue
        if value:
            out[key] = _fp(value) if fp else value
    if ha_uuid := _ha_uuid():
        out["ha_fp"] = _fp(ha_uuid)
    return out
