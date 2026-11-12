"""The `http` operation: core REST with the agent's token, add-on ingress without one."""

import base64
import logging

import aiohttp
from yarl import URL

from . import policy, settings

logger = logging.getLogger(__name__)

# A reply is base64 in a JSON frame of at most 16 MiB.
MAX_BODY = 12 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=300)
CORE_HEADERS = frozenset({"content-type", "accept"})
INGRESS_HEADERS = CORE_HEADERS | {"cookie", "node-red-api-version", "node-red-deployment-type"}
# aiohttp has already decoded and de-chunked the body it hands back.
DROPPED_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})

# slug -> ingress token, from the Supervisor's /addons/<slug>/info
_ingress_tokens: dict[str, str] = {}


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


async def _ingress_allowed(session: aiohttp.ClientSession, token: str) -> bool:
    if not settings.SUPERVISOR_URL:
        raise policy.OpError("unsupported", "no Supervisor, no ingress")
    if token not in _ingress_tokens.values():
        await _refresh_ingress(session)  # a restarted add-on gets a new token
    return token in _ingress_tokens.values()


async def handle(session: aiohttp.ClientSession, req: dict) -> dict:
    method, path = req["method"], req["path"]
    if not isinstance(method, str):
        raise TypeError("method must be a string")
    method = method.upper()
    body = base64.b64decode(req["body_b64"]) if req.get("body_b64") is not None else None
    if body is not None and len(body) > MAX_BODY:
        raise policy.OpError("too_large", "request body is too large")
    target = policy.http_target(method, path, body)
    if target is None or (
        target[0] == "ingress" and not await _ingress_allowed(session, target[1])
    ):
        raise policy.not_allowed(f"{method} {str(path).split('?', 1)[0]}")
    kind = target[0]
    allowed = CORE_HEADERS if kind == "core" else INGRESS_HEADERS
    headers = {
        k: v
        for k, v in (req.get("headers") or {}).items()
        if isinstance(k, str) and isinstance(v, str) and k.lower() in allowed
    }
    if kind == "core":
        url = settings.CORE_HTTP_URL + path
        headers["Authorization"] = f"Bearer {settings.SUPERVISOR_TOKEN}"
    else:
        url = settings.HA_URL + path
    try:
        async with session.request(
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
