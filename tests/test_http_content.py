"""http_proxy runs the content check before it forwards (#557), against a fake
HA with Node-RED and go2rtc behind ingress."""

import asyncio
import base64
import contextlib
import json

import aiohttp
import pytest
from aiohttp import web

from connector import http_proxy, settings
from connector.policy import OpError

USER_FLOWS = [
    {"id": "u_tab", "type": "tab"},
    {"id": "u_exec", "type": "exec", "z": "u_tab", "command": "ls"},
]
GO2RTC_YAML = "streams:\n  cam: exec:ffmpeg -i x\nwebrtc:\n  listen: ':8555'\n"
TOKENS = {"a0d7b954_nodered": "tokNR", "a889bffc_go2rtc": "tokG"}


@contextlib.asynccontextmanager
async def fake_ha(monkeypatch, go2rtc_status=200):
    posted: list[tuple[str, bytes]] = []

    async def addon_info(request):
        token = TOKENS.get(request.match_info["slug"])
        url = f"/api/hassio_ingress/{token}/" if token else None
        return web.json_response({"data": {"ingress_url": url}})

    async def nodered(request):
        if request.method == "GET":
            return web.json_response({"rev": "r1", "flows": USER_FLOWS})
        posted.append((request.path, await request.read()))
        return web.json_response({"rev": "r2"})

    async def go2rtc(request):
        if request.method == "GET":
            return web.Response(text=GO2RTC_YAML, status=go2rtc_status)
        posted.append((request.path, await request.read()))
        return web.Response()

    async def automation(request):
        if request.method == "GET":  # a1 is new, user1 the user's own
            if request.match_info["id"] != "user1":
                return web.json_response({"message": "Resource not found"}, status=404)
            return web.json_response(USER_AUTOMATION)
        posted.append((request.path, await request.read()))
        return web.json_response({"result": "ok"})

    app = web.Application()
    app.router.add_get("/addons/{slug}/info", addon_info)
    app.router.add_route("*", "/api/hassio_ingress/tokNR/flows", nodered)
    app.router.add_route("*", "/api/hassio_ingress/tokG/api/config", go2rtc)
    app.router.add_route("*", "/api/config/automation/config/{id}", automation)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    for name in ("CORE_HTTP_URL", "HA_URL", "SUPERVISOR_URL"):
        monkeypatch.setattr(settings, name, url)
    monkeypatch.setattr(settings, "SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(http_proxy, "_ingress_tokens", {})
    monkeypatch.setattr(http_proxy, "_ingress_refreshed", float("-inf"))
    monkeypatch.setattr(http_proxy, "_ingress_lock", asyncio.Lock())
    try:
        async with aiohttp.ClientSession() as http:
            yield http_proxy.HttpProxy(http), posted
    finally:
        await runner.cleanup()


def post(path: str, body: bytes) -> dict:
    return {"method": "POST", "path": path, "body_b64": base64.b64encode(body).decode()}


def flows(nodes: list) -> bytes:
    return json.dumps({"rev": "r1", "flows": nodes}).encode()


async def refused(proxy, req) -> str:
    with pytest.raises(OpError) as e:
        await proxy.handle(req)
    assert e.value.code == "refused_content"
    return e.value.message


def test_nodered_flows(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (proxy, posted):
            path = "/api/hassio_ingress/tokNR/flows"
            ours = [{"id": "hfb_t", "type": "tab"}, {"id": "hfb_t_f", "type": "function"}]
            ok = await proxy.handle(post(path, flows(USER_FLOWS + ours)))
            assert ok["status"] == 200 and len(posted) == 1  # the user's exec node is theirs
            evil = [{"id": "hfb_t_x", "type": "exec", "command": "id"}]
            message = await refused(proxy, post(path, flows(USER_FLOWS + evil)))
            assert message == (
                "a0d7b954_nodered/flows: uzel exec není přes Homecraft Connector povolený"
            )
            changed = [USER_FLOWS[0], {**USER_FLOWS[1], "command": "curl x"}]
            await refused(proxy, post(path, flows(changed)))
            assert len(posted) == 1  # nothing refused reached Node-RED

    asyncio.run(scenario())


def test_go2rtc_config(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (proxy, posted):
            path = "/api/hassio_ingress/tokG/api/config"
            new = GO2RTC_YAML.replace("':8555'", "':8556'").encode()
            assert (await proxy.handle(post(path, new)))["status"] == 200
            await refused(proxy, post(path, GO2RTC_YAML.replace("exec:ffmpeg", "exec:sh").encode()))
            assert [p for p, _ in posted] == ["/api/hassio_ingress/tokG/api/config"]

    asyncio.run(scenario())


def test_go2rtc_without_a_config_file(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch, go2rtc_status=404) as (proxy, _):
            path = "/api/hassio_ingress/tokG/api/config"
            assert (await proxy.handle(post(path, b"webrtc: {}\n")))["status"] == 200
            await refused(proxy, post(path, b"streams: {x: 'echo:id'}\n"))

    asyncio.run(scenario())


def test_go2rtc_unreadable_config_is_not_written(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch, go2rtc_status=500) as (proxy, posted):
            with pytest.raises(OpError) as e:
                await proxy.handle(post("/api/hassio_ingress/tokG/api/config", b"webrtc: {}\n"))
            assert e.value.code == "ha_unreachable" and not posted

    asyncio.run(scenario())


def test_automation_config(monkeypatch):
    async def scenario():
        async with fake_ha(monkeypatch) as (proxy, posted):
            path = "/api/config/automation/config/a1"
            ok = json.dumps({"actions": [{"action": "light.turn_on"}]}).encode()
            assert (await proxy.handle(post(path, ok)))["status"] == 200
            bad = json.dumps({"actions": [{"action": "shell_command.x"}]}).encode()
            message = await refused(proxy, post(path + "?x=1", bad))
            assert message.startswith(f"{path}: action: shell_command.x")
            assert len(posted) == 1
            # the user's own automation, renamed entity: its hassio action was there
            user = "/api/config/automation/config/user1"
            renamed = json.dumps({**USER_AUTOMATION, "triggers": [{"entity_id": "b.new"}]})
            assert (await proxy.handle(post(user, renamed.encode())))["status"] == 200
            more = {**USER_AUTOMATION, "actions": [*USER_AUTOMATION["actions"], *bad_actions]}
            await refused(proxy, post(user, json.dumps(more).encode()))
            assert len(posted) == 2

    asyncio.run(scenario())


USER_AUTOMATION = {
    "triggers": [{"entity_id": "b.old"}],
    "actions": [{"action": "hassio.addon_restart", "data": {"addon": "x"}}],
}
bad_actions = [{"action": "shell_command.x"}]
