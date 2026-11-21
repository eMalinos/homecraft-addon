"""The panel: ingress only, CSRF guard, pause and reconnect."""

from connector import panel, state

CSRF = {panel.CSRF_HEADER: panel.NONCE}


def test_refuses_anyone_but_the_ingress_proxy(run_panel):
    async def scenario(http):
        return [
            (await http.get("/")).status,
            (await http.get("/api/status")).status,
            (await http.post("/api/pause", json={"paused": True}, headers=CSRF)).status,
        ]

    assert run_panel(scenario, from_ingress=False) == [403, 403, 403]


def test_dev_skips_the_ingress_check(run_panel, monkeypatch):
    async def scenario(http):
        monkeypatch.setattr(panel.settings, "DEV", True)
        return (await http.get("/api/status")).status

    assert run_panel(scenario, from_ingress=False) == 200


def test_page_and_status(run_panel):
    async def scenario(http):
        page = await http.get("/")
        status = await http.get("/api/status")
        return page.status, await page.text(), page.headers, await status.json()

    code, html, headers, status = run_panel(scenario)
    assert code == 200 and "Pozastavit Homecraft" in html
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    assert status["pairing_code"] == "CODE"
    assert status["verification_code"] == "ABC-DEF" and status["pending_approval"] is False
    assert 'id="verification"' in html and "Čeká na schválení v Homecraftu" in html
    assert status["connected"] is False and status["paused"] is False
    assert status["token_set"] is False
    assert status["audit_24h"] == {"count": 0, "refused": 0, "capped": False}
    for tab in ("overview", "security", "audit"):
        assert f'href="#{tab}"' in html and f'data-tab="{tab}"' in html


def test_state_changes_need_the_csrf_header(run_panel, agent):
    async def scenario(http):
        return [
            (await http.post("/api/pause", json={"paused": True})).status,
            (await http.post("/api/reconnect")).status,
            (await http.post("/api/access-token", json={"token": "a:b"})).status,
            (await http.delete("/api/access-token")).status,
            (
                await http.post(
                    "/api/pause",
                    json={"paused": True},
                    headers={**CSRF, "Sec-Fetch-Site": "cross-site"},
                )
            ).status,
        ]

    assert run_panel(scenario) == [403] * 5
    assert agent.paused is False and not state.load_paused()


def test_pause_switch_persists(run_panel, agent):
    async def scenario(http):
        on = await http.post("/api/pause", json={"paused": True}, headers=CSRF)
        bad = await http.post("/api/pause", json={"paused": "yes"}, headers=CSRF)
        return on.status, bad.status, (await (await http.get("/api/status")).json())["paused"]

    assert run_panel(scenario) == (200, 400, True)
    assert agent.paused is True and state.load_paused() is True

    async def resume(http):
        return (await http.post("/api/pause", json={"paused": False}, headers=CSRF)).status

    assert run_panel(resume) == 200
    assert agent.paused is False and state.load_paused() is False


def test_reconnect_skips_the_wait(run_panel, agent):
    agent.backoff.next("refused")
    agent.backoff.next("refused")

    async def scenario(http):
        return (await http.post("/api/reconnect", headers=CSRF)).status

    assert run_panel(scenario) == 200
    assert agent._wake.is_set()
    assert agent.backoff.next("refused") == 60  # started over


def test_wrong_nonce_is_refused_with_security_headers(run_panel):
    async def scenario(http):
        resp = await http.post("/api/reconnect", headers={panel.CSRF_HEADER: "1"})
        return resp.status, resp.headers

    status, headers = run_panel(scenario)
    assert status == 403
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"


def test_updates_list_and_run(run_panel, agent, monkeypatch):
    item = {"kind": "card", "id": "x", "name": "X", "installed": "1", "available": "2",
            "state": "available", "message": ""}  # fmt: skip
    agent.updates = [item]
    calls: list[tuple[str, str]] = []
    errors: list[Exception] = []

    async def run_update(kind, item):
        calls.append((kind, item))
        if errors:
            raise errors.pop()

    monkeypatch.setattr(agent, "run_update", run_update)
    body = {"kind": "card", "item": "x"}

    async def scenario(http):
        listed = await (await http.get("/api/updates")).json()
        no_nonce = (await http.post("/api/updates/run", json=body)).status
        ok = await http.post("/api/updates/run", json=body, headers=CSRF)
        out = [listed, no_nonce, ok.status, await ok.json()]
        for e in (RuntimeError("offline"), RuntimeError("paused"), ValueError()):
            errors.append(e)
            r = await http.post("/api/updates/run", json=body, headers=CSRF)
            out.append((r.status, (await r.json())["error"]))
        out.append(
            (await http.post("/api/updates/run", json={"kind": "card"}, headers=CSRF)).status
        )
        return out

    listed, no_nonce, ok, ok_body, *errs, missing = run_panel(scenario)
    assert listed == {"connected": False, "paused": False, "items": [item]}
    assert no_nonce == 403 and ok == 200 and ok_body == {"ok": True}
    assert errs == [
        (409, "Connector není připojený"),
        (409, "Homecraft je pozastavený"),
        (400, "Položka není v seznamu"),
    ]
    assert missing == 400
    assert calls == [("card", "x")] * 4


def test_page_has_the_updates_section(run_panel):
    async def scenario(http):
        return await (await http.get("/")).text()

    html = run_panel(scenario)
    assert 'id="updates"' in html and "<h2>Aktualizace</h2>" in html
    assert html.index('id="updates"') < html.index("<h2>Spojení</h2>")


def test_page_carries_the_nonce(run_panel):
    async def scenario(http):
        return await (await http.get("/")).text()

    html = run_panel(scenario)
    assert f'const NONCE = "{panel.NONCE}"' in html and "{{NONCE}}" not in html
