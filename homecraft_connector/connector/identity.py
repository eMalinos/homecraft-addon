"""The agent's identity: a UUID and an Ed25519 key, created on first start.

Homecraft keeps only the public key (from the pairing code); the private key
never leaves /data/agent_key (0600) and is never logged."""

import base64
import os
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import settings


def _write_new(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def load_or_create() -> tuple[uuid.UUID, Ed25519PrivateKey]:
    id_path = settings.DATA_DIR / "agent_id"
    key_path = settings.DATA_DIR / "agent_key"
    # The key first: an id without its key would be useless after a crash.
    if not key_path.exists():
        _write_new(key_path, os.urandom(32), 0o600)
        id_path.unlink(missing_ok=True)  # a new key is a new agent
    if not id_path.exists():
        _write_new(id_path, str(uuid.uuid4()).encode(), 0o644)
    key = Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
    return uuid.UUID(id_path.read_text().strip()), key


def public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def pairing_code(agent_id: uuid.UUID, key: Ed25519PrivateKey) -> str:
    """base32 (no padding) of agent_id (16 B) + public key (32 B); the backend's
    `decode_pairing_code` reverses it."""
    return base64.b32encode(agent_id.bytes + public_raw(key)).decode().rstrip("=")


def sign_hello(agent_id: uuid.UUID, key: Ed25519PrivateKey, nonce_b64: str) -> str:
    """The hello signature: Ed25519(nonce || agent_id), base64."""
    sig = key.sign(base64.b64decode(nonce_b64) + str(agent_id).encode())
    return base64.b64encode(sig).decode()
