import json
import logging
import os
import re
import secrets
import shutil
from collections.abc import Iterable, Sequence
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
CLIP_KEEP_FREE_BYTES = 5 * 1024**3
LEGACY_EVENT = "kitchen"

logger = logging.getLogger(__name__)

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
    event: str


class CaptureStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("could not create media directory %s", root)

    def new_id(self) -> str:
        return secrets.token_urlsafe(16)

    def create(
        self,
        capture_id: str,
        photo: bytes,
        clip_frames: Sequence[bytes],
        clip_fps: float,
        event: str,
    ) -> None:
        target = self._dir(capture_id)
        staging = self.root / f"{_TMP_PREFIX}{capture_id}"
        staging.mkdir()
        _write_durably(staging / PHOTO, [photo])
        _write_durably(staging / CLIP, clip_frames)
        meta = CaptureMeta(
            id=capture_id,
            captured_at=_utc_now(),
            state=CaptureState.PENDING,
            uploaded=False,
            clip_fps=clip_fps,
            event=event,
        )
        _write_durably(staging / META, [_dump(meta).encode("utf-8")])
        os.rename(staging, target)
        if os.name == "posix":
            _fsync_directory(self.root)

    def put_file(self, capture_id: str, name: str, source: Path) -> None:
        target = self.path(capture_id, name)
        os.replace(source, target)
        if os.name == "posix":
            try:
                _fsync_directory(target.parent)
            except OSError:
                logger.warning("could not sync the directory of %s; the file is in place", capture_id, exc_info=True)

    def path(self, capture_id: str, name: str) -> Path:
        return self._dir(capture_id) / name

    def exists(self, capture_id: str) -> bool:
        return self._dir(capture_id).is_dir()

    def meta(self, capture_id: str) -> CaptureMeta:
        data = json.loads(self.path(capture_id, META).read_text(encoding="utf-8"))
        data["state"] = CaptureState(data["state"])
        data.setdefault("event", LEGACY_EVENT)
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
        try:
            entries = list(self.root.iterdir())
        except OSError:
            logger.exception("could not list media directory %s", self.root)
            return
        for entry in entries:
            try:
                self._recover_entry(entry)
            except OSError:
                logger.warning("could not recover %s", entry.name, exc_info=True)

    def prune_clips(self, min_free_bytes: int = CLIP_KEEP_FREE_BYTES) -> list[str]:
        pruned: list[str] = []
        if not self._short_of_space(min_free_bytes):
            return pruned
        for capture_id in self._prunable_clips_oldest_first():
            try:
                self.path(capture_id, CLIP).unlink()
            except OSError:
                logger.warning("could not delete the clip of %s", capture_id, exc_info=True)
                continue
            logger.info("deleted the clip of %s to free disk space", capture_id)
            pruned.append(capture_id)
            if not self._short_of_space(min_free_bytes):
                break
        return pruned

    def _short_of_space(self, min_free_bytes: int) -> bool:
        try:
            return shutil.disk_usage(self.root).free < min_free_bytes
        except OSError:
            logger.exception("could not measure free space in %s", self.root)
            return False

    def _prunable_clips_oldest_first(self) -> list[str]:
        try:
            entries = list(self.root.iterdir())
        except OSError:
            logger.exception("could not list media directory %s", self.root)
            return []
        prunable: list[tuple[str, str]] = []
        for entry in entries:
            if not (entry.is_dir() and _ID_PATTERN.fullmatch(entry.name)):
                continue
            if not all((entry / name).is_file() for name in (CLIP, BOOMERANG, VIDEO)):
                continue
            try:
                prunable.append((self.meta(entry.name).captured_at, entry.name))
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("could not read %s while pruning clips", entry.name, exc_info=True)
        return [capture_id for _, capture_id in sorted(prunable)]

    def _recover_entry(self, entry: Path) -> None:
        if not entry.is_dir():
            return
        if entry.name.startswith(_TMP_PREFIX):
            shutil.rmtree(entry)
        elif _ID_PATTERN.fullmatch(entry.name):
            try:
                pending = self.meta(entry.name).state == CaptureState.PENDING
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                shutil.rmtree(entry)
                return
            if pending:
                self.keep(entry.name)

    def _dir(self, capture_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(capture_id):
            raise ValueError(f"invalid capture id: {capture_id!r}")
        return self.root / capture_id

    def _write_meta(self, meta: CaptureMeta) -> None:
        final = self.path(meta.id, META)
        staging = final.with_suffix(".json.tmp")
        _write_durably(staging, [_dump(meta).encode("utf-8")])
        os.replace(staging, final)


def _write_durably(path: Path, chunks: Iterable[bytes]) -> None:
    with path.open("wb") as file:
        for chunk in chunks:
            file.write(chunk)
        file.flush()
        os.fsync(file.fileno())


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _dump(meta: CaptureMeta) -> str:
    return json.dumps(asdict(meta))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
