"""The add-on panel behind HA ingress (#556): state, pairing code, Access token,
pause and the audit.

Only the Supervisor's ingress proxy may talk to it (HA add-on docs: allow
172.30.32.2, deny everyone else); CONNECTOR_DEV=1 drops that check for the
dev run without a Supervisor. The Access token is write-only: no response
carries it, `GET /api/status` says only `token_set`."""

import logging
from pathlib import Path

from aiohttp import web

from . import access_token, audit, settings, state
from .client import Client

logger = logging.getLogger(__name__)

INGRESS_PROXY = "172.30.32.2"
PORT = 8099
# State-changing requests must carry it: a cross-site form cannot set a custom
# header, and a cross-site fetch with one needs a CORS preflight we never answer.
CSRF_HEADER = "X-Homecraft-Panel"
PAGE = (Path(__file__).parent / "panel.html").read_text(encoding="utf-8")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Inline script and style only; HA shows the panel in its own iframe.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
        "form-action 'none'"
    ),
}

CLIENT = web.AppKey("client", Client)
PAIRING_CODE = web.AppKey("pairing_code", str)


@web.middleware
async def _guard(request: web.Request, handler):
    if not settings.DEV and request.remote != INGRESS_PROXY:
        raise web.HTTPForbidden()
    if request.method not in ("GET", "HEAD") and (
        request.headers.get(CSRF_HEADER) != "1"
        or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"
    ):
        raise web.HTTPForbidden()
    response = await handler(request)
    response.headers.update(SECURITY_HEADERS)
    return response


async def _page(request: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html")


async def _status(request: web.Request) -> web.Response:
    client = request.app[CLIENT]
    return web.json_response(
        {
            "connected": client.connected,
            "server_url": client.server_url,
            "version": client.version,
            "last_error": client.last_error,
            "last_connected": client.last_connected,
            "pairing_code": request.app[PAIRING_CODE],
            "token_set": access_token.load() is not None,
            "paused": client.paused,
        }
    )


async def _audit(request: web.Request) -> web.Response:
    return web.json_response(audit.recent())


async def _json_field(request: web.Request, name: str, kind: type) -> object:
    try:
        value = (await request.json())[name]
    except (ValueError, KeyError, TypeError):
        raise web.HTTPBadRequest(text=f"expected JSON with {name}") from None
    if not isinstance(value, kind):
        raise web.HTTPBadRequest(text=f"{name} must be {kind.__name__}")
    return value


async def _save_token(request: web.Request) -> web.Response:
    token = await _json_field(request, "token", str)
    try:
        access_token.save(token)
    except ValueError:
        # Never echo the input: it is (part of) the secret.
        return web.json_response(
            {"ok": False, "error": "Token má tvar <client_id>:<client_secret>"}, status=400
        )
    logger.info("Přístupový token uložen v panelu")
    request.app[CLIENT].reconnect_now()
    return web.json_response({"ok": True})


async def _clear_token(request: web.Request) -> web.Response:
    access_token.clear()
    logger.info("Přístupový token smazán v panelu")
    return web.json_response({"ok": True})


async def _pause(request: web.Request) -> web.Response:
    paused = await _json_field(request, "paused", bool)
    state.save_paused(paused)
    request.app[CLIENT].paused = paused
    logger.info("Homecraft %s v panelu", "pozastaven" if paused else "znovu povolen")
    return web.json_response({"ok": True, "paused": paused})


async def _reconnect(request: web.Request) -> web.Response:
    request.app[CLIENT].reconnect_now()
    return web.json_response({"ok": True})


def make_app(client: Client, pairing_code: str) -> web.Application:
    app = web.Application(middlewares=[_guard], client_max_size=16 * 1024)
    app[CLIENT] = client
    app[PAIRING_CODE] = pairing_code
    app.router.add_get("/", _page)
    app.router.add_get("/api/status", _status)
    app.router.add_get("/api/audit", _audit)
    app.router.add_post("/api/access-token", _save_token)
    app.router.add_delete("/api/access-token", _clear_token)
    app.router.add_post("/api/pause", _pause)
    app.router.add_post("/api/reconnect", _reconnect)
    return app


async def start(client: Client, pairing_code: str) -> web.AppRunner:
    # All interfaces: the ingress proxy reaches the add-on on the hassio network
    # (no host port is published); _guard turns away anyone but the proxy.
    runner = web.AppRunner(make_app(client, pairing_code), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    return runner
