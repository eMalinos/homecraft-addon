"""ws_proxy and http_proxy against a fake HA (aiohttp test server)."""

import asyncio
import base64
import contextlib

import aiohttp
import pytest
from aiohttp import web

from connector import http_proxy, settings, ws_proxy
from connector.policy import OpError


@contextlib.asynccontextmanager
async def fake_ha(monkeypatch, token="t0k"):
    seen: list[dict] = []
    sockets: list[web.WebSocketResponse] = []

    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sockets.append(ws)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != token:
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        async for msg in ws:
            cmd = msg.json()
            seen.append(cmd)
            if cmd["type"] == "close_me":  # never sent: refused by the allowlist
                break
            await ws.send_json({"id": cmd["id"], "type": "result", "success": True, "result": 1})
        await ws.close()
        return ws

    async def check_config(request):
        seen.append({"auth": request.headers.get("Authorization"), "x": request.headers.get("X")})
        return web.json_response({"result": "valid"})

    async def redirect(request):
        raise web.HTTPFound("/elsewhere")

    app = web.Application()
    app.router.add_get("/api/websocket", websocket)
    app.router.add_post("/api/config/core/check_config", check_config)
    app.router.add_post("/api/config/config_entries/entry/{id}/reload", redirect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(settings, "CORE_WS_URL", f"ws://127.0.0.1:{port}/api/websocket")
    monkeypatch.setattr(settings, "CORE_HTTP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings, "SUPERVISOR_TOKEN", token)
    monkeypatch.setattr(settings, "SUPERVISOR_URL", "")
    try:
        yield seen, sockets
    finally:
        await runner.cleanup()


async def _until(pushed, n):
    for _ in range(200):
        if len(pushed) >= n:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {n} pushes, got {pushed}")


def test_session_forwards_refuses_and_reports_close(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (seen, sockets), aiohttp.ClientSession() as http:
            pushed: list[dict] = []

            async def push(data):
                pushed.append(data)

            sessions = ws_proxy.Sessions(http, push)
            sid = (await sessions.open())["session"]
            await sessions.send(sid, {"id": 1, "type": "get_states"})
            await sessions.send(
                sid, {"id": 2, "type": "call_service", "domain": "shell_command", "service": "x"}
            )
            await _until(pushed, 2)
            replies = {p["msg"]["id"]: p["msg"] for p in pushed}
            assert replies[1]["success"] is True
            assert replies[2]["error"]["code"] == "not_allowed"
            assert [c["type"] for c in seen] == ["get_states"]  # HA never saw the refused one

            await sockets[0].close()  # HA restarts
            await _until(pushed, 3)
            assert pushed[-1] == {"op": "ws.closed", "session": sid}
            with pytest.raises(OpError):
                await sessions.send(sid, {"id": 3, "type": "get_states"})

            sid2 = (await sessions.open())["session"]
            await sessions.close_all()
            with pytest.raises(OpError):
                await sessions.send(sid2, {"id": 1, "type": "get_states"})

    asyncio.run(scenario())


def test_session_limit_and_bad_token(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as _, aiohttp.ClientSession() as http:

            async def push(data):
                pass

            sessions = ws_proxy.Sessions(http, push)
            for _ in range(ws_proxy.MAX_SESSIONS):
                await sessions.open()
            with pytest.raises(OpError) as e:
                await sessions.open()
            assert e.value.code == "too_many_sessions"
            await sessions.close_all()

            monkeypatch.setattr(settings, "SUPERVISOR_TOKEN", "wrong")
            with pytest.raises(OpError) as e:
                await sessions.open()
            assert e.value.code == "auth_failed"

    asyncio.run(scenario())


def test_http_core_token_redirect_and_refusal(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (seen, _), aiohttp.ClientSession() as http:
            ok = await http_proxy.handle(
                http,
                {
                    "method": "POST",
                    "path": "/api/config/core/check_config",
                    "headers": {"Authorization": "Bearer homecraft", "X": "y"},
                    "body_b64": None,
                },
            )
            assert ok["status"] == 200
            assert base64.b64decode(ok["body_b64"]) == b'{"result": "valid"}'
            assert seen == [{"auth": "Bearer t0k", "x": None}]  # own token, nothing else

            moved = await http_proxy.handle(
                http,
                {"method": "POST", "path": "/api/config/config_entries/entry/e1/reload"},
            )
            assert moved["status"] == 302  # returned, not followed
            assert moved["headers"]["Location"] == "/elsewhere"

            for path in ("/api/states", "/api/hassio_ingress/tok/x"):
                with pytest.raises(OpError) as e:
                    await http_proxy.handle(http, {"method": "GET", "path": path})
                assert e.value.code in ("not_allowed", "unsupported")

    asyncio.run(scenario())
