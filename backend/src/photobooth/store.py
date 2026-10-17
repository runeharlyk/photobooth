import json
import os
import re
import secrets
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

PHOTO = "photo.jpg"
CLIP = "clip.mjpeg"
BOOMERANG = "boomerang.mp4"
VIDEO = "video.mp4"
META = "capture.json"
PUBLIC_FILES = (PHOTO, BOOMERANG, VIDEO)

_TMP_PREFIX = ".tmp-"
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22}")


class CaptureState(StrEnum):
    PENDING = "pending"
    KEPT = "kept"


@dataclass
class CaptureMeta:
    id: str
    captured_at: str
    state: CaptureState
    uploaded: bool
    clip_fps: float


class CaptureStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return secrets.token_urlsafe(16)

    def create(
        self,
        capture_id: str,
        photo: bytes,
        clip_frames: Sequence[bytes],
        clip_fps: float,
    ) -> None:
        target = self._dir(capture_id)
        staging = self.root / f"{_TMP_PREFIX}{capture_id}"
        staging.mkdir()
        (staging / PHOTO).write_bytes(photo)
        with (staging / CLIP).open("wb") as clip:
            for frame in clip_frames:
                clip.write(frame)
        meta = CaptureMeta(
            id=capture_id,
            captured_at=_utc_now(),
            state=CaptureState.PENDING,
            uploaded=False,
            clip_fps=clip_fps,
        )
        (staging / META).write_text(_dump(meta), encoding="utf-8")
        os.rename(staging, target)

    def put_file(self, capture_id: str, name: str, source: Path) -> None:
        os.replace(source, self.path(capture_id, name))

    def path(self, capture_id: str, name: str) -> Path:
        return self._dir(capture_id) / name

    def exists(self, capture_id: str) -> bool:
        return self._dir(capture_id).is_dir()

    def meta(self, capture_id: str) -> CaptureMeta:
        data = json.loads(self.path(capture_id, META).read_text(encoding="utf-8"))
        data["state"] = CaptureState(data["state"])
        return CaptureMeta(**data)

    def keep(self, capture_id: str) -> None:
        meta = self.meta(capture_id)
        meta.state = CaptureState.KEPT
        self._write_meta(meta)

    def delete(self, capture_id: str) -> None:
        target = self._dir(capture_id)
        doomed = self.root / f"{_TMP_PREFIX}{capture_id}"
        try:
            os.rename(target, doomed)
        except FileNotFoundError:
            return
        shutil.rmtree(doomed)

    def recover(self) -> None:
        for entry in self.root.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith(_TMP_PREFIX):
                shutil.rmtree(entry)
            elif _ID_PATTERN.fullmatch(entry.name):
                if self.meta(entry.name).state == CaptureState.PENDING:
                    self.keep(entry.name)

    def _dir(self, capture_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(capture_id):
            raise ValueError(f"invalid capture id: {capture_id!r}")
        return self.root / capture_id

    def _write_meta(self, meta: CaptureMeta) -> None:
        final = self.path(meta.id, META)
        staging = final.with_suffix(".json.tmp")
        staging.write_text(_dump(meta), encoding="utf-8")
        os.replace(staging, final)


def _dump(meta: CaptureMeta) -> str:
    return json.dumps(asdict(meta))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
