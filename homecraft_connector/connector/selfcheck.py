"""Start-up self-check: logs OK / FAIL for what the Connector relies on.

Read-only. Verifies the spec's assumptions on a real HA OS (*To verify first*);
the result goes into the issue by hand."""

import contextlib
import logging
import os
from collections.abc import AsyncIterator

import aiohttp

from . import settings

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)

WS_COMMANDS = [
    {"type": "get_config"},
    {"type": "lovelace/dashboards/list"},
    {"type": "config/label_registry/list"},
]


def _log(name: str, status: str, detail: str = "") -> None:
    logger.info("self-check %-40s %s%s", name, status, f" ({detail})" if detail else "")


class CoreAuthError(Exception):
    """Core refused SUPERVISOR_TOKEN; the message is HA's reply type."""


@contextlib.asynccontextmanager
async def core_ws(session: aiohttp.ClientSession) -> AsyncIterator[aiohttp.ClientWebSocketResponse]:
    """Core's WebSocket, authenticated with SUPERVISOR_TOKEN."""
    async with session.ws_connect(
        settings.CORE_WS_URL, timeout=aiohttp.ClientWSTimeout(ws_close=10)
    ) as ws:
        await ws.receive_json(timeout=10)  # auth_required
        await ws.send_json({"type": "auth", "access_token": settings.SUPERVISOR_TOKEN})
        auth = await ws.receive_json(timeout=10)
        if auth.get("type") != "auth_ok":
            raise CoreAuthError(auth.get("type", "?"))
        yield ws


async def command(ws: aiohttp.ClientWebSocketResponse, i: int, cmd: dict) -> dict:
    """Send `cmd` with id `i`; HA's reply to it (other messages are skipped)."""
    await ws.send_json({"id": i, **cmd})
    while (reply := await ws.receive_json(timeout=10)).get("id") != i:
        pass
    return reply


async def _check_ws(session: aiohttp.ClientSession) -> None:
    commands = WS_COMMANDS  # `supervisor/api` goes to the Supervisor (http check)
    try:
        async with core_ws(session) as ws:
            _log("ws auth", "OK")
            for i, cmd in enumerate(commands, start=1):
                reply = await command(ws, i, cmd)
                name = "ws " + cmd["type"] + (f" {cmd['endpoint']}" if "endpoint" in cmd else "")
                if reply.get("success"):
                    _log(name, "OK")
                else:
                    _log(name, "FAIL", (reply.get("error") or {}).get("code", "?"))
    except CoreAuthError as e:
        _log("ws auth", "FAIL", str(e))
    except (TimeoutError, aiohttp.ClientError, OSError, TypeError, ValueError) as e:
        _log("ws " + settings.CORE_WS_URL, "FAIL", type(e).__name__)


async def _check_http(
    session: aiohttp.ClientSession, name: str, url: str, expect: int, token: bool
) -> None:
    headers = {"Authorization": f"Bearer {settings.SUPERVISOR_TOKEN}"} if token else {}
    try:
        async with session.get(url, headers=headers) as resp:
            status = resp.status
    except (TimeoutError, aiohttp.ClientError, OSError) as e:
        _log(name, "FAIL", type(e).__name__)
        return
    _log(name, "OK" if status == expect else "FAIL", f"HTTP {status}")


async def resolve_ha_url() -> None:
    """HA_URL's port from the Supervisor's /core/info: `http: server_port`
    need not be 8123 (e.g. 80). Kept when env sets HA_URL or there is no
    Supervisor; the default stays when the Supervisor does not answer."""
    if "HA_URL" in os.environ or not settings.SUPERVISOR_URL:
        return
    name = "http supervisor /core/info"
    headers = {"Authorization": f"Bearer {settings.SUPERVISOR_TOKEN}"}
    try:
        async with (
            aiohttp.ClientSession(timeout=TIMEOUT) as session,
            session.get(f"{settings.SUPERVISOR_URL}/core/info", headers=headers) as resp,
        ):
            info = (await resp.json())["data"]
        port = int(info["port"])
    except (TimeoutError, aiohttp.ClientError, OSError, KeyError, TypeError, ValueError) as e:
        _log(name, "FAIL", type(e).__name__)
        return
    settings.HA_URL = f"http://homeassistant:{port}"
    _log(name, "OK", settings.HA_URL + (", ssl: ingress nefunguje" if info.get("ssl") else ""))


async def run() -> None:
    # The session timeout bounds every request, the WebSocket handshake included.
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        await _check_ws(session)
        if settings.SUPERVISOR_URL:
            # Listing the store needs hassio_role manager.
            await _check_http(
                session, "http supervisor /store", f"{settings.SUPERVISOR_URL}/store", 200, True
            )
        else:
            _log("http supervisor /store", "unsupported", "bez Supervisoru")
        await _check_http(
            session, "http core REST /api/", f"{settings.CORE_HTTP_URL}/api/", 200, True
        )
        # Direct reach to core (ingress), without a token: a 401 would make HA
        # report a failed login on every start, the manifest needs no auth.
        await _check_http(
            session, "http core /manifest.json", f"{settings.HA_URL}/manifest.json", 200, False
        )
