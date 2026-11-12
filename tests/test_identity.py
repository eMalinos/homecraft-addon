import base64
import binascii
import stat
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from connector import identity, settings


def decode_pairing_code(code: str) -> tuple[str, str]:
    """Copy of the backend's `infrastructure.agent_hub.decode_pairing_code`."""
    text = "".join(code.split()).replace("-", "").upper()
    try:
        raw = base64.b32decode(text + "=" * (-len(text) % 8))
        if len(raw) != 48:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(raw[16:])
    except (ValueError, binascii.Error):
        raise ValueError("Invalid pairing code") from None
    return str(uuid.UUID(bytes=raw[:16])), base64.b64encode(raw[16:]).decode()


def test_pairing_code_round_trip():
    agent_id, key = identity.load_or_create()
    decoded_id, public_b64 = decode_pairing_code(identity.pairing_code(agent_id, key))
    assert decoded_id == str(agent_id)
    assert base64.b64decode(public_b64) == identity.public_raw(key)


def test_key_persists_and_is_private(data_dir):
    agent_id, key = identity.load_or_create()
    again_id, again_key = identity.load_or_create()
    assert again_id == agent_id
    assert identity.public_raw(again_key) == identity.public_raw(key)
    assert stat.S_IMODE((data_dir / "agent_key").stat().st_mode) == 0o600


def test_new_key_means_new_agent(data_dir):
    agent_id, _ = identity.load_or_create()
    (data_dir / "agent_key").unlink()
    assert identity.load_or_create()[0] != agent_id


def test_hello_signature_verifies_like_the_hub():
    agent_id, key = identity.load_or_create()
    nonce = b"\x01" * 32
    sig = identity.sign_hello(agent_id, key, base64.b64encode(nonce).decode())
    # AgentHub._handshake: verify(sig, nonce + agent_id.encode())
    Ed25519PublicKey.from_public_bytes(identity.public_raw(key)).verify(
        base64.b64decode(sig, validate=True), nonce + str(agent_id).encode()
    )


def test_corrupt_identity_is_regenerated(data_dir):
    agent_id, _ = identity.load_or_create()
    (data_dir / "agent_key").write_bytes(b"short")
    new_id, key = identity.load_or_create()
    assert new_id != agent_id
    assert len((data_dir / "agent_key").read_bytes()) == 32
    (data_dir / "agent_id").write_text("not-a-uuid")
    assert identity.load_or_create()[0] not in (agent_id, new_id)


def test_version_from_build(monkeypatch):
    monkeypatch.setenv("CONNECTOR_VERSION", "0.1.0")
    assert settings.version() == "0.1.0"
    monkeypatch.delenv("CONNECTOR_VERSION")
    assert settings.version() == "dev"
