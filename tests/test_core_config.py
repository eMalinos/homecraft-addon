import asyncio
import base64
import json

import pytest

from connector import core_config, settings
from connector.policy import OpError


@pytest.fixture
def root(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(settings, "CONFIG_ROOT", config)
    return config


class FakeHA:
    def __init__(self, result="valid", status=200):
        self.result, self.status, self.calls = result, status, []

    async def core(self, method, path):
        self.calls.append((method, path))
        body = json.dumps(
            {"result": self.result, "errors": None if self.result == "valid" else "bad"}
        )
        return {
            "status": self.status,
            "headers": {},
            "body_b64": base64.b64encode(body.encode()).decode(),
        }


def run(ha):
    return asyncio.run(core_config.packages_include(ha))


def test_adds_the_include_into_the_existing_block(root):
    (root / "configuration.yaml").write_text(
        "homeassistant:\n    name: Home\nhttp: !include http.yaml\n"
    )
    ha = FakeHA()
    assert run(ha) == "added"
    assert (root / "configuration.yaml").read_text() == (
        "homeassistant:\n    packages: !include_dir_named packages\n"
        "    name: Home\nhttp: !include http.yaml\n"
    )
    assert ha.calls == [("POST", "/api/config/core/check_config")]
    assert not list(root.glob("*.hfb-tmp"))


def test_adds_a_block_and_keeps_crlf(root):
    (root / "configuration.yaml").write_bytes(b"default_config:\r\n")
    assert run(FakeHA()) == "added"
    assert (root / "configuration.yaml").read_bytes() == (
        b"default_config:\r\n\r\nhomeassistant:\r\n  packages: !include_dir_named packages\r\n"
    )


def test_present_is_left_alone(root):
    text = "homeassistant:\n  packages: !include_dir_named packages\n"
    (root / "configuration.yaml").write_text(text)
    ha = FakeHA()
    assert run(ha) == "present"
    assert (root / "configuration.yaml").read_text() == text and ha.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "homeassistant: !include core.yaml\n",  # tagged, not a plain block
        "homeassistant: {name: x}\n",  # flow style
        "homeassistant:\n  packages: !include_dir_merge_named pkgs\n",  # an own packages:
        "homeassistant:\n  name: [\n",  # unparsable
    ],
)
def test_unsafe_configuration_is_not_touched(root, text):
    (root / "configuration.yaml").write_text(text)
    with pytest.raises(OpError) as e:
        run(FakeHA())
    assert e.value.code == "not_supported"
    assert (root / "configuration.yaml").read_text() == text


@pytest.mark.parametrize("ha", [FakeHA(result="invalid"), FakeHA(status=500)])
def test_rejected_by_ha_is_restored(root, ha):
    (root / "configuration.yaml").write_text("default_config:\n")
    with pytest.raises(OpError) as e:
        run(ha)
    assert e.value.code == "check_failed"
    assert (root / "configuration.yaml").read_text() == "default_config:\n"


def test_unreachable_ha_restores(root):
    class Down:
        async def core(self, method, path):
            raise OpError("ha_unreachable", "x")

    (root / "configuration.yaml").write_text("default_config:\n")
    with pytest.raises(OpError):
        run(Down())
    assert (root / "configuration.yaml").read_text() == "default_config:\n"


def test_missing_and_symlink(root, tmp_path):
    with pytest.raises(OpError) as e:
        run(FakeHA())
    assert e.value.code == "not_found"
    (tmp_path / "elsewhere.yaml").write_text("default_config:\n")
    (root / "configuration.yaml").symlink_to(tmp_path / "elsewhere.yaml")
    with pytest.raises(OpError) as e:
        run(FakeHA())
    assert e.value.code == "not_allowed"
    assert (tmp_path / "elsewhere.yaml").read_text() == "default_config:\n"


def test_keeps_permissions(root):
    path = root / "configuration.yaml"
    path.write_text("default_config:\n")
    path.chmod(0o640)
    run(FakeHA())
    assert path.stat().st_mode & 0o777 == 0o640
