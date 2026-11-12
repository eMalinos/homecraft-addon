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


@pytest.mark.parametrize("token", ["", "noseparator", ":secret", "id:", " : "])
def test_invalid_format(token, data_dir):
    with pytest.raises(ValueError):
        access_token.save(token)
    assert not (data_dir / "cf_access.json").exists()


def test_corrupt_file_reads_as_missing(data_dir):
    (data_dir / "cf_access.json").write_text("{")
    assert access_token.load() is None
