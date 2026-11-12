import stat

import pytest

from connector import access_token


def test_save_load_clear(data_dir):
    assert access_token.load() is None
    access_token.save("abc.access:s3cr:et")
    assert access_token.load() == ("abc.access", "s3cr:et")  # split on the first ':'
    assert stat.S_IMODE((data_dir / "cf_access.json").stat().st_mode) == 0o600
    access_token.clear()
    assert access_token.load() is None
    access_token.clear()  # clearing twice is fine


def test_parts_are_stripped():
    access_token.save(" abc.access : secret\n")
    assert access_token.load() == ("abc.access", "secret")


@pytest.mark.parametrize(
    "token",
    ["", "noseparator", ":secret", "id:", " : ", "id:sec\r\nX-Evil: 1", "id:sé", "i d:secret"],
)
def test_invalid_format(token, data_dir):
    with pytest.raises(ValueError):
        access_token.save(token)
    assert not (data_dir / "cf_access.json").exists()


def test_corrupt_file_reads_as_missing(data_dir):
    (data_dir / "cf_access.json").write_text("{")
    assert access_token.load() is None


def test_stage_commit(data_dir):
    access_token.save("old:s1")
    access_token.stage(" new : s2 ")
    assert access_token.pending() and access_token.load() == ("new", "s2")  # staged first
    assert access_token.updated_by_homecraft() is None  # the active one is from the panel
    assert stat.S_IMODE((data_dir / "cf_access.pending.json").stat().st_mode) == 0o600
    access_token.commit()
    assert not access_token.pending() and access_token.load() == ("new", "s2")
    assert stat.S_IMODE((data_dir / "cf_access.json").stat().st_mode) == 0o600
    assert access_token.updated_by_homecraft() is not None


def test_discard_keeps_the_active_token():
    access_token.save("old:s1")
    access_token.stage("new:s2")
    access_token.discard()
    assert access_token.load() == ("old", "s1") and not access_token.pending()


def test_panel_save_and_clear_drop_a_staged_token():
    access_token.stage("new:s2")
    access_token.save("mine:s3")
    assert access_token.load() == ("mine", "s3")
    access_token.stage("new:s2")
    access_token.clear()
    assert access_token.load() is None


def test_stage_validates_like_save(data_dir):
    with pytest.raises(ValueError):
        access_token.stage("id:sec\r\nX-Evil: 1")
    assert not (data_dir / "cf_access.pending.json").exists()
