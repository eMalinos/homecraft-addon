"""The outgoing connection to Homecraft: challenge/hello, request loop, reconnect."""

import asyncio
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import access_token, audit, files, http_proxy, identity, policy, ws_proxy

logger = logging.getLogger(__name__)

CLOSE_UNAUTHORIZED = 4401
# Unpaired, the pairing request waits for the operator in Homecraft. Retry soon,
# so an approval takes effect at once; the backend keeps the request 15 min and
# every connection renews it.
CLOSE_PENDING = 4403
PENDING_DELAY = (10, 30)
# Pending longer than the backend keeps a request: slow down like a refusal.
PENDING_LIFETIME = 15 * 60
STABLE_AFTER = 60.0  # seconds connected before the backoff starts over
MAX_MSG_SIZE = 16 * 1024 * 1024
# What we send: the backend (uvicorn) closes the connection over 16 MiB.
MAX_FRAME = 15 * 1024 * 1024
MAX_CONCURRENT_OPS = 4  # http and file.* at once; ws.* are not limited
# The hub has no "welcome": it checks the hello at once and closes on a bad
# one. Still open this long after the hello (or any request) = accepted.
HELLO_ACCEPTED_AFTER = 3.0

# Not paired (4401) or no valid Access token (403): retrying every minute
# forever would only knock on a closed door, so slow down to once an hour.
REFUSED_DELAYS = (60, 5 * 60, 15 * 60, 60 * 60)
# A staged token is tried on a new connection once nothing runs, but no later
# than this after the push (an HA session can stay open for long).
ROTATE_AFTER = 15 * 60


# How a connection attempt ended; picks the reconnect delay.
OUTAGE, REFUSED, PENDING = "outage", "refused", "pending"


class Backoff:
    """Reconnect delays: an outage 1, 2, 4 … 60 s; a refusal 1, 5, 15, 60 min, then
    hourly; waiting for approval 10 s, then every 30 s, as a refusal after PENDING_LIFETIME."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._outages = self._refusals = self._pending = 0
        self._pending_since: float | None = None

    def next(self, kind: str) -> float:
        if kind == PENDING:
            now = time.monotonic()
            if self._pending_since is None:  # just entered: start at the first delay
                self._pending_since, self._pending = now, 0
            if now - self._pending_since < PENDING_LIFETIME:
                delay = PENDING_DELAY[min(self._pending, len(PENDING_DELAY) - 1)]
                self._pending += 1
                return delay
            kind = REFUSED
        else:
            self._pending_since = None
        if kind == REFUSED:
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
        hello_extra: Callable[[], Awaitable[dict]] | None = None,
    ) -> None:
        self.server_url = server_url
        self.agent_id, self.key, self.version = agent_id, key, version
        self.paused = paused
        # ha_name and fingerprints for a pairing request (fingerprint.Collector):
        # asked before every attempt, it looks up only what is still missing.
        self.hello_extra = hello_extra
        self._extra: dict = {}
        self.backoff = Backoff()
        self._wake = asyncio.Event()
        # For the panel: never a token or a payload in last_error.
        self.connected = False
        self.last_error: str | None = None
        self.last_connected: float | None = None  # unix time hello was accepted
        self.pending_approval = False  # the last attempt ended with CLOSE_PENDING
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._conn: _Conn | None = None
        self._dropping = False
        self._closers: set[asyncio.Task] = set()
        # A connect in flight when reconnect_now came: drop it once it is up.
        self._reconnect_requested = False
        # Fingerprint of the staged token Access refused: logged once, and a
        # repeated push of it does not trigger a reconnect (#565).
        self._refused_fp: str | None = None

    def reconnect_now(self) -> None:
        """Connect again at once (panel: **Připojit znovu**, token saved or deleted):
        skip the current wait, or drop the open connection so the next one uses
        the current token. A handshake still in flight is dropped once it is up."""
        self.backoff.reset()
        self._wake.set()
        self._reconnect_requested = True
        if self._ws is not None:
            self._drop(self._ws)

    def _drop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Close `ws` if it is still the current connection."""
        if ws is not self._ws or ws.closed:
            return
        self._dropping = True
        task = asyncio.get_running_loop().create_task(ws.close())
        self._closers.add(task)
        task.add_done_callback(self._closers.discard)

    async def set_paused(self, paused: bool) -> None:
        """Pausing also closes every open HA session (Homecraft gets ws.closed)."""
        self.paused = paused
        if paused and self._conn is not None:
            try:
                await self._conn.sessions.close_all(notify=True)
            except (ConnectionError, aiohttp.ClientError, RuntimeError):
                pass  # Homecraft is gone too; the sessions are closed either way

    def _accepted(self) -> None:
        if not self.connected:
            self.connected, self.last_connected, self.last_error = True, time.time(), None
            self.pending_approval = False

    async def wait(self, delay: float) -> None:
        """Sleep `delay` (+ up to 10 % jitter) unless `reconnect_now` cuts it short."""
        try:
            await asyncio.wait_for(self._wake.wait(), delay * random.uniform(1, 1.1))
        except TimeoutError:
            pass
        self._wake.clear()

    async def run(self) -> None:
        # Two sessions: Homecraft's has no cookie jar, so a CF_Authorization
        # cookie from Access never stands in for the token we send (#565).
        async with (
            aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as homecraft,
            aiohttp.ClientSession() as ha,
        ):
            while True:
                try:
                    outcome = await self._connect_once(homecraft, ha)
                except Exception:
                    # Never die silently: an unexpected error counts as an outage.
                    logger.exception("Neočekávaná chyba spojení")
                    outcome = OUTAGE
                    self.pending_approval = False
                await self.wait(self.backoff.next(outcome))

    async def _connect_once(
        self, session: aiohttp.ClientSession, ha: aiohttp.ClientSession | None = None
    ) -> str:
        """One connection until it drops: OUTAGE, REFUSED or PENDING.

        A staged token is tried first; when Access refuses it, the active one
        right away in the same attempt (the staged one stays for next time)."""
        self._reconnect_requested = False
        if self.hello_extra is not None:
            self._extra = await self.hello_extra()
        token, staged = access_token.load()
        try:
            outcome = await self._connect_with(session, ha or session, token, staged)
        except _StagedRefused:
            self._staged_refused(token)
            outcome = await self._connect_with(session, ha or session, access_token.active(), False)
        # Only while the backend is seeing the request (it closes with 4403).
        self.pending_approval = outcome == PENDING
        return outcome

    async def _connect_with(
        self,
        session: aiohttp.ClientSession,
        ha: aiohttp.ClientSession,
        token: access_token.Token | None,
        staged: bool,
    ) -> str:
        headers = (
            {"CF-Access-Client-Id": token[0], "CF-Access-Client-Secret": token[1]} if token else {}
        )
        started = None
        last_connected = self.last_connected
        self._dropping = False
        try:
            async with session.ws_connect(
                self.server_url, headers=headers, heartbeat=30, max_msg_size=MAX_MSG_SIZE
            ) as ws:
                started = time.monotonic()
                self._ws = ws
                if staged and not await self._commit_token(token):
                    self._reconnect_requested = True  # its token is no longer on disk
                if self._reconnect_requested:  # the panel changed the token meanwhile
                    self._drop(ws)
                else:
                    logger.info("Připojeno k %s", self.server_url)
                    fp = access_token.fingerprint(token) if token else None
                    await self._serve(ws, ha, fp)
                code = ws.close_code
        except aiohttp.WSServerHandshakeError as e:
            if access_refused(e):
                if staged:
                    raise _StagedRefused() from None
                return self._failed(
                    REFUSED, "Chybí nebo neplatí přístupový token — zadej ho v panelu add-onu"
                )
            return self._failed(OUTAGE, f"Homecraft odmítl spojení (HTTP {e.status})")
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            return self._failed(OUTAGE, f"Homecraft nedostupný: {type(e).__name__}")
        finally:
            self.connected, self._ws = False, None
            if started is not None and time.monotonic() - started >= STABLE_AFTER:
                self.backoff.reset()
        if code == CLOSE_PENDING:
            # A slow close may have counted as accepted meanwhile: it was not.
            self.last_connected = last_connected
            if not self.pending_approval:  # logged once, not on every retry
                logger.info("Čeká na schválení v Homecraftu — porovnej ověřovací kód")
            self.last_error = "Čeká na schválení v Homecraftu — porovnej ověřovací kód"
            return PENDING
        if code == CLOSE_UNAUTHORIZED:
            return self._failed(REFUSED, "Agent není spárovaný — vlož párovací kód v Homecraftu")
        if self._dropping:
            logger.info("Spojení s Homecraftem ukončeno, připojuji se znovu")
            return OUTAGE
        logger.info("Spojení s Homecraftem skončilo (kód %s)", code)
        self.last_error = f"Spojení skončilo (kód {code})"
        return OUTAGE

    async def _commit_token(self, token: access_token.Token) -> bool:
        """Access let the staged `token` through: it becomes the active one,
        unless the panel or a newer push replaced it meanwhile (then False)."""
        if not await asyncio.to_thread(access_token.commit, token):
            return False
        self._refused_fp = None
        logger.info("Přístupový token aktualizován Homecraftem")
        audit.record("access_token.commit", "", True)
        return True

    def _staged_refused(self, token: access_token.Token) -> None:
        """Logged and audited once per staged token; it stays staged."""
        fp = access_token.fingerprint(token)
        if fp != self._refused_fp:
            self._refused_fp = fp
            logger.warning("Nový token od Homecraftu odmítnut Cloudflare, používám původní")
            audit.record("access_token.commit", "", False, "refused")

    async def _set_token(self, rid: object, request: dict, conn: "_Conn") -> dict:
        """`access_token.set` from Homecraft: staged, never echoed or logged.
        The connection is replaced once idle (`_Conn.rotate`), at the latest
        after ROTATE_AFTER, never in the middle of a request."""
        token = request.get("token")
        try:
            if not isinstance(token, str):
                raise ValueError
            parsed = access_token.parse(token)
            changed = await asyncio.to_thread(access_token.stage, token)
        except ValueError:
            return _error(rid, "bad_request", "expected <client_id>:<client_secret>")
        except OSError as e:
            return _error(rid, "internal", type(e).__name__)
        fp = access_token.fingerprint(parsed)
        if changed and fp == self._refused_fp:
            self._refused_fp = None  # staged anew (e.g. after a panel save): log again
        # A token this connection already fell back from: wait for the next one
        # (it tries the staged token first), do not reconnect over and over.
        if fp != self._refused_fp and access_token.pending():
            conn.request_rotation()
        return {"id": rid, "ok": True, "result": None}

    def _failed(self, outcome: str, message: str) -> str:
        logger.warning("%s", message)
        self.last_error = message
        return outcome

    async def _serve(
        self, ws: aiohttp.ClientWebSocketResponse, http: aiohttp.ClientSession, fp: str | None
    ) -> None:
        lock = asyncio.Lock()

        async def send(data: dict) -> None:
            text = json.dumps(data)  # ASCII: len == bytes
            if len(text) > MAX_FRAME:
                smaller = _oversized(data)
                if smaller is None:
                    logger.warning("Zpráva přes limit rámce zahozena")
                    return
                text = json.dumps(smaller)
            async with lock:
                await ws.send_str(text)

        loop = asyncio.get_running_loop()
        tasks: set[asyncio.Task] = set()
        rotate = False
        deadline: asyncio.TimerHandle | None = None

        def maybe_rotate(_: object = None) -> None:
            """A staged token: reconnect once nothing runs on this connection.
            Checked when a request or an HA session ends; tied to `ws`, so a
            late callback never drops a newer connection."""
            if rotate and not tasks and sessions.idle:
                self._drop(ws)

        def request_rotation() -> None:
            nonlocal rotate, deadline
            rotate = True
            if deadline is None:
                deadline = loop.call_later(ROTATE_AFTER, self._drop, ws)
            maybe_rotate()

        conn = _Conn(
            ws_proxy.Sessions(http, send, lambda: self.paused, on_end=maybe_rotate),
            http_proxy.HttpProxy(http),
            request_rotation,
        )
        self._conn = conn
        sessions = conn.sessions
        accept: asyncio.TimerHandle | None = None  # started once the hello is out

        async def respond(request: dict) -> None:
            reply = await self._reply(request, conn)
            await send(reply)
            msg = request.get("msg")
            if request.get("op") == "ws.send" and not reply["ok"] and isinstance(msg, dict):
                if "id" in msg:  # AgentSocket.recv waits for this id, not for our reply
                    await send(
                        {
                            "op": "ws.msg",
                            "session": request.get("session"),
                            "msg": ws_proxy.ha_error(msg["id"], **reply["error"]),
                        }
                    )

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
                            "public_key": identity.public_b64(self.key),
                            **self._extra,
                            **({"access_token_fp": fp} if fp else {}),
                        }
                    )
                    if accept is None:
                        accept = loop.call_later(HELLO_ACCEPTED_AFTER, self._accepted)
                elif "id" in data:
                    self._accepted()  # requests come only after an accepted hello
                    if data.get("op") in ("ws.send", "ws.close", "access_token.set"):
                        await respond(data)  # inline: a session keeps its message order
                        maybe_rotate()
                    else:
                        task = asyncio.create_task(respond(data))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                        task.add_done_callback(maybe_rotate)
        finally:
            if accept is not None:
                accept.cancel()
            if deadline is not None:
                deadline.cancel()
            self._conn = None
            for task in list(tasks):
                task.cancel()
            await sessions.close_all()

    async def _reply(self, request: dict, conn: "_Conn") -> dict:
        started = time.monotonic()
        reply = await self._handle(request, conn)
        refused = reply.pop("_refused", None)  # ws.send answered as HA would: its code
        error = reply.get("error")
        audit.record(
            str(request.get("op")),
            _audit_target(request),
            error is None and not refused,
            refused or error and error["code"],
            (time.monotonic() - started) * 1000,
        )
        return reply

    async def _handle(self, request: dict, conn: "_Conn") -> dict:
        rid, op = request["id"], request.get("op")
        # Before pause and the allowlist: it touches only the connection to
        # Homecraft, never HA, and a paused home must not lose that at rotation.
        if op == "access_token.set":
            return await self._set_token(rid, request, conn)
        if self.paused and op != "ws.close":  # Homecraft may still close its sessions
            return _error(rid, "paused", "")
        try:
            result = await _dispatch(op, request, conn)
        except policy.OpError as e:
            if e.code in ("not_allowed", "refused_content"):
                logger.info("Odmítnuto: %s", e.message)
            return _error(rid, e.code, e.message)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            return _error(rid, "bad_request", f"{op}: {type(e).__name__}")
        except Exception as e:
            # The type only: messages can carry paths, payloads or HA's answers.
            logger.warning("Operace %s selhala: %s", op, type(e).__name__)
            return _error(rid, "internal", type(e).__name__)
        if isinstance(result, _Refused):
            return {"id": rid, "ok": True, "result": None, "_refused": result.code}
        return {"id": rid, "ok": True, "result": result}


class _StagedRefused(Exception):
    """Access refused the staged token: try the active one."""


@dataclass
class _Conn:
    """Per Homecraft connection: HA sessions, HTTP state, and a cap on the
    http/file operations running at once."""

    sessions: ws_proxy.Sessions
    http: http_proxy.HttpProxy
    request_rotation: Callable[[], None]  # a token was staged (#565)
    ops: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_OPS))


FILE_OPS = {
    "file.read": lambda r: (files.read, r["path"]),
    "file.write": lambda r: (files.write, r["path"], r["text"]),
    "file.delete": lambda r: (files.delete, r["path"]),
    "file.list": lambda r: (files.list_dir, r["path"]),
}


@dataclass
class _Refused:
    """ws.send refused (the HA-shaped error is already pushed): not_allowed or refused_content."""

    code: str


_INGRESS_TOKEN_RE = re.compile(r"^/api/hassio_ingress/[^/]+")


def _audit_target(req: dict) -> str:
    """What an operation touched, without its content (and without ingress tokens)."""
    op = req.get("op")
    if op == "ws.send":
        msg = req.get("msg")
        if not isinstance(msg, dict):
            return ""
        kind = str(msg.get("type"))
        if kind == "call_service":
            return f"{kind} {msg.get('domain')}.{msg.get('service')}"
        if kind == "supervisor/api":
            return f"{kind} {msg.get('method', 'get')} {msg.get('endpoint')}"
        return kind
    if op == "http":
        path = _INGRESS_TOKEN_RE.sub("/api/hassio_ingress/…", str(req.get("path", "")))
        return f"{req.get('method')} {path.split('?', 1)[0]}"
    if op in FILE_OPS:
        return str(req.get("path"))
    return ""


async def _dispatch(op: object, req: dict, conn: _Conn) -> object:
    if op == "ws.open":
        return await conn.sessions.open()
    if op == "ws.send":
        refused = await conn.sessions.send(req["session"], req["msg"])
        return _Refused(refused) if refused else None
    if op == "ws.close":
        return await conn.sessions.close(req["session"])
    if op == "http":
        async with conn.ops:
            return await conn.http.handle(req)
    if op in FILE_OPS:
        call = FILE_OPS[op](req)
        async with conn.ops:
            return await asyncio.to_thread(*call)
    raise policy.OpError("unknown_op", str(op)[:64])


def _error(rid: object, code: str, message: str) -> dict:
    return {"id": rid, "ok": False, "error": {"code": code, "message": message}}


def _oversized(data: dict) -> dict | None:
    """What goes out instead of a frame over MAX_FRAME: a reply becomes a
    `too_large` error, a ws.msg result an HA-shaped one; anything else is dropped."""
    message = "over the 16 MiB frame limit"
    if "ok" in data and "id" in data:
        return _error(data["id"], "too_large", message)
    msg = data.get("msg")
    if data.get("op") == "ws.msg" and isinstance(msg, dict) and "id" in msg:
        return {**data, "msg": ws_proxy.ha_error(msg["id"], "too_large", message)}
    return None
