import json
import os
from pathlib import Path

import pytest

from photobooth.store import (
    CLIP,
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

    store.create(capture_id, b"jpeg-bytes", frames, 17.25)

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
    store.create(capture_id, b"photo", [b"f"], 10.0)

    store.keep(capture_id)
    assert store.meta(capture_id).state == CaptureState.KEPT

    store.delete(capture_id)
    assert not store.exists(capture_id)
    assert not store.path(capture_id, PHOTO).parent.exists()
    store.delete(capture_id)


def test_recover_removes_partial_and_keeps_pending(store: CaptureStore):
    pending_id = store.new_id()
    store.create(pending_id, b"photo", [b"f"], 10.0)
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
    store.create(capture_id, b"photo", [b"f"], 10.0)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    store.put_file(capture_id, VIDEO, first)
    store.put_file(capture_id, VIDEO, second)

    assert store.path(capture_id, VIDEO).read_bytes() == b"second"
    assert not first.exists()
    assert not second.exists()


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
    store.create(pending_id, b"photo", [b"f"], 10.0)
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

    store.create(store.new_id(), b"photo", [b"a", b"b"], 10.0)

    assert events[:4] == ["fsync", "fsync", "fsync", "rename"]


def test_keep_fsyncs_metadata_before_replacing_it(
    store: CaptureStore, monkeypatch: pytest.MonkeyPatch
):
    capture_id = store.new_id()
    store.create(capture_id, b"photo", [b"f"], 10.0)
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
