import json
import logging
import os
import shutil
import types
from pathlib import Path

import pytest

from photobooth import store as store_module
from photobooth.store import (
    BOOMERANG,
    CLIP,
    CLIP_KEEP_FREE_BYTES,
    META,
    PHOTO,
    VIDEO,
    CaptureState,
    CaptureStore,
)


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path / "media")


def test_create_writes_complete_capture(store: CaptureStore):
    capture_id = store.new_id()
    frames = [b"frame-one", b"frame-two", b"frame-three"]

    store.create(capture_id, b"jpeg-bytes", frames, 17.25, "kitchen")

    assert store.exists(capture_id)
    assert store.path(capture_id, PHOTO).read_bytes() == b"jpeg-bytes"
    assert store.path(capture_id, CLIP).read_bytes() == b"".join(frames)
    meta = store.meta(capture_id)
    assert meta.id == capture_id
    assert meta.state == CaptureState.PENDING
    assert meta.uploaded is False
    assert meta.clip_fps == 17.25
    assert not list(store.root.glob(".tmp-*"))
    stored = json.loads(store.path(capture_id, META).read_text(encoding="utf-8"))
    assert stored["state"] == "pending"


def test_keep_and_delete(store: CaptureStore):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "kitchen")

    store.keep(capture_id)
    assert store.meta(capture_id).state == CaptureState.KEPT

    store.delete(capture_id)
    assert not store.exists(capture_id)
    assert not store.path(capture_id, PHOTO).parent.exists()
    store.delete(capture_id)


def test_recover_removes_partial_and_keeps_pending(store: CaptureStore):
    pending_id = store.new_id()
    store.create(pending_id, b"photo", [b"f"], 10.0, "kitchen")
    partial = store.root / ".tmp-x"
    partial.mkdir()
    (partial / PHOTO).write_bytes(b"half")

    store.recover()

    assert not partial.exists()
    assert store.meta(pending_id).state == CaptureState.KEPT


@pytest.mark.parametrize("bad_id", ["../etc", "short", "a" * 23, "a" * 21 + "/", ""])
def test_rejects_path_traversal_ids(store: CaptureStore, bad_id: str):
    with pytest.raises(ValueError):
        store.path(bad_id, PHOTO)
    with pytest.raises(ValueError):
        store.delete(bad_id)


def test_put_file_replaces_atomically(store: CaptureStore, tmp_path: Path):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "kitchen")
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    store.put_file(capture_id, VIDEO, first)
    store.put_file(capture_id, VIDEO, second)

    assert store.path(capture_id, VIDEO).read_bytes() == b"second"
    assert not first.exists()
    assert not second.exists()


def _os_named(name: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(**{**vars(os), "name": name})


@pytest.mark.parametrize(("os_name", "synced"), [("posix", True), ("nt", False)])
def test_put_file_fsyncs_the_capture_directory_on_posix_only(
    store: CaptureStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str, synced: bool
):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "kitchen")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    synced_directories: list[Path] = []
    monkeypatch.setattr(store_module, "_fsync_directory", synced_directories.append)
    monkeypatch.setattr(store_module, "os", _os_named(os_name))

    store.put_file(capture_id, VIDEO, source)

    assert synced_directories == ([store.root / capture_id] if synced else [])
    assert store.path(capture_id, VIDEO).read_bytes() == b"video"


def test_put_file_survives_a_failing_directory_fsync(
    store: CaptureStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "kitchen")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")

    def failing(directory: Path) -> None:
        raise OSError("input/output error")

    monkeypatch.setattr(store_module, "_fsync_directory", failing)
    monkeypatch.setattr(store_module, "os", _os_named("posix"))

    with caplog.at_level(logging.WARNING, logger="photobooth.store"):
        store.put_file(capture_id, VIDEO, source)

    assert store.path(capture_id, VIDEO).read_bytes() == b"video"
    assert capture_id in caplog.text


def _make_id_dir(store: CaptureStore, files: dict[str, bytes]) -> str:
    capture_id = store.new_id()
    directory = store.root / capture_id
    directory.mkdir()
    for name, content in files.items():
        (directory / name).write_bytes(content)
    return capture_id


@pytest.mark.parametrize(
    "files",
    [
        {PHOTO: b"photo"},
        {PHOTO: b"photo", META: b'{"id": "trunc'},
        {PHOTO: b"photo", META: b"\x00\xff garbage"},
        {PHOTO: b"photo", META: b"[]"},
    ],
    ids=["missing-metadata", "truncated-metadata", "garbled-metadata", "wrong-shape"],
)
def test_recover_removes_capture_with_unreadable_metadata(
    store: CaptureStore, files: dict[str, bytes]
):
    broken_id = _make_id_dir(store, files)
    pending_id = store.new_id()
    store.create(pending_id, b"photo", [b"f"], 10.0, "kitchen")
    partial = store.root / ".tmp-leftover"
    partial.mkdir()

    store.recover()

    assert not (store.root / broken_id).exists()
    assert not partial.exists()
    assert store.meta(pending_id).state == CaptureState.KEPT


def test_create_fsyncs_every_file_before_the_rename(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    events: list[str] = []
    real_fsync, real_rename = os.fsync, os.rename

    def spy_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def spy_rename(src, dst) -> None:
        events.append("rename")
        real_rename(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "rename", spy_rename)

    store.create(store.new_id(), b"photo", [b"a", b"b"], 10.0, "kitchen")

    assert events[:4] == ["fsync", "fsync", "fsync", "rename"]


def test_keep_fsyncs_metadata_before_replacing_it(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "kitchen")
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def spy_replace(src, dst) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)

    store.keep(capture_id)

    assert events == ["fsync", "replace"]


def test_recover_leaves_capture_with_transiently_unreadable_metadata(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    locked_id = store.new_id()
    store.create(locked_id, b"photo", [b"f"], 10.0, "kitchen")
    pending_id = store.new_id()
    store.create(pending_id, b"photo", [b"f"], 10.0, "kitchen")
    real_meta = store.meta

    def locked_meta(capture_id: str):
        if capture_id == locked_id:
            raise PermissionError("held by another process")
        return real_meta(capture_id)

    monkeypatch.setattr(store, "meta", locked_meta)

    with caplog.at_level(logging.WARNING, logger="photobooth.store"):
        store.recover()

    assert store.path(locked_id, PHOTO).read_bytes() == b"photo"
    assert store.path(locked_id, CLIP).read_bytes() == b"f"
    assert real_meta(pending_id).state == CaptureState.KEPT
    assert locked_id in caplog.text


def test_recover_continues_when_removal_fails(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    stuck = store.root / ".tmp-stuck"
    stuck.mkdir()
    pending_id = store.new_id()
    store.create(pending_id, b"photo", [b"f"], 10.0, "kitchen")
    real_rmtree = shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        if Path(path) == stuck:
            raise PermissionError("held by another process")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with caplog.at_level(logging.WARNING, logger="photobooth.store"):
        store.recover()

    assert stuck.exists()
    assert store.meta(pending_id).state == CaptureState.KEPT
    assert ".tmp-stuck" in caplog.text


CLIP_BYTES = 1000
THRESHOLD = 10_000


def _capture(
    store: CaptureStore, captured_at: str, built: tuple[str, ...] = (BOOMERANG, VIDEO)
) -> str:
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"c" * CLIP_BYTES], 10.0, "kitchen")
    meta_path = store.path(capture_id, META)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["captured_at"] = captured_at
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    for name in built:
        store.path(capture_id, name).write_bytes(b"media")
    return capture_id


def _clip_bytes(store: CaptureStore) -> int:
    return sum(path.stat().st_size for path in store.root.glob(f"*/{CLIP}"))


def _free_space(store: CaptureStore, monkeypatch: pytest.MonkeyPatch, free: int) -> None:
    """Reports `free` bytes now and one more byte for every clip byte deleted since."""
    initial = _clip_bytes(store)
    real_disk_usage = shutil.disk_usage
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda path: real_disk_usage(path)._replace(free=free + initial - _clip_bytes(store)),
    )


def test_prune_deletes_the_oldest_clip_first_by_capture_time(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    middle = _capture(store, "2026-10-02T10:00:00+00:00")
    oldest = _capture(store, "2026-10-01T10:00:00+00:00")
    newest = _capture(store, "2026-10-03T10:00:00+00:00")
    _free_space(store, monkeypatch, THRESHOLD - CLIP_BYTES // 2)

    assert store.prune_clips(THRESHOLD) == [oldest]

    assert not store.path(oldest, CLIP).exists()
    assert store.path(middle, CLIP).exists()
    assert store.path(newest, CLIP).exists()


def test_prune_never_touches_photo_boomerang_video_or_metadata(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    capture_id = _capture(store, "2026-10-01T10:00:00+00:00")
    _free_space(store, monkeypatch, THRESHOLD - 1)

    assert store.prune_clips(THRESHOLD) == [capture_id]

    assert store.path(capture_id, PHOTO).read_bytes() == b"photo"
    assert store.path(capture_id, BOOMERANG).read_bytes() == b"media"
    assert store.path(capture_id, VIDEO).read_bytes() == b"media"
    assert store.meta(capture_id).id == capture_id


def test_prune_stops_as_soon_as_enough_is_free(store: CaptureStore, monkeypatch: pytest.MonkeyPatch):
    ids = [_capture(store, f"2026-10-0{day}T10:00:00+00:00") for day in (1, 2, 3, 4)]
    _free_space(store, monkeypatch, THRESHOLD - 2 * CLIP_BYTES)

    assert store.prune_clips(THRESHOLD) == ids[:2]

    assert [store.path(capture_id, CLIP).exists() for capture_id in ids] == [False, False, True, True]


def test_prune_leaves_everything_when_free_space_is_sufficient(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    ids = [_capture(store, f"2026-10-0{day}T10:00:00+00:00") for day in (1, 2)]
    _free_space(store, monkeypatch, THRESHOLD)

    assert store.prune_clips(THRESHOLD) == []

    assert all(store.path(capture_id, CLIP).exists() for capture_id in ids)


@pytest.mark.parametrize("built", [(BOOMERANG,), (VIDEO,), ()], ids=["no-video", "no-boomerang", "neither"])
def test_prune_never_deletes_a_clip_whose_media_are_not_built(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch, built: tuple[str, ...]
):
    unbuilt = _capture(store, "2026-10-01T10:00:00+00:00", built)
    built_id = _capture(store, "2026-10-02T10:00:00+00:00")
    _free_space(store, monkeypatch, 0)

    assert store.prune_clips(THRESHOLD) == [built_id]

    assert store.path(unbuilt, CLIP).exists()


def test_prune_gives_up_when_no_clip_is_eligible(store: CaptureStore, monkeypatch: pytest.MonkeyPatch):
    unbuilt = _capture(store, "2026-10-01T10:00:00+00:00", ())
    _free_space(store, monkeypatch, 0)

    assert store.prune_clips(THRESHOLD) == []

    assert store.path(unbuilt, CLIP).exists()


def test_prune_skips_unreadable_captures(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    broken = _capture(store, "2026-10-01T10:00:00+00:00")
    store.path(broken, META).write_text('{"id": "trunc', encoding="utf-8")
    readable = _capture(store, "2026-10-02T10:00:00+00:00")
    _free_space(store, monkeypatch, 0)

    with caplog.at_level(logging.WARNING, logger="photobooth.store"):
        assert store.prune_clips(THRESHOLD) == [readable]

    assert store.path(broken, CLIP).exists()
    assert broken in caplog.text


def test_prune_logs_each_deletion(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    ids = [_capture(store, f"2026-10-0{day}T10:00:00+00:00") for day in (1, 2)]
    _free_space(store, monkeypatch, 0)

    with caplog.at_level(logging.INFO, logger="photobooth.store"):
        store.prune_clips(THRESHOLD)

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert [any(capture_id in message for message in messages) for capture_id in ids] == [True, True]


def test_prune_defaults_to_five_gigabytes_free(store: CaptureStore, monkeypatch: pytest.MonkeyPatch):
    assert CLIP_KEEP_FREE_BYTES == 5 * 1024**3
    capture_id = _capture(store, "2026-10-01T10:00:00+00:00")
    _free_space(store, monkeypatch, CLIP_KEEP_FREE_BYTES - 1)

    assert store.prune_clips() == [capture_id]


def test_prune_returns_nothing_when_the_media_directory_is_unusable(store: CaptureStore):
    shutil.rmtree(store.root)

    assert store.prune_clips(THRESHOLD) == []


def test_create_stamps_the_event_in_the_metadata(store: CaptureStore):
    capture_id = store.new_id()

    store.create(capture_id, b"photo", [b"f"], 10.0, "birthday-anna")

    assert store.meta(capture_id).event == "birthday-anna"
    stored = json.loads(store.path(capture_id, META).read_text(encoding="utf-8"))
    assert stored["event"] == "birthday-anna"


def _without_event(store: CaptureStore, capture_id: str) -> None:
    meta_path = store.path(capture_id, META)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["event"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_a_capture_made_before_events_reads_as_kitchen(store: CaptureStore):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "party")
    _without_event(store, capture_id)

    assert store.meta(capture_id).event == "kitchen"


def test_recover_keeps_and_keeps_marking_a_capture_made_before_events(store: CaptureStore):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0, "party")
    _without_event(store, capture_id)

    store.recover()

    assert store.exists(capture_id)
    assert store.meta(capture_id).state == CaptureState.KEPT
    assert store.meta(capture_id).event == "kitchen"


def test_prune_deletes_the_clip_of_a_capture_made_before_events(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    capture_id = _capture(store, "2026-10-01T10:00:00+00:00")
    _without_event(store, capture_id)
    _free_space(store, monkeypatch, 0)

    assert store.prune_clips(THRESHOLD) == [capture_id]
