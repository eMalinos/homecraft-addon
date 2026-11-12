"""ws_proxy and http_proxy against a fake HA (aiohttp test server)."""

import asyncio
import base64
import contextlib
import json

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

    async def flow_start(request):
        return web.json_response({"flow_id": "f1", "type": "menu"})

    async def flow_step(request):
        seen.append({"flow": request.method, "id": request.match_info["id"]})
        return web.json_response({"type": "form"})

    async def addon_info(request):  # the Supervisor
        seen.append({"supervisor": request.match_info["slug"]})
        slug = request.match_info["slug"]
        url = "/api/hassio_ingress/tokNR/" if slug == "a0d7b954_nodered" else None
        return web.json_response({"data": {"ingress_url": url}})

    async def ingress(request):
        seen.append(
            {"ingress": request.match_info["rest"], "auth": request.headers.get("Authorization")}
        )
        return web.json_response([])

    app = web.Application()
    app.router.add_get("/api/websocket", websocket)
    app.router.add_post("/api/config/core/check_config", check_config)
    app.router.add_post("/api/config/config_entries/entry/{id}/reload", redirect)
    app.router.add_post("/api/config/config_entries/flow", flow_start)
    app.router.add_route("*", "/api/config/config_entries/flow/{id}", flow_step)
    app.router.add_get("/addons/{slug}/info", addon_info)
    app.router.add_route("*", "/api/hassio_ingress/tokNR/{rest:.*}", ingress)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(settings, "CORE_WS_URL", f"ws://127.0.0.1:{port}/api/websocket")
    monkeypatch.setattr(settings, "CORE_HTTP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings, "HA_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(http_proxy, "_ingress_tokens", {})
    monkeypatch.setattr(http_proxy, "_ingress_refreshed", float("-inf"))
    monkeypatch.setattr(http_proxy, "_ingress_lock", asyncio.Lock())
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


def b64(data: dict) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


def test_http_core_token_redirect_and_refusal(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (seen, _), aiohttp.ClientSession() as http:
            proxy = http_proxy.HttpProxy(http)
            ok = await proxy.handle(
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

            moved = await proxy.handle(
                {"method": "POST", "path": "/api/config/config_entries/entry/e1/reload"},
            )
            assert moved["status"] == 302  # returned, not followed
            assert moved["headers"]["Location"] == "/elsewhere"

            with pytest.raises(OpError) as e:
                await proxy.handle({"method": "GET", "path": "/api/states"})
            assert e.value.code == "not_allowed"
            with pytest.raises(OpError) as e:  # no Supervisor
                await proxy.handle({"method": "GET", "path": "/api/hassio_ingress/tok/flows"})
            assert e.value.code == "unsupported"
            with pytest.raises(OpError) as e:
                await proxy.handle(
                    {"method": "POST", "path": "/api/x", "body_b64": "A" * (13 << 20)}
                )
            assert e.value.code == "too_large"

    asyncio.run(scenario())


def test_http_influxdb_flow_only_own_and_private(monkeypatch):
    flow = "/api/config/config_entries/flow"

    async def scenario():
        async with fake_ha(monkeypatch) as (seen, _), aiohttp.ClientSession() as http:
            proxy = http_proxy.HttpProxy(http)

            async def step(method, body=None, flow_id="f1"):
                req = {"method": method, "path": f"{flow}/{flow_id}"}
                return await proxy.handle({**req, "body_b64": b64(body) if body else None})

            with pytest.raises(OpError):  # a flow this connection did not start
                await step("POST", {"next_step_id": "configure_v1"})
            started = await proxy.handle(
                {"method": "POST", "path": flow, "body_b64": b64({"handler": "influxdb"})}
            )
            assert started["status"] == 200
            assert (await step("POST", {"next_step_id": "configure_v1"}))["status"] == 200
            for url in ("http://a0d7b954-influxdb:8086", "http://10.0.0.5:8086"):
                assert (await step("POST", {"url": url, "database": "h"}))["status"] == 200
            for url in ("http://8.8.8.8:8086", "http://127.0.0.1:8086", "http://no.such.invalid"):
                with pytest.raises(OpError) as e:
                    await step("POST", {"url": url, "database": "h"})
                assert e.value.code == "not_allowed"
            with pytest.raises(OpError):
                await step("POST", {"next_step_id": "configure_v1"}, flow_id="other")
            assert (await step("DELETE"))["status"] == 200
            with pytest.raises(OpError):  # gone after the DELETE
                await step("DELETE")
            assert [s for s in seen if "flow" in s] == [{"flow": "POST", "id": "f1"}] * 3 + [
                {"flow": "DELETE", "id": "f1"}
            ]

    asyncio.run(scenario())


def test_http_ingress_per_addon_routes(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (seen, _), aiohttp.ClientSession() as http:
            monkeypatch.setattr(settings, "SUPERVISOR_URL", settings.HA_URL)
            proxy = http_proxy.HttpProxy(http)
            ok = await proxy.handle(
                {
                    "method": "GET",
                    "path": "/api/hassio_ingress/tokNR/flows",
                    "headers": {"Cookie": "ingress_session=s", "Authorization": "Bearer x"},
                }
            )
            assert ok["status"] == 200
            assert {"ingress": "flows", "auth": None} in seen  # no token to an add-on
            for method, path in [
                ("POST", "/api/hassio_ingress/tokNR/nodes"),  # Node-RED: no module installs
                ("GET", "/api/hassio_ingress/tokNR/settings"),
                ("GET", "/api/hassio_ingress/unknown/flows"),
                ("GET", "/api/hassio_ingress/unknown2/flows"),
            ]:
                with pytest.raises(OpError) as e:
                    await proxy.handle({"method": method, "path": path})
                assert e.value.code == "not_allowed"
            # the unknown tokens asked the Supervisor once (rate limit), not per request
            asked = [s for s in seen if "supervisor" in s]
            assert len(asked) == len(http_proxy.policy.ADDONS)

    asyncio.run(scenario())
