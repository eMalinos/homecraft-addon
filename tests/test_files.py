import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from connector import files, policy, settings
from connector.policy import OpError


@pytest.fixture
def root(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "packages").mkdir(parents=True)
    (config / "configuration.yaml").write_text("default_config:\n")
    monkeypatch.setattr(settings, "CONFIG_ROOT", config)
    return config


def test_write_read_list_delete(root):
    files.write("packages/sub/hfb.yaml", "a: 1\n")
    assert files.read("packages/sub/hfb.yaml") == "a: 1\n"
    assert files.list_dir("packages") == ["sub"]
    assert files.list_dir("packages/sub") == ["hfb.yaml"]  # no leftover .hfb-tmp
    files.delete("packages/sub/hfb.yaml")
    files.delete("packages/sub/hfb.yaml")  # missing: still fine
    assert files.read("packages/sub/hfb.yaml") is None


def test_missing(root):
    assert files.read("packages/nope.yaml") is None
    assert files.list_dir("custom_templates") == []
    assert files.read("configuration.yaml") == "default_config:\n"


def test_refused(root):
    for op, args in [
        (files.write, ("custom_components/x.py", "x")),
        (files.write, ("../secrets.yaml", "x")),
        (files.write, ("configuration.yaml", "x")),
        (files.read, ("secrets.yaml",)),
        (files.write, ("www/other.js", "x")),
    ]:
        with pytest.raises(OpError) as e:
            op(*args)
        assert e.value.code == "not_allowed"
    assert not (root / "custom_components").exists()
    assert not (root / "www").exists()


def test_card_write_creates_www(root, card_key):
    path = "www/homecraft/heating-card.js"
    big = "x" * 600_000
    assert files.write(path, big, card_key(big)) == "www_created"
    assert files.write(path, "y", card_key("y")) is None
    assert files.list_dir("www/homecraft") == ["heating-card.js"]  # no leftover .hfb-tmp
    assert (root / path).read_text() == "y"
    files.delete(path)
    assert files.list_dir("www/homecraft") == []
    assert files.write("packages/x.yaml", "a: 1\n") is None


def test_card_write_needs_a_valid_signature(root, card_key):
    path, text = "www/homecraft/heating-card.js", "x"
    other_key = Ed25519PrivateKey.generate()
    for args in [
        (),
        (None,),
        (123,),
        ("!!",),
        (base64.b64encode(b"\0" * 63).decode(),),
        (card_key("y"),),
        (base64.b64encode(other_key.sign(text.encode())).decode(),),
    ]:
        with pytest.raises(OpError) as e:
            files.write(path, text, *args)
        assert e.value.code == "refused_content"
    assert not (root / "www").exists()


def test_card_write_accepts_any_pinned_key(root, card_key, monkeypatch):
    second = Ed25519PrivateKey.generate()
    keys = (*policy.CARD_PUBLIC_KEYS, second.public_key())
    monkeypatch.setattr(policy, "CARD_PUBLIC_KEYS", keys)
    sig = base64.b64encode(second.sign(b"x")).decode()
    assert files.write("www/homecraft/heating-card.js", "x", sig) == "www_created"


def test_card_signature_tolerates_surrounding_whitespace(root, card_key):
    assert files.write("www/homecraft/heating-card.js", "x", card_key("x") + "\n") == "www_created"


def test_link_into_card_dir_needs_a_signature(root, card_key):
    (root / "www" / "homecraft").mkdir(parents=True)
    card = root / "www" / "homecraft" / "heating-card.js"
    (root / "packages" / "x.js").symlink_to(card)
    with pytest.raises(OpError) as e:
        files.write("packages/x.js", "a: 1\n")
    assert e.value.code == "refused_content"
    assert not card.exists()
    files.write("packages/x.js", "a: 1\n", card_key("a: 1\n"))
    assert card.read_text() == "a: 1\n"


def test_signature_ignored_outside_cards(root):
    assert files.write("packages/x.yaml", "a: 1\n", "garbage") is None


def test_pinned_keys_are_valid():
    for key in policy.CARD_SIGNING_KEYS:
        raw = base64.b64decode(key, validate=True)
        assert len(raw) == 32
        Ed25519PublicKey.from_public_bytes(raw)
    assert len(policy.CARD_PUBLIC_KEYS) == len(policy.CARD_SIGNING_KEYS)


def test_card_dir_symlink_out_of_root(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "www").mkdir()
    (root / "www" / "homecraft").symlink_to(outside)
    with pytest.raises(OpError) as e:
        files.write("www/homecraft/heating-card.js", "x")
    assert e.value.code == "not_allowed"
    assert list(outside.iterdir()) == []


def test_write_too_large(root):
    big = "x" * (files.MAX_FILE + 1)
    with pytest.raises(OpError) as e:
        files.write("packages/x.yaml", big)
    assert e.value.code == "too_large"
    assert not (root / "packages" / "x.yaml").exists()
    with pytest.raises(OpError) as e:
        files.write("custom_components/x.py", big)
    assert e.value.code == "not_allowed"  # the path first, at any size


def test_symlink_out_of_root(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "packages" / "out").symlink_to(outside)
    with pytest.raises(OpError):
        files.write("packages/out/x.yaml", "x")
    assert not (outside / "x.yaml").exists()


def test_stale_tmp_link_is_replaced_not_followed(root, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("keep\n")
    (root / "packages" / "x.yaml.hfb-tmp").symlink_to(outside)
    files.write("packages/x.yaml", "new\n")
    assert outside.read_text() == "keep\n"
    assert (root / "packages" / "x.yaml").read_text() == "new\n"
    assert not (root / "packages" / "x.yaml").is_symlink()
    assert files.list_dir("packages") == ["x.yaml"]


def test_failed_write_leaves_no_tmp(root):
    with pytest.raises(UnicodeEncodeError):
        files.write("custom_templates/x.jinja", "\ud800")
    assert files.list_dir("custom_templates") == []


def test_symlink_inside_root_to_other_dir(root):
    (root / "custom_components").mkdir()
    (root / "packages" / "cc").symlink_to(root / "custom_components")
    with pytest.raises(OpError):
        files.write("packages/cc/x.py", "x")
    (root / "packages" / "conf.yaml").symlink_to(root / "configuration.yaml")
    with pytest.raises(OpError):
        files.write("packages/conf.yaml", "x")
    assert (root / "configuration.yaml").read_text() == "default_config:\n"


def test_package_content_is_checked_before_writing(root):
    with pytest.raises(OpError) as e:
        files.write("packages/x.yaml", "shell_command:\n  x: 'true'\n")
    assert e.value.code == "refused_content"
    assert (
        e.value.message == "packages/x.yaml: shell_command není přes Homecraft Connector povolený"
    )
    assert list((root / "packages").iterdir()) == []  # no file, no .hfb-tmp
    files.write("themes/t.yaml", "t:\n  http: x\n")  # themes run no code: not checked


def test_package_reached_through_a_link_is_checked(root):
    (root / "themes").mkdir()
    (root / "themes" / "pk").symlink_to(root / "packages")
    with pytest.raises(OpError):
        files.write("themes/pk/x.yaml", "shell_command: {}\n")
    assert not (root / "packages" / "x.yaml").exists()


def test_theme_tags_and_secrets_are_refused(root):
    for path, text in [
        ("themes/t.yaml", "t: !secret x\n"),
        ("themes/t.yaml", "t: !include ../secrets.yaml\n"),
        ("themes/t.yaml", "t: !env_var X\n"),
        ("themes/secrets.yaml", "x: y\n"),
        ("packages/secrets.yaml", "x: y\n"),
        ("custom_templates/secrets.yaml", "x: y\n"),
    ]:
        with pytest.raises(OpError) as e:
            files.write(path, text)
        assert e.value.code == "refused_content", path
    assert not (root / "themes" / "t.yaml").exists()
    files.write("custom_templates/x.jinja", "{% macro m() %}!secret{% endmacro %}\n")
