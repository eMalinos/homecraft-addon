"""The outgoing connection to Homecraft: challenge/hello, request loop, reconnect.

Operations come with #555; until then every request is refused."""

import asyncio
import json
import logging
import random
import time
import uuid

import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import access_token, identity

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
                refused = await self._connect_once(session)
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
                await self._serve(ws)
                code = ws.close_code
        except aiohttp.WSServerHandshakeError as e:
            if e.status in (401, 403):
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

    async def _serve(self, ws: aiohttp.ClientWebSocketResponse) -> None:
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
                await ws.send_json(
                    {
                        "op": "hello",
                        "agent_id": str(self.agent_id),
                        "version": self.version,
                        "sig": sig,
                    }
                )
            elif "id" in data:
                await ws.send_json(self._reply(data))

    def _reply(self, request: dict) -> dict:
        code = "paused" if self.paused else "not_implemented"
        return {"id": request["id"], "ok": False, "error": {"code": code, "message": ""}}
