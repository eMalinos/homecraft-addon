"""access_token.set (#565) against a fake Homecraft behind a fake Access."""

import asyncio
import json
import logging
import uuid

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_ws_proxy import fake_ha

from connector import access_token, audit
from connector.client import Client

SECRET = "n3w-s3cret"
NEW = f"new.access:{SECRET}"


async def connections(scripts, refuse=(), agent=None):
    """One `_connect_once` per script; Access answers 403 to the client ids in
    `refuse`. Returns (agent, hellos, results), a refused handshake has no hello."""
    agent = agent or Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "t")
    hellos, todo = [], list(scripts)

    async def handler(request):
        if request.headers.get("CF-Access-Client-Id") in refuse:
            return web.Response(status=403)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": "challenge", "nonce": "AAAA"})
        hellos.append(await ws.receive_json())
        await todo.pop(0)(ws, agent)
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
        async with aiohttp.ClientSession() as session:
            for _ in range(len(scripts) + len(refuse)):
                if not todo:
                    break
                results.append(await asyncio.wait_for(agent._connect_once(session), 10))
    finally:
        await runner.cleanup()
    return agent, hellos, results


async def push(ws, token=NEW):
    await ws.send_json({"id": 1, "op": "access_token.set", "token": token})
    return await ws.receive_json(timeout=5)


def test_push_reconnects_with_the_new_token_and_commits_it(caplog, data_dir):
    caplog.set_level(logging.DEBUG)
    access_token.save("old.access:s1")
    out = {}

    async def first(ws, agent):
        out["reply"] = await push(ws)
        out["then"] = await ws.receive(timeout=5)  # the agent reconnects at once

    async def second(ws, agent):
        pass

    _, hellos, results = asyncio.run(connections([first, second]))
    assert out["reply"] == {"id": 1, "ok": True, "result": None}
    assert out["then"].type == aiohttp.WSMsgType.CLOSE
    assert [h["access_client_id"] for h in hellos] == ["old.access", "new.access"]
    assert results == [False, False]
    assert access_token.load() == ("new.access", SECRET) and not access_token.pending()
    audit.flush()
    ops = [(e["op"], e["ok"]) for e in audit.recent()]
    assert ops == [("access_token.commit", True), ("access_token.set", True)]
    for text in (caplog.text, (data_dir / "audit.jsonl").read_text(), json.dumps(out["reply"])):
        assert SECRET not in text and "new.access" not in text


def test_token_refused_by_access_is_discarded_and_not_staged_again():
    access_token.save("old.access:s1")
    out = {}

    async def first(ws, agent):
        await push(ws)

    async def back_on_old(ws, agent):
        out["again"] = await push(ws)

    agent, hellos, results = asyncio.run(connections([first, back_on_old], refuse={"new.access"}))
    assert results == [False, False, False]  # pushed, 403, old token again
    assert [h["access_client_id"] for h in hellos] == ["old.access", "old.access"]
    assert access_token.load() == ("old.access", "s1") and not access_token.pending()
    assert out["again"]["error"]["code"] == "refused"
    codes = [(e["op"], e["code"]) for e in audit.recent()]
    assert ("access_token.commit", "refused") in codes


def test_no_reconnect_while_a_session_is_open(monkeypatch):
    out = {}

    async def script(ws, agent):
        await ws.send_json({"id": 1, "op": "ws.open"})
        sid = (await ws.receive_json(timeout=5))["result"]["session"]
        assert (await push(ws))["ok"] is True
        await asyncio.sleep(0.2)
        out["open_after_push"] = not ws.closed and agent._ws is not None and not agent._dropping
        await ws.send_json({"id": 3, "op": "ws.close", "session": sid})
        out["close"] = await ws.receive_json(timeout=5)
        out["then"] = await ws.receive(timeout=5)

    async def scenario():
        async with fake_ha(monkeypatch):
            await connections([script])

    asyncio.run(scenario())
    assert out["open_after_push"] is True
    assert out["close"]["ok"] is True
    assert out["then"].type == aiohttp.WSMsgType.CLOSE


def test_bad_token_and_paused():
    out = {}

    async def script(ws, agent):
        out["bad"] = await push(ws, "id:sec\r\nX-Evil: 1")
        out["typed"] = await push(ws, 5)
        await agent.set_paused(True)
        out["paused"] = await push(ws)
        await asyncio.sleep(0.1)

    async def second(ws, agent):
        pass

    asyncio.run(connections([script, second]))
    assert out["bad"]["error"]["code"] == "bad_request"
    assert "Evil" not in json.dumps(out["bad"])
    assert out["typed"]["error"]["code"] == "bad_request"
    # Pause guards HA, not the connection to Homecraft: the token is still taken.
    assert out["paused"]["ok"] is True


def test_no_token_no_access_client_id():
    async def quiet(ws, agent):
        pass

    _, hellos, _ = asyncio.run(connections([quiet]))
    assert "access_client_id" not in hellos[0]
