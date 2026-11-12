"""The request loop against a fake Homecraft (aiohttp test server)."""

import asyncio
import uuid

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from connector import client, settings
from connector.client import Client


def run_against_homecraft(requests: list[dict], expect: int, paused=False) -> list[dict]:
    """Serve `requests` after the hello; collect what the agent sends back."""
    got: list[dict] = []

    async def scenario():
        done = asyncio.Event()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"op": "challenge", "nonce": "AAAA"})
            assert (await ws.receive_json())["op"] == "hello"
            for r in requests:
                await ws.send_json(r)
            while len(got) < expect:
                got.append(await ws.receive_json(timeout=5))
            done.set()
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/ws", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        agent = Client(
            f"ws://127.0.0.1:{port}/ws", uuid.uuid4(), Ed25519PrivateKey.generate(), "t", paused
        )
        async with aiohttp.ClientSession() as session:
            await agent._connect_once(session)
        await runner.cleanup()
        assert done.is_set()

    asyncio.run(scenario())
    return got


def test_failed_ws_send_also_answers_the_ha_command():
    got = run_against_homecraft(
        [{"id": 1, "op": "ws.send", "session": "nope", "msg": {"id": 7, "type": "get_states"}}],
        expect=2,
    )
    reply = next(g for g in got if g.get("id") == 1)
    push = next(g for g in got if g.get("op") == "ws.msg")
    assert reply["error"]["code"] == "unknown_session"
    assert push == {
        "op": "ws.msg",
        "session": "nope",
        "msg": {
            "id": 7,
            "type": "result",
            "success": False,
            "error": {"code": "unknown_session", "message": ""},
        },
    }


def test_paused_ws_send_answers_the_ha_command():
    got = run_against_homecraft(
        [{"id": 1, "op": "ws.send", "session": "s", "msg": {"id": 3, "type": "get_states"}}],
        expect=2,
        paused=True,
    )
    push = next(g for g in got if g.get("op") == "ws.msg")
    assert push["msg"]["id"] == 3 and push["msg"]["error"]["code"] == "paused"


def test_oversized_reply_becomes_too_large(tmp_path, monkeypatch):
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "big.yaml").write_text("é" * 2000)  # é escaped: 6 bytes each
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)
    monkeypatch.setattr(client, "MAX_FRAME", 10_000)
    got = run_against_homecraft(
        [
            {"id": 1, "op": "file.read", "path": "packages/big.yaml"},
            {"id": 2, "op": "file.list", "path": "packages"},
        ],
        expect=2,
    )
    by_id = {g["id"]: g for g in got}
    assert by_id[1]["ok"] is False and by_id[1]["error"]["code"] == "too_large"
    assert by_id[2] == {"id": 2, "ok": True, "result": ["big.yaml"]}


def test_oversized_push_keeps_only_the_ha_error():
    msg = {"op": "ws.msg", "session": "s", "msg": {"id": 4, "type": "result", "result": "x"}}
    assert client._oversized(msg)["msg"]["error"]["code"] == "too_large"
    assert client._oversized({"op": "ws.msg", "session": "s", "msg": {"type": "event"}}) is None
