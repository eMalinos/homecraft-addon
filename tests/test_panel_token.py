"""The Access token is write-only: saved and cleared in the panel, never shown."""

from connector import access_token, panel

CSRF = {panel.CSRF_HEADER: panel.NONCE}
SECRET = "s3cr3t-value-0123456789abcdef"
TOKEN = f"client-id.access:{SECRET}"


def test_save_status_delete(run_panel, agent):
    async def scenario(http):
        bodies = []
        saved = await http.post("/api/access-token", json={"token": TOKEN}, headers=CSRF)
        bodies.append(await saved.text())
        for path in ("/", "/api/status", "/api/audit"):
            bodies.append(await (await http.get(path)).text())
        status = await (await http.get("/api/status")).json()
        deleted = await http.delete("/api/access-token", headers=CSRF)
        after = await (await http.get("/api/status")).json()
        return saved.status, bodies, status, deleted.status, after

    saved, bodies, status, deleted, after = run_panel(scenario)
    assert saved == 200 and status["token_set"] is True
    assert agent._wake.is_set()  # saving reconnects at once
    assert not any(SECRET in b or "client-id.access" in b for b in bodies)
    assert deleted == 200 and after["token_set"] is False
    assert access_token.active() is None


def test_bad_token_is_refused_without_echo(run_panel):
    async def scenario(http):
        resp = await http.post("/api/access-token", json={"token": SECRET}, headers=CSRF)
        typed = await http.post("/api/access-token", json={"token": 5}, headers=CSRF)
        return resp.status, await resp.text(), typed.status

    status, body, typed = run_panel(scenario)
    assert status == 400 and SECRET not in body and typed == 400
    assert access_token.active() is None


def test_change_replaces_the_token(run_panel):
    async def scenario(http):
        await http.post("/api/access-token", json={"token": "a:old"}, headers=CSRF)
        await http.post("/api/access-token", json={"token": "b:new"}, headers=CSRF)

    run_panel(scenario)
    assert access_token.active() == ("b", "new")


def test_status_says_when_homecraft_updated_the_token(run_panel):
    async def status(http):
        return await (await http.get("/api/status")).json()

    access_token.save("old.access:s1")
    first = run_panel(status)
    assert first["token_updated_at"] is None and first["token_pending"] is False
    access_token.stage(TOKEN)
    staged = run_panel(status)
    assert staged["token_pending"] is True and staged["token_updated_at"] is None
    access_token.commit(access_token.parse(TOKEN))
    body = run_panel(status)
    assert body["token_set"] is True and body["token_pending"] is False
    assert isinstance(body["token_updated_at"], float)
    assert SECRET not in str([first, staged, body])


def test_ha_admin_token_is_write_only(run_panel, data_dir):
    from connector import ha_token, panel

    csrf = {panel.CSRF_HEADER: panel.NONCE}

    async def scenario(http):
        bad = await http.post("/api/ha-token", json={"token": "has space"}, headers=csrf)
        saved = await http.post("/api/ha-token", json={"token": " eyJ.abc.def "}, headers=csrf)
        status = await (await http.get("/api/status")).json()
        blind = await http.delete("/api/ha-token")  # no CSRF header
        cleared = await http.delete("/api/ha-token", headers=csrf)
        after = await (await http.get("/api/status")).json()
        return (
            bad.status,
            saved.status,
            await saved.text(),
            status,
            blind.status,
            cleared.status,
            after,
        )

    bad, saved, body, status, blind, cleared, after = run_panel(scenario)
    assert (bad, saved, blind, cleared) == (400, 200, 403, 200)
    assert "eyJ" not in body and "eyJ" not in str(status)
    assert status["ha_token_set"] is True and after["ha_token_set"] is False
    assert ha_token.load() is None
    assert not (data_dir / "ha_token").exists()
