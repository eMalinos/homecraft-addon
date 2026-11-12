"""The pause stops every operation and survives a restart."""

from test_client_ops import run_against_homecraft

from connector import audit, settings, state

OPS = [
    {"id": 1, "op": "ws.open"},
    {"id": 2, "op": "ws.close", "session": "s"},
    {"id": 3, "op": "http", "method": "GET", "path": "/api/"},
    {"id": 4, "op": "file.write", "path": "packages/a.yaml", "text": "x"},
    {"id": 5, "op": "file.read", "path": "packages/a.yaml"},
    {"id": 6, "op": "file.delete", "path": "packages/a.yaml"},
    {"id": 7, "op": "file.list", "path": "packages"},
    {"id": 8, "op": "ws.send", "session": "s", "msg": {"type": "get_states"}},
]


def test_pause_blocks_every_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_ROOT", tmp_path)
    got = run_against_homecraft(OPS, expect=len(OPS), paused=True)
    assert {g["id"]: g["error"]["code"] for g in got} == {o["id"]: "paused" for o in OPS}
    assert not (tmp_path / "packages").exists()
    assert {r["code"] for r in audit.recent()} == {"paused"}


def test_paused_survives_a_restart():
    assert state.load_paused() is False
    state.save_paused(True)
    assert state.load_paused() is True
    state.save_paused(False)
    assert state.load_paused() is False


def test_unreadable_state_is_not_paused(data_dir):
    (data_dir / "state.json").write_text("[1]")
    assert state.load_paused() is False
