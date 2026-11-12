"""Pause, reconnect and the connection state against a scripted fake Homecraft."""

import asyncio
import logging
import uuid

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_ws_proxy import fake_ha

from connector import client, identity
from connector.client import Client

HELLOS: list[dict] = []  # every hello `against` received


async def against(script, agent=None, before_challenge=0.0):
    """Run one agent connection against `script(ws, agent)`; returns (outcome, agent)."""
    agent = agent or Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t")

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await asyncio.sleep(before_challenge)
        await ws.send_json({"op": "challenge", "nonce": "AAAA"})
        HELLOS.append(await ws.receive_json())
        assert HELLOS[-1]["op"] == "hello"
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
    assert refused == "outage" and agent.last_error is None and agent._wake.is_set()
    assert agent.connected is False and agent.last_connected is not None


def test_not_connected_until_the_hello_is_accepted(monkeypatch):
    async def unpaired(ws, agent):
        await ws.close(code=client.CLOSE_UNAUTHORIZED)

    refused, agent = asyncio.run(against(unpaired))
    assert refused == "refused" and agent.last_connected is None
    assert agent.last_error.startswith("Agent není spárovaný")

    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)

    seen = {}

    async def quiet(ws, a):
        await asyncio.sleep(0.2)  # paired, nothing to do yet
        seen.update(connected=a.connected, error=a.last_error)

    asyncio.run(against(quiet, agent))
    assert seen == {"connected": True, "error": None}
    assert agent.last_connected is not None


def test_hello_carries_the_public_key_and_extra():
    key = Ed25519PrivateKey.generate()
    calls = []

    async def extra():
        calls.append(1)
        return {"ha_name": "Domov"}

    agent = Client("ws://x", uuid.uuid4(), key, "t", hello_extra=extra)

    async def quiet(ws, a):
        pass

    asyncio.run(against(quiet, agent))
    assert HELLOS[-1]["public_key"] == identity.public_b64(key)
    assert HELLOS[-1]["ha_name"] == "Domov"
    asyncio.run(against(quiet, agent))
    assert calls == [1, 1]  # asked before every attempt (it caches by itself)


def test_lookups_stop_once_paired_and_resume_when_unpaired(monkeypatch):
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)
    calls = []

    async def extra():
        calls.append(1)
        return {}

    agent = Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t", hello_extra=extra)

    async def accepted(ws, a):
        await asyncio.sleep(0.2)

    async def unpaired(ws, a):
        await ws.close(code=client.CLOSE_UNAUTHORIZED)

    asyncio.run(against(accepted, agent))
    asyncio.run(against(accepted, agent))
    assert calls == [1]  # paired: no more lookups
    asyncio.run(against(unpaired, agent))  # still thought paired: no lookup
    asyncio.run(against(unpaired, agent))
    assert calls == [1, 1]


def test_reconnect_during_lookups_does_not_drop_the_new_connection(monkeypatch):
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)
    seen = {}

    async def extra():
        agent.reconnect_now()  # e.g. the panel saved a token meanwhile
        return {}

    agent = Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t", hello_extra=extra)

    async def quiet(ws, a):
        await asyncio.sleep(0.2)
        seen["connected"] = a.connected

    asyncio.run(against(quiet, agent))
    assert seen == {"connected": True}


async def pending(ws, a):
    await ws.close(code=client.CLOSE_PENDING)


def test_pending_approval_only_while_the_backend_says_so(monkeypatch):
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)
    outcome, agent = asyncio.run(against(pending))
    assert outcome == "pending" and agent.pending_approval is True
    assert agent.last_error.startswith("Čeká na schválení")

    async def accepted(ws, a):
        await asyncio.sleep(0.2)  # still open after the hello = accepted

    assert asyncio.run(against(accepted, agent))[0] == "outage"
    assert agent.pending_approval is False

    async def unpaired(ws, a):
        await ws.close(code=client.CLOSE_UNAUTHORIZED)

    async def other(ws, a):
        await ws.close(code=4000)

    for script in (unpaired, other):
        asyncio.run(against(pending, agent))
        assert agent.pending_approval is True
        asyncio.run(against(script, agent))
        assert agent.pending_approval is False

    # Homecraft unreachable (outage) clears it too.
    asyncio.run(against(pending, agent))
    agent.server_url = "ws://127.0.0.1:1/ws"

    async def unreachable():
        async with aiohttp.ClientSession() as session:
            return await agent._connect_once(session)

    assert asyncio.run(unreachable()) == "outage" and agent.pending_approval is False


def test_accepted_timer_starts_after_the_hello(monkeypatch):
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)
    # A slow challenge: the hello goes out late, the 4403 right after it.
    outcome, agent = asyncio.run(against(pending, before_challenge=0.3))
    assert outcome == "pending" and agent.last_connected is None and not agent.connected


def test_slow_pending_close_is_not_a_connection(monkeypatch):
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)

    async def slow_pending(ws, a):
        await asyncio.sleep(0.2)  # long enough to count as accepted
        await ws.close(code=client.CLOSE_PENDING)

    outcome, agent = asyncio.run(against(slow_pending))
    assert outcome == "pending" and agent.pending_approval is True
    assert agent.last_connected is None and not agent.connected


def test_slow_unpaired_close_is_not_a_connection(monkeypatch):
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)

    async def slow_unpaired(ws, a):
        await asyncio.sleep(0.2)
        await ws.close(code=client.CLOSE_UNAUTHORIZED)

    outcome, agent = asyncio.run(against(slow_unpaired))
    assert outcome == "refused" and agent.last_connected is None and not agent.connected
    assert agent.last_error.startswith("Agent není spárovaný")


def test_pending_logged_once_even_after_slow_closes(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(client, "HELLO_ACCEPTED_AFTER", 0.05)

    async def slow_pending(ws, a):
        await asyncio.sleep(0.2)
        await ws.close(code=client.CLOSE_PENDING)

    _, agent = asyncio.run(against(slow_pending))
    asyncio.run(against(slow_pending, agent))
    assert caplog.text.count("Čeká na schválení") == 1


def test_run_waits_the_pending_delay(monkeypatch):
    async def scenario():
        agent = Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t")
        delays = []

        async def attempt(session, ha=None):
            return "pending"

        async def wait(delay):
            delays.append(delay)
            if len(delays) == 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(agent, "_connect_once", attempt)
        monkeypatch.setattr(agent, "wait", wait)
        try:
            await agent.run()
        except asyncio.CancelledError:
            pass
        return delays

    assert asyncio.run(scenario()) == [10, 30, 30]
