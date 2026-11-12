import asyncio
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from connector.client import Backoff, Client


def test_outage_doubles_up_to_a_minute():
    b = Backoff()
    assert [b.next(refused=False) for _ in range(9)] == [1, 2, 4, 8, 16, 32, 60, 60, 60]


def test_refusal_slows_to_hourly():
    b = Backoff()
    minutes = [b.next(refused=True) / 60 for _ in range(6)]
    assert minutes == [1, 5, 15, 60, 60, 60]


def test_reset_starts_over():
    b = Backoff()
    b.next(refused=False), b.next(refused=True), b.next(refused=True)
    b.reset()
    assert (b.next(refused=False), b.next(refused=True)) == (1, 60)


def test_reconnect_now_cuts_the_wait():
    async def scenario():
        client = Client("ws://x", uuid.uuid4(), Ed25519PrivateKey.generate(), "0.1.0")
        client.backoff.next(refused=True)
        start = time.monotonic()
        asyncio.get_running_loop().call_later(0.05, client.reconnect_now)
        await client.wait(3600)
        assert time.monotonic() - start < 1
        assert client.backoff.next(refused=True) == 60  # backoff reset too

    asyncio.run(scenario())
