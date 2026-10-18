import json
import logging
import os
import re
import secrets
import shutil
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

PHOTO = "photo.jpg"
CLIP = "clip.mjpeg"
BOOMERANG = "boomerang.mp4"
VIDEO = "video.mp4"
META = "capture.json"
SHARED_META = "meta.json"
VIDEOS = (BOOMERANG, VIDEO)
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
    """The metadata of one capture.

    `uploaded_files` names what has been uploaded, `failed_files` what the server rejected and
    will not be retried. `build_finished` is set once no boomerang or video will be added: the
    build has ended, or the process that ran it ended, since builds never resume after a
    restart. A video still missing then is missing for good, so `uploaded` means the photo,
    meta.json and every video that exists were uploaded once the build had finished.
    """

    id: str
    captured_at: str
    state: CaptureState
    uploaded: bool
    clip_fps: float
    event: str
    uploaded_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    build_finished: bool = False


class CaptureStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("could not create media directory %s", root)
        self._meta_lock = threading.RLock()

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

    def capture_ids(self) -> list[str]:
        try:
            entries = list(self.root.iterdir())
        except OSError:
            logger.exception("could not list media directory %s", self.root)
            return []
        return [entry.name for entry in entries if _ID_PATTERN.fullmatch(entry.name) and entry.is_dir()]

    def meta(self, capture_id: str) -> CaptureMeta:
        with self._meta_lock:
            data = json.loads(self.path(capture_id, META).read_text(encoding="utf-8"))
        data["state"] = CaptureState(data["state"])
        data.setdefault("event", LEGACY_EVENT)
        return CaptureMeta(**data)

    def keep(self, capture_id: str) -> None:
        self._update_meta(capture_id, _mark_kept)

    def finish_build(self, capture_id: str) -> None:
        self._update_meta(capture_id, _mark_build_finished)

    def record_upload(self, capture_id: str, name: str) -> None:
        self._update_meta(capture_id, lambda meta: _add(meta.uploaded_files, name))

    def record_upload_failure(self, capture_id: str, name: str) -> None:
        self._update_meta(capture_id, lambda meta: _add(meta.failed_files, name))

    def unuploaded(self, meta: CaptureMeta) -> list[str]:
        """The files of a capture still to upload, in upload order."""
        done = {*meta.uploaded_files, *meta.failed_files}
        return [name for name in self._uploadable(meta.id) if name not in done]

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
        prunable: list[tuple[str, str]] = []
        for capture_id in self.capture_ids():
            if not all(self.path(capture_id, name).is_file() for name in (CLIP, BOOMERANG, VIDEO)):
                continue
            try:
                prunable.append((self.meta(capture_id).captured_at, capture_id))
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("could not read %s while pruning clips", capture_id, exc_info=True)
        return [capture_id for _, capture_id in sorted(prunable)]

    def _recover_entry(self, entry: Path) -> None:
        if not entry.is_dir():
            return
        if entry.name.startswith(_TMP_PREFIX):
            shutil.rmtree(entry)
        elif _ID_PATTERN.fullmatch(entry.name):
            try:
                meta = self.meta(entry.name)
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                shutil.rmtree(entry)
                return
            if meta.state == CaptureState.PENDING or not meta.build_finished:
                self._update_meta(entry.name, lambda meta: (_mark_kept(meta), _mark_build_finished(meta)))

    def _dir(self, capture_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(capture_id):
            raise ValueError(f"invalid capture id: {capture_id!r}")
        return self.root / capture_id

    def _uploadable(self, capture_id: str) -> list[str]:
        return [PHOTO, SHARED_META, *(name for name in VIDEOS if self.path(capture_id, name).is_file())]

    def _update_meta(self, capture_id: str, change: Callable[[CaptureMeta], object]) -> None:
        """Read, change and rewrite the metadata as one step, so concurrent updates never undo each other."""
        with self._meta_lock:
            meta = self.meta(capture_id)
            change(meta)
            meta.uploaded = meta.build_finished and all(
                name in meta.uploaded_files for name in self._uploadable(capture_id)
            )
            final = self.path(capture_id, META)
            staging = final.with_suffix(".json.tmp")
            _write_durably(staging, [_dump(meta).encode("utf-8")])
            os.replace(staging, final)


def _mark_kept(meta: CaptureMeta) -> None:
    meta.state = CaptureState.KEPT


def _mark_build_finished(meta: CaptureMeta) -> None:
    meta.build_finished = True


def _add(names: list[str], name: str) -> None:
    if name not in names:
        names.append(name)


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
