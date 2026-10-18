from dataclasses import replace
from pathlib import Path

import pytest

from photobooth.config import Timings, ignored_remote_keys, load_config, with_remote


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
    assert (config.remote_config_url, config.config_poll_s) == (None, 60.0)


@pytest.mark.parametrize("top", ["config_poll_s = 0", 'config_poll_s = "60"', "remote_config_url = 1"])
def test_reload_keys_are_validated(tmp_path, top):
    path = write_config(tmp_path / "config.toml", top + "\n" + MINIMAL)

    with pytest.raises(ValueError, match=top.split(" ")[0]):
        load_config(path)


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


def test_a_non_numeric_timing_is_rejected(tmp_path):
    path = write_config(tmp_path / "config.toml", MINIMAL + '\n[timings]\nflash_s = "2"\n')

    with pytest.raises(ValueError, match="flash_s"):
        load_config(path)


def test_a_missing_required_key_is_a_value_error(tmp_path):
    path = write_config(tmp_path / "config.toml", MINIMAL.replace('media_dir = "media"', ""))

    with pytest.raises(ValueError, match="media_dir"):
        load_config(path)


@pytest.fixture
def local(tmp_path):
    timings = "\n[timings]\ncountdown_s = 5.0\nflash_s = 1.5\n"
    return load_config(write_config(tmp_path / "config.toml", MINIMAL + timings))


def test_remote_overrides_the_share_base_and_merges_into_the_local_timings(local):
    effective = with_remote(
        local, {"share_base_url": "https://remote.example.invalid/s", "timings": {"countdown_s": 4.0}}
    )

    assert effective.share_base_url == "https://remote.example.invalid/s"
    assert effective.timings == replace(local.timings, countdown_s=4.0)
    assert effective.timings.flash_s == 1.5
    assert replace(effective, share_base_url=local.share_base_url, timings=local.timings) == local


def test_remote_keys_other_than_share_base_and_timings_are_named_and_not_applied(local):
    document = {"camera": {"kind": "ffmpeg"}, "port": 9000, "share_base_url": "https://remote.example.invalid/s"}

    assert ignored_remote_keys(document) == ["camera", "port"]
    effective = with_remote(local, document)
    assert (effective.camera, effective.port) == (local.camera, local.port)


@pytest.mark.parametrize(
    ("document", "named"),
    [
        ({"timings": {"countdwn_s": 2}}, "countdwn_s"),
        ({"timings": {"countdown_s": 0.5}}, "record_start_s"),
        ({"timings": {"countdown_s": "4"}}, "countdown_s"),
        ({"timings": 4}, "timings"),
        ({"share_base_url": 5}, "share_base_url"),
    ],
)
def test_remote_settings_are_validated_like_the_local_file(local, document, named):
    with pytest.raises(ValueError, match=named):
        with_remote(local, document)
