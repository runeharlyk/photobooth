import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class CameraConfig:
    kind: Literal["fake", "ffmpeg"]
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


def load_config(path: Path) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    camera_table = data.pop("camera", {})
    timings_table = data.pop("timings", {})
    _reject_unknown(data, {"share_base_url", "media_dir", "host", "port"}, "top level")
    _reject_unknown(camera_table, _field_names(CameraConfig), "[camera]")
    _reject_unknown(timings_table, _field_names(Timings), "[timings]")

    timings = Timings(**timings_table)
    if timings.record_start_s >= timings.countdown_s:
        raise ValueError(
            f"record_start_s ({timings.record_start_s}) must be less than "
            f"countdown_s ({timings.countdown_s})"
        )

    return Config(
        camera=CameraConfig(**camera_table),
        timings=timings,
        share_base_url=data["share_base_url"],
        media_dir=(path.parent / data["media_dir"]).resolve(),
        **{key: data[key] for key in ("host", "port") if key in data},
    )


def _field_names(cls: type) -> set[str]:
    return {field.name for field in fields(cls)}


def _reject_unknown(table: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r} in {where}")
