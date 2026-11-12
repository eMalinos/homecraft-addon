"""The outgoing connection to Homecraft: challenge/hello, request loop, reconnect."""

import asyncio
import json
import logging
import random
import time
import uuid

import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import access_token, files, http_proxy, identity, policy, ws_proxy

logger = logging.getLogger(__name__)

CLOSE_UNAUTHORIZED = 4401
STABLE_AFTER = 60.0  # seconds connected before the backoff starts over
MAX_MSG_SIZE = 16 * 1024 * 1024

# Not paired (4401) or no valid Access token (403): retrying every minute
# forever would only knock on a closed door, so slow down to once an hour.
REFUSED_DELAYS = (60, 5 * 60, 15 * 60, 60 * 60)


class Backoff:
    """Reconnect delays: an outage 1, 2, 4 … 60 s; a refusal 1, 5, 15, 60 min, then hourly."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._outages = self._refusals = 0

    def next(self, refused: bool) -> float:
        if refused:
            delay = REFUSED_DELAYS[min(self._refusals, len(REFUSED_DELAYS) - 1)]
            self._refusals += 1
        else:
            delay = min(60, 2**self._outages)
            self._outages += 1
        return delay


def access_refused(e: aiohttp.WSServerHandshakeError) -> bool:
    """Cloudflare Access without a valid token: 401/403, or a redirect to its
    login page (aiohttp's ws_connect follows redirects, so that ends in 200 HTML)."""
    return bool(e.history) or e.status in (200, 401, 403) or 300 <= e.status < 400


class Client:
    def __init__(
        self,
        server_url: str,
        agent_id: uuid.UUID,
        key: Ed25519PrivateKey,
        version: str,
        paused: bool = False,
    ) -> None:
        self.server_url = server_url
        self.agent_id, self.key, self.version = agent_id, key, version
        self.paused = paused
        self.backoff = Backoff()
        self._wake = asyncio.Event()

    def reconnect_now(self) -> None:
        """Skip the current wait (panel button **Připojit znovu**, #556)."""
        self.backoff.reset()
        self._wake.set()

    async def wait(self, delay: float) -> None:
        """Sleep `delay` (+ up to 10 % jitter) unless `reconnect_now` cuts it short."""
        try:
            await asyncio.wait_for(self._wake.wait(), delay * random.uniform(1, 1.1))
        except TimeoutError:
            pass
        self._wake.clear()

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    refused = await self._connect_once(session)
                except Exception:
                    # Never die silently: an unexpected error counts as an outage.
                    logger.exception("Neočekávaná chyba spojení")
                    refused = False
                await self.wait(self.backoff.next(refused))

    async def _connect_once(self, session: aiohttp.ClientSession) -> bool:
        """One connection until it drops. True when Homecraft refused us."""
        token = access_token.load()
        headers = (
            {"CF-Access-Client-Id": token[0], "CF-Access-Client-Secret": token[1]} if token else {}
        )
        started = None
        try:
            async with session.ws_connect(
                self.server_url, headers=headers, heartbeat=30, max_msg_size=MAX_MSG_SIZE
            ) as ws:
                started = time.monotonic()
                logger.info("Připojeno k %s", self.server_url)
                await self._serve(ws, session)
                code = ws.close_code
        except aiohttp.WSServerHandshakeError as e:
            if access_refused(e):
                logger.warning("Chybí nebo neplatí přístupový token — zadej ho v panelu add-onu")
                return True
            logger.warning("Homecraft odmítl spojení (HTTP %s)", e.status)
            return False
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            logger.warning("Homecraft nedostupný: %s", type(e).__name__)
            return False
        finally:
            if started is not None and time.monotonic() - started >= STABLE_AFTER:
                self.backoff.reset()
        if code == CLOSE_UNAUTHORIZED:
            logger.warning("Agent není spárovaný — vlož párovací kód v Homecraftu")
            return True
        logger.info("Spojení s Homecraftem skončilo (kód %s)", code)
        return False

    async def _serve(
        self, ws: aiohttp.ClientWebSocketResponse, http: aiohttp.ClientSession
    ) -> None:
        lock = asyncio.Lock()

        async def send(data: dict) -> None:
            async with lock:
                await ws.send_json(data)

        sessions = ws_proxy.Sessions(http, send)
        tasks: set[asyncio.Task] = set()

        async def respond(request: dict) -> None:
            await send(await self._reply(request, http, sessions))

        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("op") == "challenge":
                    try:
                        sig = identity.sign_hello(self.agent_id, self.key, data["nonce"])
                    except (KeyError, TypeError, ValueError):
                        logger.warning("Neplatná výzva od serveru")
                        return
                    await send(
                        {
                            "op": "hello",
                            "agent_id": str(self.agent_id),
                            "version": self.version,
                            "sig": sig,
                        }
                    )
                elif "id" in data:
                    if data.get("op") in ("ws.send", "ws.close"):
                        await respond(data)  # inline: a session keeps its message order
                    else:
                        task = asyncio.create_task(respond(data))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
        finally:
            for task in list(tasks):
                task.cancel()
            await sessions.close_all()

    async def _reply(
        self, request: dict, http: aiohttp.ClientSession, sessions: ws_proxy.Sessions
    ) -> dict:
        rid, op = request["id"], request.get("op")
        if self.paused:
            return _error(rid, "paused", "")
        try:
            result = await _dispatch(op, request, http, sessions)
        except policy.OpError as e:
            if e.code == "not_allowed":
                logger.info("Odmítnuto: %s", e.message)
            return _error(rid, e.code, e.message)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            return _error(rid, "bad_request", f"{op}: {type(e).__name__}")
        except Exception as e:
            # The type only: messages can carry paths, payloads or HA's answers.
            logger.warning("Operace %s selhala: %s", op, type(e).__name__)
            return _error(rid, "internal", type(e).__name__)
        return {"id": rid, "ok": True, "result": result}


async def _dispatch(
    op: object, req: dict, http: aiohttp.ClientSession, sessions: ws_proxy.Sessions
) -> object:
    if op == "ws.open":
        return await sessions.open()
    if op == "ws.send":
        return await sessions.send(req["session"], req["msg"])
    if op == "ws.close":
        return await sessions.close(req["session"])
    if op == "http":
        return await http_proxy.handle(http, req)
    if op == "file.read":
        return await asyncio.to_thread(files.read, req["path"])
    if op == "file.write":
        return await asyncio.to_thread(files.write, req["path"], req["text"])
    if op == "file.delete":
        return await asyncio.to_thread(files.delete, req["path"])
    if op == "file.list":
        return await asyncio.to_thread(files.list_dir, req["path"])
    raise policy.OpError("unknown_op", str(op)[:64])


def _error(rid: object, code: str, message: str) -> dict:
    return {"id": rid, "ok": False, "error": {"code": code, "message": message}}
