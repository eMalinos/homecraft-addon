"""`ws.open|send|close`: WebSocket sessions to HA core, one per backend socket.

Every HA message of a session goes to Homecraft as `{"op": "ws.msg", "session",
"msg"}`, and `{"op": "ws.closed", "session"}` when HA closes it."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable

import aiohttp

from . import policy, settings

logger = logging.getLogger(__name__)

MAX_SESSIONS = 8
MAX_MSG_SIZE = 16 * 1024 * 1024
AUTH_TIMEOUT = 10

Push = Callable[[dict], Awaitable[None]]


def ha_error(msg_id: object, code: str, message: str) -> dict:
    """A failed command's result, shaped as HA answers it."""
    return {
        "id": msg_id,
        "type": "result",
        "success": False,
        "error": {"code": code, "message": message},
    }


class Sessions:
    """The HA sessions of one Homecraft connection; `close_all` when it drops."""

    def __init__(self, http: aiohttp.ClientSession, push: Push) -> None:
        self._http, self._push = http, push
        self._open: dict[str, tuple[aiohttp.ClientWebSocketResponse, asyncio.Task]] = {}
        self._opening = 0

    async def open(self) -> dict:
        if len(self._open) + self._opening >= MAX_SESSIONS:
            raise policy.OpError("too_many_sessions", f"at most {MAX_SESSIONS}")
        self._opening += 1
        try:
            ws = await self._connect()
        finally:
            self._opening -= 1
        sid = uuid.uuid4().hex
        self._open[sid] = (ws, asyncio.create_task(self._pump(sid, ws)))
        return {"session": sid}

    async def _connect(self) -> aiohttp.ClientWebSocketResponse:
        try:
            ws = await self._http.ws_connect(
                settings.CORE_WS_URL,
                max_msg_size=MAX_MSG_SIZE,
                timeout=aiohttp.ClientWSTimeout(ws_close=AUTH_TIMEOUT),
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            raise policy.OpError("ha_unreachable", type(e).__name__) from None
        try:
            await ws.receive_json(timeout=AUTH_TIMEOUT)  # auth_required
            await ws.send_json({"type": "auth", "access_token": settings.SUPERVISOR_TOKEN})
            auth = await ws.receive_json(timeout=AUTH_TIMEOUT)
            if not isinstance(auth, dict) or auth.get("type") != "auth_ok":
                raise policy.OpError("auth_failed", "HA refused the add-on's token")
        except BaseException as e:
            await ws.close()
            if isinstance(e, (TimeoutError, aiohttp.ClientError, OSError, TypeError, ValueError)):
                raise policy.OpError("ha_unreachable", type(e).__name__) from None
            raise
        return ws

    async def _pump(self, sid: str, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    msg = json.loads(m.data)
                except ValueError:
                    continue
                await self._push({"op": "ws.msg", "session": sid, "msg": msg})
            if self._open.pop(sid, None) is not None:  # HA closed it, not us
                await self._push({"op": "ws.closed", "session": sid})
        except (ConnectionError, aiohttp.ClientError, RuntimeError):
            # Homecraft is gone; its own drop closes every session
            self._open.pop(sid, None)
            await ws.close()

    async def send(self, sid: str, msg: object) -> bool:
        """Forward an allowed command (True); refuse any other as HA would answer it."""
        entry = self._open.get(sid)
        if entry is None:
            raise policy.OpError("unknown_session", "")
        if not policy.ws_allowed(msg):
            kind = msg.get("type") if isinstance(msg, dict) else None
            logger.info("Odmítnuto: ws %s", kind)
            error = ha_error(
                msg.get("id") if isinstance(msg, dict) else None,
                "not_allowed",
                f"{kind} is not allowed by Homecraft Connector",
            )
            await self._push({"op": "ws.msg", "session": sid, "msg": error})
            return False
        await entry[0].send_json(msg)
        return True

    async def close(self, sid: str) -> None:
        entry = self._open.pop(sid, None)
        if entry is not None:
            entry[1].cancel()
            await entry[0].close()

    async def close_all(self) -> None:
        for sid in list(self._open):
            await self.close(sid)
