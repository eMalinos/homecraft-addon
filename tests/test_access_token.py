import hashlib
import stat

import pytest

from connector import access_token


def test_save_load_clear(data_dir):
    assert access_token.load() == (None, False)
    access_token.save("abc.access:s3cr:et")
    assert access_token.load() == (("abc.access", "s3cr:et"), False)  # split on the first ':'
    assert stat.S_IMODE((data_dir / "cf_access.json").stat().st_mode) == 0o600
    access_token.clear()
    assert access_token.active() is None
    access_token.clear()  # clearing twice is fine


def test_parts_are_stripped():
    access_token.save(" abc.access : secret\n")
    assert access_token.active() == ("abc.access", "secret")


@pytest.mark.parametrize(
    "token",
    ["", "noseparator", ":secret", "id:", " : ", "id:sec\r\nX-Evil: 1", "id:sé", "i d:secret"],
)
def test_invalid_format(token, data_dir):
    with pytest.raises(ValueError):
        access_token.save(token)
    with pytest.raises(ValueError):
        access_token.stage(token)
    assert list(data_dir.iterdir()) == []


def test_corrupt_file_reads_as_missing(data_dir):
    (data_dir / "cf_access.json").write_text("{")
    assert access_token.active() is None


def test_fingerprint_covers_the_secret():
    fp = access_token.fingerprint(("id", "s1"))
    assert fp == hashlib.sha256(b"id:s1").hexdigest()[:16]
    assert fp != access_token.fingerprint(("id", "s2"))


def test_stage_and_commit(data_dir):
    access_token.save("old:s1")
    assert access_token.stage(" new : s2 ") is True
    assert access_token.load() == (("new", "s2"), True)  # staged first
    assert access_token.active() == ("old", "s1")
    assert stat.S_IMODE((data_dir / "cf_access.pending.json").stat().st_mode) == 0o600
    assert access_token.updated_by_homecraft() is None  # set on commit, not on stage
    assert access_token.commit(("new", "s2")) is True
    assert access_token.load() == (("new", "s2"), False) and not access_token.pending()
    assert stat.S_IMODE((data_dir / "cf_access.json").stat().st_mode) == 0o600
    assert access_token.updated_by_homecraft() is not None


def test_stage_is_idempotent(data_dir):
    access_token.save("old:s1")
    assert access_token.stage("new:s2") is True
    mtime = (data_dir / "cf_access.pending.json").stat().st_mtime_ns
    assert access_token.stage("new:s2") is False  # no rewrite
    assert (data_dir / "cf_access.pending.json").stat().st_mtime_ns == mtime
    assert access_token.stage("old:s1") is False  # the active one: nothing staged
    assert not access_token.pending()


def test_commit_only_the_token_that_was_used():
    access_token.save("old:s1")
    access_token.stage("new:s2")
    access_token.save("mine:s3")  # the panel wins and drops the staged one
    assert access_token.commit(("new", "s2")) is False
    assert access_token.active() == ("mine", "s3")
    access_token.stage("new:s2")
    access_token.stage("newer:s4")
    assert access_token.commit(("new", "s2")) is False
    assert access_token.load() == (("newer", "s4"), True)


def test_clear_drops_a_staged_token():
    access_token.stage("new:s2")
    access_token.clear()
    assert access_token.load() == (None, False)
