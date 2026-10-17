import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from photobooth.camera.fake import FakeCamera
from photobooth.config import Timings
from photobooth.session import Booth, BoothState, Screen
from photobooth.store import CaptureStore


@pytest.fixture
def fast_timings() -> Timings:
    return Timings(
        countdown_s=0.6,
        record_start_s=0.2,
        flash_s=0.3,
        boomerang_s=0.2,
        delete_window_s=0.5,
        result_timeout_s=1.0,
        message_s=0.3,
    )


@pytest.fixture
def camera():
    fake = FakeCamera(fps=30)
    yield fake
    fake.stop()


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path / "media")


@pytest.fixture
async def make_booth(camera, store, fast_timings):
    booths: list[Booth] = []

    def make(booth_camera=None, timings: Timings | None = None) -> Booth:
        booth = Booth(booth_camera or camera, store, timings or fast_timings)
        booths.append(booth)
        return booth

    yield make
    for booth in booths:
        await booth.close()


@pytest.fixture
async def booth(make_booth) -> Booth:
    started = make_booth()
    await started.start()
    return started


async def wait_for_state(
    booth: Booth, predicate: Callable[[BoothState], bool], timeout: float = 10.0
) -> BoothState:
    queue = booth.subscribe()
    try:
        async with asyncio.timeout(timeout):
            state = booth.state
            while not predicate(state):
                state = await queue.get()
            return state
    finally:
        booth.unsubscribe(queue)


async def wait_for_screen(booth: Booth, screen: Screen, timeout: float = 10.0) -> BoothState:
    return await wait_for_state(booth, lambda state: state.screen is screen, timeout)


@pytest.fixture
def await_state():
    return wait_for_state


@pytest.fixture
def await_screen():
    return wait_for_screen
