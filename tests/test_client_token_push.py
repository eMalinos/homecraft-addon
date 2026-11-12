"""access_token.set (#565) against a fake Homecraft behind a fake Access."""

import asyncio
import json
import logging
import uuid

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_ws_proxy import fake_ha

from connector import access_token, audit, client
from connector.client import Client

SECRET = "n3w-s3cret"
NEW = f"new.access:{SECRET}"
OLD_FP = access_token.fingerprint(("old.access", "s1"))
NEW_FP = access_token.fingerprint(("new.access", SECRET))


async def quiet(ws, agent):
    pass


async def serve(scripts, attempts=None, refuse=(), agent=None, before_prepare=None):
    """`attempts` x `_connect_once` against a fake Homecraft whose accepted
    connections run `scripts` in order. Access answers 403 to the client ids in
    `refuse` (a set, may change meanwhile). Returns (agent, hellos, seen, results):
    `seen` = the client id of every handshake, refused ones included."""
    agent = agent or Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t")
    hellos, seen, todo = [], [], list(scripts)

    async def handler(request):
        seen.append(request.headers.get("CF-Access-Client-Id"))
        if request.headers.get("CF-Access-Client-Id") in refuse:
            return web.Response(status=403)
        if before_prepare is not None:
            await before_prepare(agent)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": "challenge", "nonce": "AAAA"})
        msg = await ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            hellos.append(json.loads(msg.data))
            await (todo.pop(0) if todo else quiet)(ws, agent)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    agent.server_url = f"ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"
    results = []
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
            for _ in range(attempts or len(scripts)):
                results.append(await asyncio.wait_for(agent._connect_once(session), 10))
    finally:
        await runner.cleanup()
    return agent, hellos, seen, results


async def push(ws, token=NEW, rid=1):
    await ws.send_json({"id": rid, "op": "access_token.set", "token": token})
    return await ws.receive_json(timeout=5)


def test_push_reconnects_with_the_new_token_and_commits_it(caplog, data_dir):
    caplog.set_level(logging.DEBUG)
    access_token.save("old.access:s1")
    out = {}

    async def first(ws, agent):
        out["reply"] = await push(ws)
        out["then"] = await ws.receive(timeout=5)  # the agent reconnects at once

    _, hellos, _, results = asyncio.run(serve([first, quiet]))
    assert out["reply"] == {"id": 1, "ok": True, "result": None}
    assert out["then"].type == aiohttp.WSMsgType.CLOSE
    assert [h["access_token_fp"] for h in hellos] == [OLD_FP, NEW_FP]
    assert results == ["outage", "outage"]
    assert access_token.load() == (("new.access", SECRET), False)
    assert access_token.updated_by_homecraft() is not None
    audit.flush()
    ops = [(e["op"], e["ok"]) for e in audit.recent()]
    assert ops == [("access_token.commit", True), ("access_token.set", True)]
    for text in (caplog.text, (data_dir / "audit.jsonl").read_text(), json.dumps(hellos)):
        assert SECRET not in text and "new.access" not in text


def test_refused_staged_token_falls_back_in_the_same_attempt_and_commits_later(caplog):
    access_token.save("old.access:s1")
    refuse = {"new.access"}
    out = {}

    async def first(ws, agent):
        await push(ws)
        await ws.receive(timeout=5)  # reconnect

    async def on_old(ws, agent):
        out["again"] = await push(ws, rid=2)  # Homecraft pushes the same token again
        await asyncio.sleep(0.3)
        out["stayed"] = not ws.closed and agent._ws is not None

    agent, hellos, seen, results = asyncio.run(serve([first, on_old], refuse=refuse))
    assert seen == ["old.access", "new.access", "old.access"]  # 403, then old at once
    assert [h["access_token_fp"] for h in hellos] == [OLD_FP, OLD_FP]
    assert results == ["outage", "outage"]
    assert out["again"]["ok"] is True and out["stayed"] is True  # no reconnect loop
    assert access_token.load() == (("new.access", SECRET), True)  # still staged
    assert access_token.active() == ("old.access", "s1")

    # Refused again on the next connection: logged once only.
    asyncio.run(serve([quiet], refuse=refuse, agent=agent))
    assert caplog.text.count("odmítnut Cloudflare") == 1
    assert [(e["op"], e["code"]) for e in audit.recent()].count(
        ("access_token.commit", "refused")
    ) == 1

    # Access now lets it through: committed on the next connection.
    _, hellos, seen, _ = asyncio.run(serve([quiet], agent=agent))
    assert seen == ["new.access"] and hellos[0]["access_token_fp"] == NEW_FP
    assert access_token.load() == (("new.access", SECRET), False)


def test_rotation_waits_for_open_sessions(monkeypatch):
    out = {}

    async def script(ws, agent):
        await ws.send_json({"id": 1, "op": "ws.open"})
        sid = (await ws.receive_json(timeout=5))["result"]["session"]
        assert (await push(ws, rid=2))["ok"] is True
        await asyncio.sleep(0.2)
        out["open_after_push"] = not ws.closed and not agent._dropping
        await ws.send_json({"id": 3, "op": "ws.close", "session": sid})
        out["close"] = await ws.receive_json(timeout=5)
        out["then"] = await ws.receive(timeout=5)

    async def scenario():
        async with fake_ha(monkeypatch):
            await serve([script])

    asyncio.run(scenario())
    assert out["open_after_push"] is True
    assert out["close"]["ok"] is True
    assert out["then"].type == aiohttp.WSMsgType.CLOSE


def test_rotation_when_ha_closes_the_last_session(monkeypatch):
    out = {}

    async def script(ws, agent, sockets):
        await ws.send_json({"id": 1, "op": "ws.open"})
        await ws.receive_json(timeout=5)
        await push(ws, rid=2)
        await sockets[0].close()  # HA ends the session by itself
        out["closed"] = await ws.receive_json(timeout=5)
        out["then"] = await ws.receive(timeout=5)

    async def scenario():
        async with fake_ha(monkeypatch) as (_, sockets):
            await serve([lambda ws, agent: script(ws, agent, sockets)])

    asyncio.run(scenario())
    assert out["closed"]["op"] == "ws.closed"
    assert out["then"].type == aiohttp.WSMsgType.CLOSE


def test_rotation_deadline_with_a_session_left_open(monkeypatch):
    monkeypatch.setattr(client, "ROTATE_AFTER", 0.2)
    out = {}

    async def script(ws, agent):
        await ws.send_json({"id": 1, "op": "ws.open"})
        await ws.receive_json(timeout=5)
        await push(ws, rid=2)
        out["then"] = await ws.receive(timeout=5)

    async def scenario():
        async with fake_ha(monkeypatch):
            await serve([script, quiet])

    asyncio.run(scenario())
    assert out["then"].type == aiohttp.WSMsgType.CLOSE
    assert access_token.load() == (("new.access", SECRET), False)


def test_panel_save_during_a_staged_handshake_drops_that_connection():
    access_token.save("old.access:s1")
    access_token.stage(NEW)
    calls = []

    async def panel_saves(agent):
        if not calls:  # the first handshake is in flight with the staged token
            access_token.save("mine.access:s3")
            agent.reconnect_now()
        calls.append(1)

    _, hellos, seen, _ = asyncio.run(serve([quiet], attempts=2, before_prepare=panel_saves))
    assert seen == ["new.access", "mine.access"]
    # The first connection never said hello and nothing was committed.
    assert [h["access_token_fp"] for h in hellos] == [
        access_token.fingerprint(("mine.access", "s3"))
    ]
    assert access_token.load() == (("mine.access", "s3"), False)


def test_bad_token_and_paused():
    out = {}

    async def script(ws, agent):
        out["bad"] = await push(ws, "id:sec\r\nX-Evil: 1")
        out["typed"] = await push(ws, 5)
        await agent.set_paused(True)
        out["paused"] = await push(ws)
        await asyncio.sleep(0.1)

    asyncio.run(serve([script, quiet]))
    assert out["bad"]["error"]["code"] == "bad_request"
    assert "Evil" not in json.dumps(out["bad"])
    assert out["typed"]["error"]["code"] == "bad_request"
    # Pause guards HA, not the connection to Homecraft: the token is still taken.
    assert out["paused"]["ok"] is True


def test_no_token_no_fingerprint():
    _, hellos, _, _ = asyncio.run(serve([quiet]))
    assert "access_token_fp" not in hellos[0]


def test_homecraft_connection_keeps_no_cookies(monkeypatch):
    """A CF_Authorization cookie from Access must never be sent back."""
    agent = Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t")
    cookies = []
    done = asyncio.Event()

    async def handler(request):
        cookies.append(request.headers.get("Cookie"))
        ws = web.WebSocketResponse()
        ws.headers["Set-Cookie"] = "CF_Authorization=abc; Path=/"
        await ws.prepare(request)
        await ws.send_json({"op": "challenge", "nonce": "AAAA"})
        await ws.receive()
        if len(cookies) >= 2:
            done.set()
        else:
            agent.reconnect_now()
        await ws.receive()
        return ws

    async def scenario():
        app = web.Application()
        app.router.add_get("/ws", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        agent.server_url = f"ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"
        task = asyncio.create_task(agent.run())
        try:
            await asyncio.wait_for(done.wait(), 10)
        finally:
            task.cancel()
            await runner.cleanup()

    asyncio.run(scenario())
    assert cookies == [None, None]
