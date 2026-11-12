"""The `http` operation: core REST with the agent's token, add-on ingress without one."""

import asyncio
import base64
import ipaddress
import json
import logging
import socket
import time

import aiohttp
from yarl import URL

from . import content, policy, settings

logger = logging.getLogger(__name__)

# Base64 makes 12 MiB of 9 MiB: with headers and the envelope well under the
# 16 MiB frame (client.send refuses anything bigger anyway).
MAX_BODY = 9 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=300)
CORE_HEADERS = frozenset({"content-type", "accept"})
INGRESS_HEADERS = CORE_HEADERS | {
    "cookie",
    "node-red-api-version",
    "node-red-deployment-type",
    "x-requested-with",  # Chronograf's CSRF check (influxdb_admin)
}
# aiohttp has already decoded and de-chunked the body it hands back.
DROPPED_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})
INGRESS_REFRESH_EVERY = 30.0  # seconds; an unknown token asks the Supervisor at most this often

# slug -> ingress token, from the Supervisor's /addons/<slug>/info
_ingress_tokens: dict[str, str] = {}
_ingress_refreshed = float("-inf")
_ingress_lock = asyncio.Lock()


async def _refresh_ingress(session: aiohttp.ClientSession) -> None:
    for slug in policy.ADDONS:
        try:
            async with session.get(
                f"{settings.SUPERVISOR_URL}/addons/{slug}/info",
                headers={"Authorization": f"Bearer {settings.SUPERVISOR_TOKEN}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                info = (await resp.json()).get("data") or {}
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError, AttributeError):
            continue  # not installed, or the Supervisor is busy: not ours to reach
        url = info.get("ingress_url") if isinstance(info, dict) else None
        if isinstance(url, str) and (m := policy.INGRESS_RE.fullmatch(url)):
            _ingress_tokens[slug] = m.group(1)


async def _ingress_slug(session: aiohttp.ClientSession, token: str) -> str | None:
    global _ingress_refreshed
    if not settings.SUPERVISOR_URL:
        raise policy.OpError("unsupported", "no Supervisor, no ingress")
    async with _ingress_lock:
        known = {t: s for s, t in _ingress_tokens.items()}
        if token not in known and time.monotonic() - _ingress_refreshed >= INGRESS_REFRESH_EVERY:
            _ingress_refreshed = time.monotonic()
            await _refresh_ingress(session)  # a restarted add-on gets a new token
            known = {t: s for s, t in _ingress_tokens.items()}
    return known.get(token)


async def _host_allowed(host: str) -> bool:
    """The InfluxDB add-on, or a host that resolves to private addresses only
    (the project's InfluxDB without a Supervisor), like influxdb_admin._integration_url."""
    if host == policy.INFLUXDB_ADDON_HOST:
        return True
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addrs = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}
    return bool(addrs) and all(a.is_private and not a.is_loopback for a in addrs)


class HttpProxy:
    """The `http` operation for one Homecraft connection."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._flows: set[str] = set()  # config flows this connection started (influxdb)

    async def handle(self, req: dict) -> dict:
        method, path = req["method"], req["path"]
        if not isinstance(method, str):
            raise TypeError("method must be a string")
        method = method.upper()
        body = base64.b64decode(req["body_b64"]) if req.get("body_b64") is not None else None
        if body is not None and len(body) > MAX_BODY:
            raise policy.OpError("too_large", "request body is too large")
        refused = policy.not_allowed(f"{method} {str(path).split('?', 1)[0]}")
        target = policy.http_target(method, path, body)
        if target is None:
            raise refused
        kind, ref, subpath = target
        if kind == "ingress":
            slug = await _ingress_slug(self._session, ref)
            if slug is None or not policy.ingress_allowed(slug, method, subpath):
                raise refused
            if method == "POST":
                await self._check_ingress(slug, subpath, path, req.get("headers"), body)
        elif kind == "flow":
            if ref not in self._flows:
                raise refused
            if method == "POST":
                host = policy.flow_step_host(body)
                if host is False or (host is not None and not await _host_allowed(host)):
                    raise refused
        elif kind == "core" and method == "POST":
            route = path.split("?", 1)[0]
            if policy.CONFIG_RE.fullmatch(route):
                await self._check_config(route, body)
        result = await self._forward(kind, method, path, req.get("headers"), body)
        if kind == "core" and path.split("?", 1)[0] == policy.FLOW and result["status"] == 200:
            self._track_flow(result)
        elif kind == "flow" and method == "DELETE":
            self._flows.discard(ref)
        return result

    async def _check_config(self, route: str, body: bytes | None) -> None:
        """An automation/script config: only forbidden services it brings in
        that the live config of the same id lacks (a rename fix of the user's
        own automation passes). The GET only when the body has some at all."""
        if not await asyncio.to_thread(content.check_automation_body, body):
            return
        live = await self._forward("core", "GET", route, None, None)
        if live["status"] == 200:
            try:
                current = json.loads(base64.b64decode(live["body_b64"]))
            except ValueError:
                current = None
        elif live["status"] == 404:  # a new automation: checked whole
            current = None
        else:
            raise policy.OpError("ha_unreachable", f"GET {route}: HTTP {live['status']}")
        if what := await asyncio.to_thread(content.check_automation_body, body, current):
            raise content.refused(route, what)

    async def _check_ingress(
        self, slug: str, subpath: str, path: str, headers: object, body: bytes | None
    ) -> None:
        """Node-RED flows and the go2rtc config are written whole: the content
        check compares the body with what is live right now (a GET first)."""
        if (slug, subpath) not in ((content.NODE_RED, "/flows"), (content.GO2RTC, "/api/config")):
            return
        live = await self._forward("ingress", "GET", path.split("?", 1)[0], headers, None)
        status, text = live["status"], base64.b64decode(live["body_b64"]).decode(errors="replace")
        if status != 200 and not (slug == content.GO2RTC and status == 404):  # 404: no file
            raise policy.OpError("ha_unreachable", f"GET {subpath}: HTTP {status}")
        if slug == content.NODE_RED:
            try:
                current = json.loads(text)
            except ValueError:
                current = None  # nothing to compare with: every node is checked
            what = await asyncio.to_thread(content.check_nodered_flows, body, current)
        else:
            current = text if status == 200 else ""
            what = await asyncio.to_thread(content.check_go2rtc_config, body, current)
        if what:
            raise content.refused(f"{slug}{subpath}", what)

    def _track_flow(self, result: dict) -> None:
        try:
            flow_id = json.loads(base64.b64decode(result["body_b64"])).get("flow_id")
        except (ValueError, AttributeError):
            return
        if isinstance(flow_id, str):
            self._flows.add(flow_id)

    async def _forward(
        self, kind: str, method: str, path: str, in_headers: object, body: bytes | None
    ) -> dict:
        allowed = INGRESS_HEADERS if kind == "ingress" else CORE_HEADERS
        headers = {
            k: v
            for k, v in (in_headers if isinstance(in_headers, dict) else {}).items()
            if isinstance(k, str) and isinstance(v, str) and k.lower() in allowed
        }
        if kind == "ingress":
            url = settings.HA_URL + path
        else:
            url = settings.CORE_HTTP_URL + path
            headers["Authorization"] = f"Bearer {settings.SUPERVISOR_TOKEN}"
        try:
            async with self._session.request(
                method,
                URL(url, encoded=True),
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=TIMEOUT,
            ) as resp:
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > MAX_BODY:
                        raise policy.OpError("too_large", "response body is too large")
                return {
                    "status": resp.status,
                    "headers": {
                        k: v for k, v in resp.headers.items() if k.lower() not in DROPPED_HEADERS
                    },
                    "body_b64": base64.b64encode(data).decode(),
                }
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            raise policy.OpError("ha_unreachable", type(e).__name__) from None
