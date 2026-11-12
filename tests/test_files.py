import pytest

from connector import files, settings
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
    assert files.list_dir("themes") == []
    assert files.read("configuration.yaml") == "default_config:\n"


def test_refused(root):
    for op, args in [
        (files.write, ("custom_components/x.py", "x")),
        (files.write, ("../secrets.yaml", "x")),
        (files.write, ("configuration.yaml", "x")),
        (files.read, ("secrets.yaml",)),
    ]:
        with pytest.raises(OpError) as e:
            op(*args)
        assert e.value.code == "not_allowed"
    assert not (root / "custom_components").exists()


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
        files.write("packages/x.yaml", "\ud800")
    assert files.list_dir("packages") == []


def test_symlink_inside_root_to_other_dir(root):
    (root / "custom_components").mkdir()
    (root / "packages" / "cc").symlink_to(root / "custom_components")
    with pytest.raises(OpError):
        files.write("packages/cc/x.py", "x")
    (root / "packages" / "conf.yaml").symlink_to(root / "configuration.yaml")
    with pytest.raises(OpError):
        files.write("packages/conf.yaml", "x")
    assert (root / "configuration.yaml").read_text() == "default_config:\n"
