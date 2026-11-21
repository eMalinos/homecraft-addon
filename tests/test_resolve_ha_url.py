"""selfcheck.resolve_ha_url: HA core's port from the Supervisor's /core/info."""

import asyncio

from aiohttp import web

from connector import selfcheck, settings


def _resolve(monkeypatch, answer) -> str:
    async def core_info(request):
        assert request.headers["Authorization"] == "Bearer sup"
        return web.json_response(answer)

    async def run():
        app = web.Application()
        app.router.add_get("/core/info", core_info)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(settings, "SUPERVISOR_URL", f"http://127.0.0.1:{port}")
        try:
            await selfcheck.resolve_ha_url()
        finally:
            await runner.cleanup()

    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.setattr(settings, "SUPERVISOR_TOKEN", "sup")
    monkeypatch.setattr(settings, "HA_URL", "http://homeassistant:8123")
    asyncio.run(run())
    return settings.HA_URL


def test_port_from_core_info(monkeypatch):
    assert _resolve(monkeypatch, {"result": "ok", "data": {"port": 80}}) == (
        "http://homeassistant:80"
    )


def test_bad_answer_keeps_the_default(monkeypatch):
    assert _resolve(monkeypatch, {"result": "error"}) == "http://homeassistant:8123"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("HA_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "HA_URL", "http://127.0.0.1:1")
    asyncio.run(selfcheck.resolve_ha_url())
    assert settings.HA_URL == "http://127.0.0.1:1"
