"""Start-up self-check: logs OK / FAIL for what the Connector relies on.

Read-only. Verifies the spec's assumptions on a real HA OS (*To verify first*);
the result goes into the issue by hand."""

import logging

import aiohttp

from . import settings

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)

WS_COMMANDS = [
    {"type": "get_config"},
    {"type": "lovelace/dashboards/list"},
    {"type": "config/label_registry/list"},
]
SUPERVISOR_WS_COMMAND = {
    "type": "supervisor/api",
    "endpoint": "/addons/a0d7b954_nodered/info",
    "method": "get",
}


def _log(name: str, status: str, detail: str = "") -> None:
    logger.info("self-check %-40s %s%s", name, status, f" ({detail})" if detail else "")


async def _check_ws(session: aiohttp.ClientSession) -> None:
    commands = list(WS_COMMANDS)
    if settings.SUPERVISOR_URL:
        commands.append(SUPERVISOR_WS_COMMAND)
    else:
        _log("ws supervisor/api", "unsupported", "bez Supervisoru")
    try:
        async with session.ws_connect(
            settings.CORE_WS_URL, timeout=aiohttp.ClientWSTimeout(ws_close=10)
        ) as ws:
            await ws.receive_json(timeout=10)  # auth_required
            await ws.send_json({"type": "auth", "access_token": settings.SUPERVISOR_TOKEN})
            auth = await ws.receive_json(timeout=10)
            if auth.get("type") != "auth_ok":
                _log("ws auth", "FAIL", auth.get("type", "?"))
                return
            _log("ws auth", "OK")
            for i, cmd in enumerate(commands, start=1):
                await ws.send_json({"id": i, **cmd})
                while (reply := await ws.receive_json(timeout=10)).get("id") != i:
                    pass
                name = "ws " + cmd["type"] + (f" {cmd['endpoint']}" if "endpoint" in cmd else "")
                if reply.get("success"):
                    _log(name, "OK")
                else:
                    _log(name, "FAIL", (reply.get("error") or {}).get("code", "?"))
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
        # Direct reach to core (ingress): 401 without a token means reachable.
        await _check_http(session, "http core /api/", f"{settings.HA_URL}/api/", 401, False)
