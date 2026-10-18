import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

REMOTE_KEYS = {"share_base_url", "timings"}


@dataclass(frozen=True)
class CameraConfig:
    kind: Literal["fake", "ffmpeg", "picamera2"]
    device: str = ""
    input_format: str = "dshow"
    video_size: str = "1920x1080"
    framerate: int = 30


@dataclass(frozen=True)
class Timings:
    countdown_s: float = 3.0
    record_start_s: float = 1.0
    flash_s: float = 2.0
    flash_opacity: float = 0.95
    boomerang_s: float = 1.0
    delete_window_s: float = 15.0
    result_timeout_s: float = 45.0
    message_s: float = 4.0


@dataclass(frozen=True)
class Config:
    camera: CameraConfig
    timings: Timings
    share_base_url: str
    media_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    remote_config_url: str | None = None
    config_poll_s: float = 60.0


@dataclass
class LiveSettings:
    """The settings that apply without a restart, replaced in place when the config is reloaded."""

    timings: Timings
    share_base_url: str


def load_config(path: Path) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    camera_table = _table(data.pop("camera", {}), "[camera]")
    timings_table = _table(data.pop("timings", {}), "[timings]")
    _reject_unknown(
        data, {"share_base_url", "media_dir", "host", "port", "remote_config_url", "config_poll_s"}, "top level"
    )
    _reject_unknown(camera_table, _field_names(CameraConfig), "[camera]")
    _require(camera_table, "kind", "[camera]")
    _require(data, "media_dir", "top level")
    if "remote_config_url" in data and not isinstance(data["remote_config_url"], str):
        raise ValueError(f"remote_config_url must be a string, not {data['remote_config_url']!r}")
    if "config_poll_s" in data and not (_is_number(data["config_poll_s"]) and data["config_poll_s"] > 0):
        raise ValueError(f"config_poll_s must be a positive number, not {data['config_poll_s']!r}")

    return Config(
        camera=CameraConfig(**camera_table),
        timings=_timings(Timings(), timings_table),
        share_base_url=_share_base_url(data),
        media_dir=(path.parent / data["media_dir"]).resolve(),
        **{key: data[key] for key in ("host", "port", "remote_config_url", "config_poll_s") if key in data},
    )


def with_remote(config: Config, document: dict) -> Config:
    """The config with the share base and timings of a remote document laid over it."""
    share_base_url = _share_base_url(document) if "share_base_url" in document else config.share_base_url
    timings = _timings(config.timings, _table(document.get("timings", {}), "[timings]"))
    return replace(config, share_base_url=share_base_url, timings=timings)


def ignored_remote_keys(document: dict) -> list[str]:
    return sorted(set(document) - REMOTE_KEYS)


def _timings(base: Timings, table: dict) -> Timings:
    _reject_unknown(table, _field_names(Timings), "[timings]")
    for key, value in table.items():
        if not _is_number(value):
            raise ValueError(f"{key} in [timings] must be a number, not {value!r}")
    timings = replace(base, **table)
    if timings.record_start_s >= timings.countdown_s:
        raise ValueError(
            f"record_start_s ({timings.record_start_s}) must be less than "
            f"countdown_s ({timings.countdown_s})"
        )
    return timings


def _share_base_url(table: dict) -> str:
    _require(table, "share_base_url", "top level")
    value = table["share_base_url"]
    if not isinstance(value, str):
        raise ValueError(f"share_base_url must be a string, not {value!r}")
    return value


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _table(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a table, not {value!r}")
    return value


def _require(table: dict, key: str, where: str) -> None:
    if key not in table:
        raise ValueError(f"missing key {key!r} in {where}")


def _field_names(cls: type) -> set[str]:
    return {field.name for field in fields(cls)}


def _reject_unknown(table: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r} in {where}")
