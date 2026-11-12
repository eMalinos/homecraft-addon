import pytest

from connector import settings


@pytest.mark.parametrize(
    "url",
    [
        "ws://192.168.1.20:8000/api/agent/ws",
        "ws://10.0.0.5/api/agent/ws",
        "ws://172.16.3.4:8000/api/agent/ws",
        "ws://[fd12:3456::1]:8000/api/agent/ws",
        "ws://[fe80::1]/api/agent/ws",
    ],
)
def test_private_ip_allowed(url):
    assert settings.lan_ws(url)


@pytest.mark.parametrize(
    "url",
    [
        "ws://8.8.8.8/api/agent/ws",  # public
        "ws://[2001:4860::8888]/api/agent/ws",  # public
        "ws://homecraft.lan/api/agent/ws",  # hostname
        "ws://localhost:8000/api/agent/ws",  # hostname
        "ws://127.0.0.1:8000/api/agent/ws",  # loopback
        "ws://[::1]:8000/api/agent/ws",  # loopback
        "ws://[::ffff:127.0.0.1]/api/agent/ws",  # loopback, IPv4-mapped
        "ws://0.0.0.0/api/agent/ws",  # unspecified
        "wss://192.168.1.20/api/agent/ws",  # wss needs no exception
        "http://192.168.1.20/api/agent/ws",
    ],
)
def test_other_refused(url):
    assert not settings.lan_ws(url)
