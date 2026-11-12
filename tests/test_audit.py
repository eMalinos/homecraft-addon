"""The audit: every operation by name and target, never its content."""

import json

from test_client_ops import run_against_homecraft

from connector import audit, client, settings


def test_record_recent_newest_first_and_bounded():
    for i in range(audit.MAXLEN + 5):
        audit.record("file.read", f"packages/{i}.yaml", True, duration_ms=1.23)
    rows = audit.recent()
    assert len(rows) == audit.MAXLEN
    assert rows[0]["target"] == f"packages/{audit.MAXLEN + 4}.yaml"
    assert rows[0]["ok"] is True and rows[0]["code"] is None and rows[0]["ms"] == 1.2


def test_load_trims_the_file_and_fills_memory(data_dir):
    lines = [json.dumps({"op": "http", "target": str(i), "ok": True}) for i in range(1500)]
    (data_dir / "audit.jsonl").write_text("\n".join(lines) + "\nnot json\n")
    audit.load()
    kept = (data_dir / "audit.jsonl").read_text().splitlines()
    assert len(kept) == audit.KEEP and kept[-1] == "not json"
    assert audit.recent()[0]["target"] == "1499" and len(audit.recent()) == audit.MAXLEN - 1
    audit.record("ws.open", "", False, "ha_unreachable")
    audit.flush()
    assert json.loads((data_dir / "audit.jsonl").read_text().splitlines()[-1])["code"] == (
        "ha_unreachable"
    )


def test_file_is_trimmed_at_runtime(data_dir, monkeypatch):
    monkeypatch.setattr(audit, "KEEP", 10)
    audit.load()
    for i in range(21):
        audit.record("http", str(i), True)
    audit.flush()
    lines = (data_dir / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 10 and json.loads(lines[-1])["target"] == "20"


def test_targets_carry_no_content():
    t = client._audit_target
    assert t({"op": "http", "method": "GET", "path": "/api/hassio_ingress/SECRET/flows?x=1"}) == (
        "GET /api/hassio_ingress/…/flows"
    )
    assert t({"op": "ws.send", "msg": {"type": "call_service", "domain": "a", "service": "b"}}) == (
        "call_service a.b"
    )
    assert t({"op": "ws.send", "msg": {"type": "auth", "access_token": "SECRET"}}) == "auth"
    assert t({"op": "file.write", "path": "packages/x.yaml", "text": "SECRET"}) == "packages/x.yaml"
    assert t({"op": "ws.send", "msg": "junk"}) == ""


def test_operations_are_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)
    run_against_homecraft(
        [
            {"id": 1, "op": "file.write", "path": "packages/a.yaml", "text": "secret: SECRET"},
            {"id": 2, "op": "file.read", "path": "secrets.yaml"},
            {"id": 3, "op": "ws.send", "session": "nope", "msg": {"id": 1, "type": "get_states"}},
        ],
        expect=4,
    )
    audit.flush()
    rows = {r["target"]: r for r in audit.recent()}
    assert rows["packages/a.yaml"]["ok"] is True and rows["packages/a.yaml"]["op"] == "file.write"
    assert rows["secrets.yaml"]["code"] == "not_allowed"
    assert rows["get_states"]["code"] == "unknown_session"
    assert "SECRET" not in (settings.DATA_DIR / "audit.jsonl").read_text()


def test_refused_ws_send_is_audited_as_not_allowed():
    import asyncio

    class Conn:
        class sessions:
            @staticmethod
            async def send(sid, msg):
                return False  # what Sessions.send says after refusing

    agent = client.Client("ws://x", None, None, "t")
    req = {"id": 1, "op": "ws.send", "session": "s", "msg": {"id": 2, "type": "render_template"}}
    reply = asyncio.run(agent._reply(req, Conn))
    assert reply == {"id": 1, "ok": True, "result": None}
    assert audit.recent()[0]["code"] == "not_allowed"
