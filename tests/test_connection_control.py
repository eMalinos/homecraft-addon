"""Pause, reconnect and the connection state against a scripted fake Homecraft."""

import asyncio
import uuid

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_ws_proxy import fake_ha

from connector import client
from connector.client import Client


async def against(script, agent=None):
    """Run one agent connection against `script(ws, agent)`; returns (refused, agent)."""
    agent = agent or Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t")

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": "challenge", "nonce": "AAAA"})
        assert (await ws.receive_json())["op"] == "hello"
        await script(ws, agent)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    agent.server_url = f"ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"
    try:
        async with aiohttp.ClientSession() as session:
            refused = await asyncio.wait_for(agent._connect_once(session), 10)
    finally:
        await runner.cleanup()
    return refused, agent


def test_pause_closes_open_sessions_but_answers_ws_close(monkeypatch):
    out = {}

    async def script(ws, agent):
        await ws.send_json({"id": 1, "op": "ws.open"})
        sid = (await ws.receive_json())["result"]["session"]
        await agent.set_paused(True)
        out["pushed"] = await ws.receive_json(timeout=5)
        await ws.send_json({"id": 2, "op": "ws.close", "session": sid})
        out["close"] = await ws.receive_json(timeout=5)
        await ws.send_json({"id": 3, "op": "ws.open"})
        out["open"] = await ws.receive_json(timeout=5)
        out["sid"] = sid

    async def scenario():
        async with fake_ha(monkeypatch) as (_, sockets):
            await against(script)
            out["ha_closed"] = sockets[0].closed

    asyncio.run(scenario())
    assert out["pushed"] == {"op": "ws.closed", "session": out["sid"]}
    assert out["ha_closed"] is True
    assert out["close"] == {"id": 2, "ok": True, "result": None}
    assert out["open"]["error"]["code"] == "paused"


def test_reconnect_now_drops_the_open_connection():
    out = {}

    async def script(ws, agent):
        await ws.send_json({"id": 1, "op": "nope"})  # any request = hello accepted
        await ws.receive_json(timeout=5)
        out["connected"] = agent.connected
        agent.reconnect_now()
        out["msg"] = await ws.receive(timeout=5)

    refused, agent = asyncio.run(against(script))
    assert out["connected"] is True
    assert out["msg"].type == aiohttp.WSMsgType.CLOSE  # the agent closed it
    assert refused is False and agent.last_error is None and agent._wake.is_set()
    assert agent.connected is False and agent.last_connected is not None


def test_not_connected_until_the_hello_is_accepted(monkeypatch):
    async def unpaired(ws, agent):
        await ws.close(code=client.CLOSE_UNAUTHORIZED)

    refused, agent = asyncio.run(against(unpaired))
    assert refused is True and agent.last_connected is None
    assert agent.last_error.startswith("Agent není spárovaný")

    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)

    seen = {}

    async def quiet(ws, a):
        await asyncio.sleep(0.2)  # paired, nothing to do yet
        seen.update(connected=a.connected, error=a.last_error)

    asyncio.run(against(quiet, agent))
    assert seen == {"connected": True, "error": None}
    assert agent.last_connected is not None
