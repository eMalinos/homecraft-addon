"""fingerprint.collect against a fake Supervisor /info and a fake HA get_config."""

import asyncio
import contextlib
import hashlib
import json

import aiohttp
from aiohttp import web

from connector import fingerprint, settings

MACHINE_ID = "0123456789abcdef0123456789abcdef"
HA_UUID = "fedcba9876543210fedcba9876543210"
LONG_NAME = "Domov " + "x" * 80


def fp(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@contextlib.asynccontextmanager
async def fake(monkeypatch, info_status=200, name=LONG_NAME):
    async def info(request):
        assert request.headers["Authorization"] == "Bearer sup"
        return web.json_response({"data": {"machine_id": MACHINE_ID}}, status=info_status)

    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        assert (await ws.receive_json())["access_token"] == "sup"
        await ws.send_json({"type": "auth_ok"})
        cmd = await ws.receive_json()
        assert cmd["type"] == "get_config"
        await ws.send_json({"id": 99, "type": "event"})  # an unrelated message first
        await ws.send_json(
            {"id": cmd["id"], "type": "result", "success": True, "result": {"location_name": name}}
        )
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/info", info)
    app.router.add_get("/api/websocket", websocket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setattr(settings, "SUPERVISOR_URL", f"http://{base}")
    monkeypatch.setattr(settings, "CORE_WS_URL", f"ws://{base}/api/websocket")
    monkeypatch.setattr(settings, "SUPERVISOR_TOKEN", "sup")
    try:
        yield
    finally:
        await runner.cleanup()


def collect(monkeypatch, tmp_path, supervisor=True, **kw):
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)

    async def go():
        async with fake(monkeypatch, **kw), aiohttp.ClientSession() as http:
            if not supervisor:
                monkeypatch.setattr(settings, "SUPERVISOR_URL", "")  # dev
            return await fingerprint.collect(http)

    return asyncio.run(go())


def write_uuid(tmp_path, content):
    (tmp_path / ".storage").mkdir()
    (tmp_path / ".storage/core.uuid").write_text(content)


def test_collects_name_and_fingerprints_only(monkeypatch, tmp_path, caplog):
    write_uuid(tmp_path, json.dumps({"version": 1, "data": {"uuid": HA_UUID}}))
    out = collect(monkeypatch, tmp_path)
    assert out == {"ha_name": LONG_NAME[:64], "machine_fp": fp(MACHINE_ID), "ha_fp": fp(HA_UUID)}
    for text in (json.dumps(out), caplog.text):
        assert MACHINE_ID not in text and HA_UUID not in text


def test_missing_values_are_left_out(monkeypatch, tmp_path):
    # No core.uuid, /info refused (403), an empty name.
    assert collect(monkeypatch, tmp_path, info_status=403, name="") == {}


def test_broken_uuid_and_no_supervisor(monkeypatch, tmp_path):
    write_uuid(tmp_path, "not json")
    assert collect(monkeypatch, tmp_path, supervisor=False) == {"ha_name": LONG_NAME[:64]}


def test_unreachable_ha_is_only_logged(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)
    monkeypatch.setattr(settings, "SUPERVISOR_URL", "")
    monkeypatch.setattr(settings, "CORE_WS_URL", "ws://127.0.0.1:1/api/websocket")

    async def go():
        async with aiohttp.ClientSession() as http:
            return await fingerprint.collect(http)

    assert asyncio.run(go()) == {}
