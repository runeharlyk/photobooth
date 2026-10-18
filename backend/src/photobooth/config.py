import math
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Literal, get_args, get_origin

from photobooth.redact import scheme_and_host

DEFAULT_EVENT = "kitchen"
REMOTE_KEYS = {"share_base_url", "event", "timings"}
_EVENT_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_UPLOAD_KEYS = ("url", "token")
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_PLAIN_HTTP_HOSTS = ("127.0.0.1", "localhost")
TOP_LEVEL_TYPES = {
    "share_base_url": str,
    "event": str,
    "media_dir": str,
    "host": str,
    "port": int,
    "remote_config_url": str,
    "config_poll_s": float,
}


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
class UploadConfig:
    """Where kept captures are uploaded: the sharing Worker's origin and its bearer token."""

    url: str
    token: str = field(repr=False)


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
    event: str = DEFAULT_EVENT
    upload: UploadConfig | None = None


@dataclass
class LiveSettings:
    """The settings that apply without a restart, replaced in place when the config is reloaded."""

    timings: Timings
    share_base_url: str
    event: str


def load_config(path: Path) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    camera_table = _table(data.pop("camera", {}), "[camera]")
    timings_table = _table(data.pop("timings", {}), "[timings]")
    upload_table = data.pop("upload", None)
    _validate(data, TOP_LEVEL_TYPES, "top level")
    _validate(camera_table, _field_types(CameraConfig), "[camera]")
    _require(data, "share_base_url", "top level")
    _require(data, "media_dir", "top level")
    _require(camera_table, "kind", "[camera]")
    if "config_poll_s" in data and not (math.isfinite(data["config_poll_s"]) and data["config_poll_s"] > 0):
        raise ValueError(f"config_poll_s must be a positive finite number, not {data['config_poll_s']!r}")
    if "event" in data:
        _check_event(data["event"], "top level")

    return Config(
        camera=CameraConfig(**camera_table),
        timings=_timings(Timings(), timings_table),
        share_base_url=data["share_base_url"],
        media_dir=(path.parent / data["media_dir"]).resolve(),
        upload=None if upload_table is None else _upload(upload_table),
        **{key: data[key] for key in ("event", "host", "port", "remote_config_url", "config_poll_s") if key in data},
    )


def with_remote(config: Config, document: dict) -> Config:
    """The config with the share base, event and timings of a remote document laid over it."""
    share_base_url = document.get("share_base_url", config.share_base_url)
    _check("share_base_url", share_base_url, str, "top level")
    event = document.get("event", config.event)
    _check_event(event, "top level")
    timings = _timings(config.timings, _table(document.get("timings", {}), "[timings]"))
    return replace(config, share_base_url=share_base_url, event=event, timings=timings)


def ignored_remote_keys(document: dict) -> list[str]:
    return sorted(set(document) - REMOTE_KEYS)


def _upload(table: object) -> UploadConfig:
    """Validates [upload] without quoting its values: the token is secret, and so may be anything in the URL."""
    if not isinstance(table, dict):
        raise ValueError("upload must be a table")
    unknown = sorted(set(table) - set(_UPLOAD_KEYS))
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r} in [upload]")
    for key in _UPLOAD_KEYS:
        _require(table, key, "[upload]")
    url, token = table["url"], table["token"]
    if not isinstance(url, str) or not _is_upload_origin(url):
        shown = scheme_and_host(url) if isinstance(url, str) else "a value that is not a string"
        raise ValueError(
            f"url in [upload] must be an https:// origin (http:// only for 127.0.0.1 or localhost) "
            f"with no path, query or credentials, not {shown}"
        )
    if not isinstance(token, str) or not _BEARER_TOKEN_PATTERN.fullmatch(token):
        raise ValueError(
            "token in [upload] must be a bearer token (RFC 6750): letters, digits and . _ ~ + / -, "
            "optionally ending in ="
        )
    return UploadConfig(url.rstrip("/"), token)


def _is_upload_origin(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    try:
        parts.port
    except ValueError:
        return False
    secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _PLAIN_HTTP_HOSTS)
    bare = parts.path in ("", "/") and not (parts.query or parts.fragment or parts.username or parts.password)
    return secure and bool(parts.hostname) and bare


def _timings(base: Timings, table: dict) -> Timings:
    _validate(table, _field_types(Timings), "[timings]")
    for key, value in table.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} in [timings] must be finite and not negative, not {value!r}")
    timings = replace(base, **table)
    if timings.flash_opacity > 1:
        raise ValueError(f"flash_opacity ({timings.flash_opacity}) must be between 0 and 1")
    if timings.record_start_s >= timings.countdown_s:
        raise ValueError(
            f"record_start_s ({timings.record_start_s}) must be less than "
            f"countdown_s ({timings.countdown_s})"
        )
    return timings


def _check_event(value: object, where: str) -> None:
    _check("event", value, str, where)
    if not _EVENT_PATTERN.fullmatch(value):
        raise ValueError(
            f"event in {where} must be 1-40 lowercase letters, digits or hyphens, "
            f"starting with a letter or digit, not {value!r}"
        )


def _validate(table: dict, types: dict[str, object], where: str) -> None:
    unknown = sorted(set(table) - set(types))
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r} in {where}")
    for key, value in table.items():
        _check(key, value, types[key], where)


def _check(key: str, value: object, expected: object, where: str) -> None:
    if get_origin(expected) is Literal:
        if value not in get_args(expected):
            choices = ", ".join(map(repr, get_args(expected)))
            raise ValueError(f"{key} in {where} must be one of {choices}, not {value!r}")
    elif expected is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{key} in {where} must be a number, not {value!r}")
        try:
            float(value)
        except OverflowError:
            raise ValueError(f"{key} in {where} is too large") from None
    elif isinstance(value, bool) or not isinstance(value, expected):
        raise ValueError(f"{key} in {where} must be of type {expected.__name__}, not {value!r}")


def _field_types(cls: type) -> dict[str, object]:
    return {field.name: field.type for field in fields(cls)}


def _table(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a table, not {value!r}")
    return value


def _require(table: dict, key: str, where: str) -> None:
    if key not in table:
        raise ValueError(f"missing key {key!r} in {where}")
