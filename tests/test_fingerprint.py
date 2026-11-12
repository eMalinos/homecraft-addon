"""fingerprint.Collector against a fake Supervisor /info and a fake HA
get_config; run.sh and the AppArmor profile around HA's install uuid."""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from aiohttp import web

from connector import fingerprint, settings

ADDON = Path(__file__).parent.parent / "homecraft_connector"
MACHINE_ID = "0123456789abcdef0123456789abcdef"
HA_UUID = "fedcba9876543210fedcba9876543210"
LONG_NAME = "Domov " + "x" * 80


def fp(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@contextlib.asynccontextmanager
async def fake(monkeypatch, info_status=200, name=LONG_NAME, calls=None):
    calls = [] if calls is None else calls

    async def info(request):
        calls.append("info")
        assert request.headers["Authorization"] == "Bearer sup"
        return web.json_response({"data": {"machine_id": MACHINE_ID}}, status=info_status)

    async def websocket(request):
        calls.append("ws")
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


def collect(monkeypatch, ha_uuid="", supervisor=True, rounds=1, **kw):
    monkeypatch.setattr(settings, "HA_INSTALL_UUID", ha_uuid)

    async def go():
        async with fake(monkeypatch, **kw):
            if not supervisor:
                monkeypatch.setattr(settings, "SUPERVISOR_URL", "")  # dev
            collector = fingerprint.Collector()
            return [await collector() for _ in range(rounds)][-1]

    return asyncio.run(go())


def test_collects_name_and_fingerprints_only(monkeypatch, caplog):
    out = collect(monkeypatch, HA_UUID)
    assert out == {"ha_name": LONG_NAME[:64], "machine_fp": fp(MACHINE_ID), "ha_fp": fp(HA_UUID)}
    for text in (json.dumps(out), caplog.text):
        assert MACHINE_ID not in text and HA_UUID not in text


def test_found_values_are_cached(monkeypatch):
    calls = []
    collect(monkeypatch, rounds=3, calls=calls)
    assert sorted(calls) == ["info", "ws"]


def test_missing_values_are_left_out_and_retried(monkeypatch, caplog):
    # No install uuid, /info refused (403), an empty name.
    calls = []
    assert collect(monkeypatch, info_status=403, name="", rounds=2, calls=calls) == {}
    assert sorted(calls) == ["info", "info", "ws", "ws"]  # looked up again next attempt
    assert caplog.text.count("HTTP 403") == 1  # warned once, not per attempt


def test_no_supervisor(monkeypatch):
    assert collect(monkeypatch, supervisor=False) == {"ha_name": LONG_NAME[:64]}


def test_unreachable_ha_is_only_logged(monkeypatch):
    monkeypatch.setattr(settings, "HA_INSTALL_UUID", "")
    monkeypatch.setattr(settings, "SUPERVISOR_URL", "")
    monkeypatch.setattr(settings, "CORE_WS_URL", "ws://127.0.0.1:1/api/websocket")
    assert asyncio.run(fingerprint.Collector()()) == {}


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not installed")
@pytest.mark.parametrize(
    "content, expected",
    [
        (json.dumps({"version": 1, "data": {"uuid": HA_UUID}}), HA_UUID),
        ("not json", ""),
        (None, ""),
    ],
)
def test_run_sh_passes_the_install_uuid(tmp_path, content, expected):
    """run.sh with a fake python3 on PATH that prints what it got."""
    if content is not None:
        (tmp_path / ".storage").mkdir()
        (tmp_path / ".storage/core.uuid").write_text(content)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text('#!/bin/sh\nprintf "%s|%s" "$HA_INSTALL_UUID" "$*"\n')
    (bin_dir / "python3").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CONFIG_ROOT": str(tmp_path)}
    out = subprocess.run(
        [str(ADDON / "run.sh")], env=env, capture_output=True, text=True, check=True
    ).stdout
    assert out == f"{expected}|-m connector"


def test_only_the_start_script_may_read_core_uuid():
    text = (ADDON / "apparmor.txt").read_text()
    parent, agent = text.split("profile connector", 1)
    assert re.search(r"^\s*/homeassistant/\.storage/core\.uuid r,", parent, re.M)
    assert "core.uuid" not in re.sub(r"#.*", "", agent)
    assert re.search(r"^\s*deny /homeassistant/\.storage/\{,\*\*\} rw,", agent, re.M)
    assert 'CMD ["/run.sh"]' in (ADDON / "Dockerfile").read_text()
