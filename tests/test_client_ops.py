"""The request loop against a fake Homecraft (aiohttp test server)."""

import asyncio
import uuid

import aiohttp
import pytest
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from connector import audit, client, settings
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


def test_packages_include_op(tmp_path, monkeypatch):
    (tmp_path / "configuration.yaml").write_text(
        "homeassistant:\n  packages: !include_dir_named packages\n"
    )
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)
    got = run_against_homecraft([{"id": 1, "op": "config.packages_include"}], expect=1)
    assert got == [{"id": 1, "ok": True, "result": "present"}]
    assert client._audit_target({"op": "config.packages_include"}) == "configuration.yaml"


def test_card_write_passes_the_signature(tmp_path, monkeypatch, card_key):
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)
    path, text = "www/homecraft/heating-card.js", "x"
    got = run_against_homecraft(
        [{"id": 1, "op": "file.write", "path": path, "text": text, "signature": card_key(text)}],
        expect=1,
    )
    assert got == [{"id": 1, "ok": True, "result": "www_created"}]
    got = run_against_homecraft(
        [{"id": 2, "op": "file.write", "path": path, "text": text}], expect=1
    )
    assert got[0]["ok"] is False and got[0]["error"]["code"] == "refused_content"


def _update(card="x", state="available", **kw) -> dict:
    return {
        "kind": "card",
        "id": card,
        "name": "X card",
        "installed": "0.1.0",
        "available": "0.2.0",
        "state": state,
        "message": "",
        **kw,
    }


def test_updates_set_is_stored_even_when_paused_and_dropped_on_disconnect():
    items = [_update(), _update("y", "done", message="ok")]
    got: list[dict] = []
    seen: list[list[dict]] = []

    async def scenario():
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"op": "challenge", "nonce": "AAAA"})
            await ws.receive_json()
            await ws.send_json({"id": 1, "op": "updates.set", "items": items})
            got.append(await ws.receive_json(timeout=5))
            seen.append(agent.updates)
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
            f"ws://127.0.0.1:{port}/ws", uuid.uuid4(), Ed25519PrivateKey.generate(), "t", True
        )
        async with aiohttp.ClientSession() as session:
            await agent._connect_once(session)
        await runner.cleanup()
        assert agent.updates == []

    asyncio.run(scenario())
    assert got == [{"id": 1, "ok": True, "result": None}]
    assert seen == [items]


def test_updates_set_rejects_bad_shapes():
    bad = [
        "x",
        [_update()] * 51,
        ["x"],
        [_update(state="weird")],
        [_update(name="n" * 81)],
        [_update(message=None)],
        [{k: v for k, v in _update().items() if k != "installed"}],
    ]
    got = run_against_homecraft(
        [{"id": i, "op": "updates.set", "items": items} for i, items in enumerate(bad)],
        expect=len(bad),
    )
    assert all(g["ok"] is False and g["error"]["code"] == "bad_request" for g in got)


class _FakeConn:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, data: dict) -> None:
        self.sent.append(data)


def test_run_update_sends_update_run(agent):
    conn = _FakeConn()
    agent._conn, agent.connected = conn, True
    agent.updates = [_update(), _update("busy", "running"), _update("again", "failed")]
    asyncio.run(agent.run_update("card", "x"))
    asyncio.run(agent.run_update("card", "again"))
    assert conn.sent == [
        {"op": "update.run", "kind": "card", "item": "x"},
        {"op": "update.run", "kind": "card", "item": "again"},
    ]
    assert audit.recent()[0]["op"] == "update.run"
    assert audit.recent()[0]["target"] == "card again"
    for kind, item in (("card", "busy"), ("card", "nope"), ("other", "x")):
        with pytest.raises(ValueError):
            asyncio.run(agent.run_update(kind, item))
    agent.paused = True
    with pytest.raises(RuntimeError, match="paused"):
        asyncio.run(agent.run_update("card", "x"))
    agent.paused, agent.connected = False, False
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(agent.run_update("card", "x"))
    assert len(conn.sent) == 2
