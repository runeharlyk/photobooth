import asyncio
import itertools
import shutil
import threading
import time
from dataclasses import replace

import pytest

from photobooth import session
from photobooth.camera.fake import FakeCamera
from photobooth.session import FAILURE_MESSAGE, BoothState, Screen
from photobooth.store import BOOMERANG, CLIP, META, PHOTO, VIDEO, CaptureState

MEGABYTE = 1024 * 1024


def stored_captures(store) -> list[str]:
    return sorted(entry.name for entry in store.root.iterdir() if (entry / META).is_file())


def drain(queue: asyncio.Queue) -> list[BoothState]:
    states = []
    while not queue.empty():
        states.append(queue.get_nowait())
    return states


def screens_entered(states: list[BoothState]) -> list[Screen]:
    entered = []
    for previous, state in itertools.pairwise([BoothState(Screen.IDLE), *states]):
        if state.screen is not previous.screen:
            entered.append(state.screen)
    return entered


async def wait_for_frames(camera: FakeCamera) -> None:
    async with asyncio.timeout(5):
        while not camera.running:
            await asyncio.sleep(0.01)
    await asyncio.to_thread(camera.next_frame, 0, 5)


class SlowStartCamera(FakeCamera):
    def __init__(self, delay_s: float) -> None:
        super().__init__(fps=30)
        self.delay_s = delay_s

    def start(self) -> None:
        time.sleep(self.delay_s)
        super().start()


@pytest.fixture
def boomerang_gate(make_booth, monkeypatch):
    """Holds the first boomerang build in its worker thread until released."""
    started = threading.Event()
    release = threading.Event()
    calls = itertools.count()
    real_build_boomerang = session.build_boomerang

    def gated(*args):
        if next(calls) == 0:
            started.set()
            release.wait(10)
        real_build_boomerang(*args)

    monkeypatch.setattr(session, "build_boomerang", gated)
    yield started, release
    release.set()


async def test_full_cycle_produces_three_media(make_booth, camera, store, fast_timings, await_screen, await_state):
    booth = make_booth(timings=replace(fast_timings, result_timeout_s=30))
    await booth.start()
    events = booth.subscribe()

    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)

    seen = drain(events)
    assert screens_entered(seen) == [Screen.COUNTDOWN, Screen.CAPTURING, Screen.RESULT]
    assert {state.remaining for state in seen if state.screen is Screen.COUNTDOWN} == {1}
    assert result.delete_available
    assert camera.running is False

    ready = await await_state(booth, lambda state: state.boomerang_ready and state.video_ready, 10)
    assert ready.capture_id == result.capture_id
    for name in (PHOTO, BOOMERANG, VIDEO):
        assert store.path(result.capture_id, name).stat().st_size > 0


async def test_countdown_publishes_each_second(make_booth, fast_timings, await_screen):
    booth = make_booth(timings=replace(fast_timings, countdown_s=2.2, record_start_s=1.6))
    events = booth.subscribe()

    await booth.tap()
    await await_screen(booth, Screen.CAPTURING)

    assert [state.remaining for state in drain(events) if state.screen is Screen.COUNTDOWN] == [3, 2, 1]


async def test_countdown_starts_before_the_camera_is_up(make_booth, store, await_screen):
    slow_camera = SlowStartCamera(delay_s=0.8)
    booth = make_booth(slow_camera)
    try:
        await asyncio.wait_for(booth.tap(), 0.5)

        assert booth.state.screen is Screen.COUNTDOWN
        assert slow_camera.running is False
        result = await await_screen(booth, Screen.RESULT)
    finally:
        await booth.close()
        slow_camera.stop()

    assert store.path(result.capture_id, PHOTO).stat().st_size > 0
    assert store.path(result.capture_id, CLIP).stat().st_size == 0
    assert slow_camera.running is False


async def test_camera_off_at_idle(booth, camera, await_screen):
    assert camera.running is False

    await booth.tap()
    await await_screen(booth, Screen.RESULT)
    await await_screen(booth, Screen.IDLE)

    assert camera.running is False


async def test_double_tap_makes_one_capture(booth, store, await_screen):
    events = booth.subscribe()

    await asyncio.gather(booth.tap(), booth.tap())
    assert booth.state.screen is Screen.COUNTDOWN
    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)
    await booth.tap()
    assert booth.state.screen is Screen.RESULT
    await await_screen(booth, Screen.IDLE)

    states = drain(events)
    assert screens_entered(states) == [Screen.COUNTDOWN, Screen.CAPTURING, Screen.RESULT, Screen.IDLE]
    assert all(state.message is None for state in states)
    assert stored_captures(store) == [result.capture_id]


async def test_delete_inside_window_removes_capture(booth, store, await_screen):
    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)

    assert await booth.delete() is True

    assert not store.exists(result.capture_id)
    assert booth.state.screen is Screen.IDLE
    assert booth.state.capture_id is None
    await booth.close()
    assert list(store.root.iterdir()) == []


async def test_delete_after_window_is_refused(booth, store, await_screen, await_state):
    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)
    await await_state(booth, lambda state: not state.delete_available)

    assert await booth.delete() is False

    assert booth.state.screen is Screen.RESULT
    assert store.meta(result.capture_id).state is CaptureState.KEPT


async def test_timeout_keeps_capture(make_booth, store, fast_timings, await_screen):
    booth = make_booth(timings=replace(fast_timings, delete_window_s=30, result_timeout_s=0.5))
    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)

    idle = await await_screen(booth, Screen.IDLE)

    assert idle.capture_id is None
    assert not idle.delete_available
    assert store.meta(result.capture_id).state is CaptureState.KEPT
    assert await booth.delete() is False


async def test_camera_start_failure_returns_to_idle(make_booth, store, await_state):
    camera = FakeCamera(fail_start=True)
    booth = make_booth(camera)

    await booth.tap()

    failed = await await_state(booth, lambda state: state.message is not None)
    assert failed.screen is Screen.IDLE
    assert failed.message == FAILURE_MESSAGE
    assert camera.running is False
    cleared = await await_state(booth, lambda state: state.message is None, 5)
    assert cleared.screen is Screen.IDLE
    assert stored_captures(store) == []


async def test_camera_dies_mid_countdown(booth, camera, store, await_state):
    await booth.tap()
    await wait_for_frames(camera)
    assert booth.state.screen is Screen.COUNTDOWN

    camera.kill()

    failed = await await_state(booth, lambda state: state.message is not None)
    assert failed.screen is Screen.IDLE
    assert failed.message == FAILURE_MESSAGE
    assert camera.running is False
    assert stored_captures(store) == []


async def test_failed_save_stops_the_camera(booth, camera, store, monkeypatch, await_state):
    def full_disk(*args):
        assert camera.running
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "create", full_disk)

    await booth.tap()

    failed = await await_state(booth, lambda state: state.message is not None)
    assert failed.screen is Screen.IDLE
    assert failed.message == FAILURE_MESSAGE
    assert camera.running is False
    assert stored_captures(store) == []


async def test_empty_clip_still_gives_photo_and_video(
    make_booth, camera, store, fast_timings, await_screen, await_state
):
    booth = make_booth(timings=replace(fast_timings, record_start_s=0.55, result_timeout_s=30))
    await booth.tap()
    await wait_for_frames(camera)
    camera.hold()

    result = await await_screen(booth, Screen.RESULT)
    ready = await await_state(booth, lambda state: state.video_ready, 10)

    capture_id = result.capture_id
    assert store.path(capture_id, CLIP).stat().st_size == 0
    assert store.path(capture_id, PHOTO).stat().st_size > 0
    assert store.path(capture_id, VIDEO).stat().st_size > 0
    assert not store.path(capture_id, BOOMERANG).exists()
    assert ready.screen is Screen.RESULT
    assert not ready.boomerang_ready


async def test_clip_fps_is_measured_from_frame_times(make_booth, store, fast_timings, await_screen):
    slow_camera = FakeCamera(fps=10)
    booth = make_booth(slow_camera, replace(fast_timings, record_start_s=0.1))
    try:
        await booth.tap()
        result = await await_screen(booth, Screen.RESULT)
    finally:
        slow_camera.stop()

    assert 7 < store.meta(result.capture_id).clip_fps < 13


async def test_new_photo_during_build_finishes_old_media(booth, store, await_screen, boomerang_gate):
    building, release = boomerang_gate
    await booth.tap()
    first = await await_screen(booth, Screen.RESULT)
    assert await asyncio.to_thread(building.wait, 10)

    await asyncio.wait_for(booth.new_photo(), 5)

    assert booth.state.screen is Screen.COUNTDOWN
    assert store.meta(first.capture_id).state is CaptureState.KEPT
    second = await await_screen(booth, Screen.RESULT)
    assert second.capture_id != first.capture_id
    release.set()
    await booth.close()
    assert store.path(first.capture_id, BOOMERANG).stat().st_size > 0
    assert store.path(first.capture_id, VIDEO).stat().st_size > 0


async def test_media_finished_after_the_result_screen_leaves_flags_alone(booth, store, await_screen, boomerang_gate):
    building, release = boomerang_gate
    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)
    assert await asyncio.to_thread(building.wait, 10)
    await await_screen(booth, Screen.IDLE)

    release.set()
    await booth.close()

    assert store.path(result.capture_id, BOOMERANG).stat().st_size > 0
    assert store.path(result.capture_id, VIDEO).stat().st_size > 0
    assert booth.state == BoothState(Screen.IDLE)


async def test_unwritable_media_dir_is_a_fault(make_booth, camera, store):
    shutil.rmtree(store.root)
    store.root.write_bytes(b"")
    booth = make_booth()

    await booth.start()
    assert booth.state.fault == "Cannot save photos"

    await booth.tap()
    assert booth.state.screen is Screen.IDLE
    assert camera.running is False


async def test_missing_camera_at_startup_is_a_fault(make_booth):
    camera = FakeCamera(fail_start=True)
    booth = make_booth(camera)

    await booth.start()

    assert booth.state.fault == "Camera not found"
    assert camera.running is False
    await booth.tap()
    assert booth.state.screen is Screen.IDLE
    assert booth.state.message is None


async def test_full_disk_is_a_fault(make_booth, monkeypatch):
    real_disk_usage = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda path: real_disk_usage(path)._replace(free=100 * MEGABYTE))
    booth = make_booth()

    await booth.start()

    assert booth.state.fault == "Storage is full"


async def test_full_disk_before_a_capture_is_a_fault(booth, camera, store, monkeypatch):
    assert booth.state.fault is None
    real_disk_usage = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda path: real_disk_usage(path)._replace(free=100 * MEGABYTE))

    await booth.tap()

    assert booth.state.screen is Screen.IDLE
    assert booth.state.fault == "Storage is full"
    assert camera.running is False
    assert stored_captures(store) == []
