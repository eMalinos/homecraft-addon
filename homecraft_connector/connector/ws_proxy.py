"""`ws.open|send|close`: WebSocket sessions to HA core, one per backend socket.

Every HA message of a session goes to Homecraft as `{"op": "ws.msg", "session",
"msg"}`, and `{"op": "ws.closed", "session"}` when HA closes it."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable

import aiohttp

from . import content, policy, settings

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

    def __init__(
        self,
        http: aiohttp.ClientSession,
        push: Push,
        paused: Callable[[], bool] = lambda: False,
    ) -> None:
        self._http, self._push, self._paused = http, push, paused
        self._open: dict[str, tuple[aiohttp.ClientWebSocketResponse, asyncio.Task]] = {}
        self._opening = 0

    @property
    def idle(self) -> bool:
        return not self._open and not self._opening

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
                if self._paused():  # set_paused closes the sessions; this covers the gap
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

    async def send(self, sid: str, msg: object) -> str | None:
        """Forward an allowed command (None); refuse any other as HA would
        answer it and return the refusal's code."""
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
            return "not_allowed"
        if msg["type"] == "lovelace/config/save" and (what := await self._dashboard_refused(msg)):
            e = content.refused(f"lovelace/config/save {msg.get('url_path')}", what)
            logger.info("Odmítnuto: %s", e.message)
            error = ha_error(msg.get("id"), e.code, e.message)
            await self._push({"op": "ws.msg", "session": sid, "msg": error})
            return e.code
        await entry[0].send_json(msg)
        return None

    async def _dashboard_refused(self, msg: dict) -> str | None:
        """Forbidden actions the saved config brings in that the dashboard in
        HA lacks: the user's own cards never block Homecraft. The live config
        comes over a session of its own, only when the new one has any."""
        config = msg.get("config")
        if not await asyncio.to_thread(content.check_dashboard, config):
            return None
        ws = await self._connect()
        try:
            await ws.send_json(
                {"id": 1, "type": "lovelace/config", "url_path": msg.get("url_path")}
            )
            while True:
                reply = await ws.receive_json(timeout=AUTH_TIMEOUT)
                if isinstance(reply, dict) and reply.get("id") == 1:
                    break
        except (TimeoutError, aiohttp.ClientError, TypeError, ValueError) as e:
            raise policy.OpError("ha_unreachable", type(e).__name__) from None
        finally:
            await ws.close()
        live = reply.get("result") if reply.get("success") else None  # none yet: whole
        return await asyncio.to_thread(content.check_dashboard, config, live)

    async def close(self, sid: str) -> None:
        entry = self._open.pop(sid, None)
        if entry is not None:
            entry[1].cancel()
            await entry[0].close()

    async def close_all(self, notify: bool = False) -> None:
        """`notify`: tell Homecraft (ws.closed), when its connection stays up."""
        sids = list(self._open)
        for sid in sids:
            await self.close(sid)
        for sid in sids if notify else ():
            await self._push({"op": "ws.closed", "session": sid})
