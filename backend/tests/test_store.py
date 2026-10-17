import json
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
