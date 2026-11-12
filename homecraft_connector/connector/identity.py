"""The agent's identity: a UUID and an Ed25519 key, created on first start.

Homecraft keeps only the public key (from the pairing code); the private key
never leaves /data/agent_key (0600) and is never logged."""

import base64
import hashlib
import logging
import os
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import settings
from .files import write_private

logger = logging.getLogger(__name__)


def _create() -> tuple[uuid.UUID, Ed25519PrivateKey]:
    raw = os.urandom(32)
    agent_id = uuid.uuid4()
    # The key first: an id without its key would be useless after a crash.
    write_private(settings.DATA_DIR / "agent_key", raw)
    write_private(settings.DATA_DIR / "agent_id", str(agent_id).encode())
    return agent_id, Ed25519PrivateKey.from_private_bytes(raw)


def load_or_create() -> tuple[uuid.UUID, Ed25519PrivateKey]:
    id_path = settings.DATA_DIR / "agent_id"
    key_path = settings.DATA_DIR / "agent_key"
    if not key_path.exists():
        return _create()  # a new key is a new agent, whatever agent_id says
    try:
        raw = key_path.read_bytes()
        if len(raw) != 32:
            raise ValueError("key is not 32 bytes")
        agent_id = uuid.UUID(id_path.read_text().strip())
    except (OSError, ValueError) as e:
        logger.warning(
            "Identita agenta je poškozená (%s), vytvářím novou — je potřeba znovu spárovat",
            e,
        )
        return _create()
    return agent_id, Ed25519PrivateKey.from_private_bytes(raw)


def public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def pairing_code(agent_id: uuid.UUID, key: Ed25519PrivateKey) -> str:
    """base32 (no padding) of agent_id (16 B) + public key (32 B); the backend's
    `decode_pairing_code` reverses it."""
    return base64.b32encode(agent_id.bytes + public_raw(key)).decode().rstrip("=")


def public_b64(key: Ed25519PrivateKey) -> str:
    """The raw public key (32 B), base64: sent in the hello for a pairing request."""
    return base64.b64encode(public_raw(key)).decode()


def verification_code(key: Ed25519PrivateKey) -> str:
    """`XXX-XXX`: the first 6 base32 characters of sha256(public key). The panel
    shows it and Homecraft shows the same one next to the pairing request."""
    c = base64.b32encode(hashlib.sha256(public_raw(key)).digest()).decode()
    return f"{c[:3]}-{c[3:6]}"


def sign_hello(agent_id: uuid.UUID, key: Ed25519PrivateKey, nonce_b64: str) -> str:
    """The hello signature: Ed25519(nonce || agent_id), base64."""
    sig = key.sign(base64.b64decode(nonce_b64) + str(agent_id).encode())
    return base64.b64encode(sig).decode()
