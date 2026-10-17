from pathlib import Path

import pytest

from photobooth.config import Timings, load_config


def write_config(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL = """
share_base_url = "https://photo.example.invalid/p"
media_dir = "media"

[camera]
kind = "fake"
"""


def test_defaults_fill_missing_keys(tmp_path):
    config = load_config(write_config(tmp_path / "config.toml", MINIMAL))

    assert config.timings == Timings()
    assert config.timings.countdown_s == 3.0
    assert config.timings.delete_window_s == 15.0
    assert config.camera.kind == "fake"
    assert config.share_base_url == "https://photo.example.invalid/p"
    assert (config.host, config.port) == ("127.0.0.1", 8000)


def test_unknown_key_is_rejected(tmp_path):
    path = write_config(tmp_path / "config.toml", MINIMAL + "\n[timings]\ncountdwn_s = 2\n")

    with pytest.raises(ValueError, match="countdwn_s"):
        load_config(path)


def test_record_start_must_precede_shot(tmp_path):
    path = write_config(tmp_path / "config.toml", MINIMAL + "\n[timings]\nrecord_start_s = 3.0\n")

    with pytest.raises(ValueError, match="record_start_s"):
        load_config(path)


def test_media_dir_relative_to_config_file(tmp_path):
    config = load_config(write_config(tmp_path / "cfg" / "config.toml", MINIMAL))

    assert config.media_dir == tmp_path / "cfg" / "media"
