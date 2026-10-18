import asyncio
import logging
import math
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
from pathlib import Path

from photobooth.camera import Camera, CameraError, Frame
from photobooth.config import LiveSettings, Timings
from photobooth.media import MediaError, build_boomerang, build_video
from photobooth.store import BOOMERANG, CLIP, PHOTO, VIDEO, CaptureStore

MIN_FREE_BYTES = 500 * 1024 * 1024
FALLBACK_FPS = 30.0

FAILURE_MESSAGE = "Something went wrong - try again"
CAMERA_NOT_FOUND = "Camera not found"
CANNOT_SAVE = "Cannot save photos"
STORAGE_FULL = "Storage is full"

_DELETE_WINDOW = "delete window"
_RESULT_TIMEOUT = "result timeout"
_MESSAGE = "message"
_SCRATCH_PREFIX = ".tmp-build-"

logger = logging.getLogger(__name__)


class Screen(StrEnum):
    IDLE = "idle"
    COUNTDOWN = "countdown"
    CAPTURING = "capturing"
    RESULT = "result"


@dataclass(frozen=True)
class BoothState:
    screen: Screen
    remaining: int | None = None
    capture_id: str | None = None
    delete_available: bool = False
    boomerang_ready: bool = False
    video_ready: bool = False
    message: str | None = None
    fault: str | None = None


class Booth:
    """The capture sequence.

    State transitions and the camera's start, stop and capture_still run under one lock.
    _tick and next_frame run unlocked: FrameHub is thread-safe, and no other camera call
    can run during COUNTDOWN. Each countdown takes the timings and event current when it begins and
    keeps them through its capture, so a reload never changes a capture in progress.

    `stored` is set whenever a capture becomes kept, gains a video or finishes its build, and
    `build_slot` admits one media build at a time; the uploader waits on the first and shares
    the second, so an upload never competes with an ffmpeg build.
    """

    def __init__(self, camera: Camera, store: CaptureStore, settings: LiveSettings) -> None:
        self._camera = camera
        self._store = store
        self._settings = settings
        self.state = BoothState(Screen.IDLE)
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[BoothState]] = set()
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._countdown: asyncio.Task[None] | None = None
        self._builds: set[asyncio.Task[None]] = set()
        self.build_slot = asyncio.Semaphore(1)
        self.stored = asyncio.Event()

    def subscribe(self) -> asyncio.Queue[BoothState]:
        queue: asyncio.Queue[BoothState] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BoothState]) -> None:
        self._subscribers.discard(queue)

    async def start(self) -> None:
        async with self._lock:
            if fault := await self._diagnose():
                self._update(fault=fault)

    async def tap(self) -> None:
        if self.state.screen is not Screen.IDLE:
            return
        async with self._lock:
            if self.state.screen is not Screen.IDLE:
                return
            if self.state.fault is not None and (fault := await self._diagnose()):
                self._update(fault=fault)
                return
            await self._begin_countdown()

    async def delete(self) -> bool:
        async with self._lock:
            capture_id = self.state.capture_id
            if not self.state.delete_available:
                return False
            try:
                await asyncio.to_thread(self._store.delete, capture_id)
            except OSError:
                logger.exception("could not delete capture %s", capture_id)
                if self._store.exists(capture_id):
                    return False
            self._cancel_timers(_DELETE_WINDOW, _RESULT_TIMEOUT)
            self._set_idle()
            return True

    async def new_photo(self) -> None:
        async with self._lock:
            if self.state.screen is Screen.RESULT:
                self._cancel_timers(_DELETE_WINDOW, _RESULT_TIMEOUT)
                await self._keep_current()
                await self._begin_countdown()

    async def close(self) -> None:
        async with self._lock:
            pending = [*self._timers.values(), *([self._countdown] if self._countdown else [])]
            self._timers.clear()
            for task in pending:
                task.cancel()
        if pending:
            await asyncio.wait(pending)
        if self._builds:
            await asyncio.wait(set(self._builds))

    async def _diagnose(self) -> str | None:
        return await asyncio.to_thread(self._probe_camera) or await asyncio.to_thread(self._check_storage)

    def _probe_camera(self) -> str | None:
        try:
            self._camera.start()
        except CameraError:
            logger.exception("camera probe failed")
            return CAMERA_NOT_FOUND
        finally:
            self._camera.stop()
        return None

    def _check_storage(self) -> str | None:
        try:
            with tempfile.TemporaryFile(dir=self._store.root):
                pass
        except OSError:
            logger.exception("media directory %s is not writable", self._store.root)
            return CANNOT_SAVE
        free = shutil.disk_usage(self._store.root).free
        if free < MIN_FREE_BYTES:
            logger.error("media directory %s has only %d bytes free", self._store.root, free)
            return STORAGE_FULL
        return None

    async def _begin_countdown(self) -> None:
        self._cancel_timers(_MESSAGE)
        fault = await asyncio.to_thread(self._check_storage)
        if fault:
            self._set_state(BoothState(Screen.IDLE, fault=fault))
            return
        timings, event = self._settings.timings, self._settings.event
        self._set_state(BoothState(Screen.COUNTDOWN, remaining=math.ceil(timings.countdown_s)))
        self._countdown = asyncio.create_task(self._count_down(time.monotonic(), timings, event))

    async def _count_down(self, began: float, timings: Timings, event: str) -> None:
        try:
            frames = await self._run_countdown(began, timings)
        except Exception:
            logger.exception("camera failed during the countdown")
            frames = None
        async with self._lock:
            if frames is not None:
                try:
                    await self._shoot(frames, timings, event)
                    return
                except Exception:
                    logger.exception("capture failed")
            await self._fail(timings)

    async def _run_countdown(self, began: float, timings: Timings) -> list[Frame]:
        shot_at = began + timings.countdown_s
        ticker = asyncio.create_task(self._tick(shot_at))
        try:
            async with self._lock:
                await asyncio.to_thread(self._camera.start)
            return await self._record(began + timings.record_start_s, shot_at)
        finally:
            ticker.cancel()
            await asyncio.wait({ticker})

    async def _tick(self, shot_at: float) -> None:
        while (left := shot_at - time.monotonic()) > 0:
            remaining = math.ceil(left)
            if remaining != self.state.remaining:
                self._update(remaining=remaining)
            await asyncio.sleep(left - (remaining - 1))

    async def _record(self, start_at: float, shot_at: float) -> list[Frame]:
        await asyncio.sleep(start_at - time.monotonic())
        frames: list[Frame] = []
        after_seq = 0
        while (left := shot_at - time.monotonic()) > 0:
            try:
                frame = await asyncio.to_thread(self._camera.next_frame, after_seq, left)
            except TimeoutError:
                break
            after_seq = frame.seq
            if frame.t >= start_at:
                frames.append(frame)
        return frames

    async def _shoot(self, frames: list[Frame], timings: Timings, event: str) -> None:
        self._update(screen=Screen.CAPTURING, remaining=None)
        clip = [frame.jpeg for frame in frames]
        fps = _measure_fps(frames)
        capture_id = self._store.new_id()
        photo = await asyncio.to_thread(self._camera.capture_still)
        await asyncio.to_thread(self._store.create, capture_id, photo, clip, fps, event)
        await asyncio.to_thread(self._camera.stop)
        self._set_state(BoothState(Screen.RESULT, capture_id=capture_id, delete_available=True))
        self._schedule(_DELETE_WINDOW, timings.delete_window_s, self._keep_current)
        self._schedule(_RESULT_TIMEOUT, timings.result_timeout_s, self._time_out)
        build = asyncio.create_task(self._build_media(capture_id, photo, clip, fps, timings))
        self._builds.add(build)
        build.add_done_callback(self._builds.discard)

    async def _fail(self, timings: Timings) -> None:
        await self._stop_camera()
        self._set_idle(FAILURE_MESSAGE)
        self._schedule(_MESSAGE, timings.message_s, self._clear_message)

    async def _force_idle(self) -> None:
        self._cancel_timers(_DELETE_WINDOW, _RESULT_TIMEOUT)
        await self._stop_camera()
        self._set_idle()

    async def _stop_camera(self) -> None:
        try:
            await asyncio.to_thread(self._camera.stop)
        except Exception:
            logger.exception("could not stop the camera")

    async def _keep_current(self) -> None:
        if not self.state.delete_available:
            return
        try:
            await asyncio.to_thread(self._store.keep, self.state.capture_id)
        except Exception:
            logger.exception("could not mark capture %s kept", self.state.capture_id)
        else:
            self.stored.set()
        self._update(delete_available=False)

    async def _time_out(self) -> None:
        self._cancel_timers(_DELETE_WINDOW)
        await self._keep_current()
        self._set_idle()

    async def _clear_message(self) -> None:
        self._update(message=None)

    async def _build_media(
        self, capture_id: str, photo: bytes, clip: Sequence[bytes], fps: float, timings: Timings
    ) -> None:
        try:
            async with self.build_slot:
                if not self._store.exists(capture_id):
                    return
                with tempfile.TemporaryDirectory(dir=self._store.root, prefix=_SCRATCH_PREFIX) as name:
                    scratch = Path(name)
                    await asyncio.to_thread(_write_inputs, scratch, photo, clip)
                    clip_path = scratch / CLIP if clip else None
                    if clip_path:
                        boomerang = partial(build_boomerang, clip_path, fps, timings.boomerang_s, scratch / BOOMERANG)
                        await self._build_file(capture_id, scratch / BOOMERANG, "boomerang_ready", boomerang)
                    if not self._store.exists(capture_id):
                        return
                    video = partial(
                        build_video,
                        clip_path, fps, scratch / PHOTO, timings.flash_s, timings.flash_opacity, scratch / VIDEO,
                    )
                    await self._build_file(capture_id, scratch / VIDEO, "video_ready", video)
        except OSError:
            logger.exception("could not store media for capture %s", capture_id)
        await self._finish_build(capture_id)
        await self._prune_clips()

    async def _finish_build(self, capture_id: str) -> None:
        async with self._lock:
            if not self._store.exists(capture_id):
                return
            try:
                await asyncio.to_thread(self._store.finish_build, capture_id)
            except Exception:
                logger.exception("could not record that the media of capture %s are built", capture_id)
                return
            self.stored.set()

    async def _prune_clips(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._store.prune_clips)

    async def _build_file(self, capture_id: str, out: Path, flag: str, build: Callable[[], None]) -> None:
        try:
            await asyncio.to_thread(build)
        except MediaError:
            logger.exception("could not build %s for capture %s", out.name, capture_id)
            return
        async with self._lock:
            if not self._store.exists(capture_id):
                return
            self._store.put_file(capture_id, out.name, out)
            self.stored.set()
            if self.state.capture_id == capture_id:
                self._update(**{flag: True})

    def _schedule(self, name: str, delay: float, action: Callable[[], Awaitable[None]]) -> None:
        self._cancel_timers(name)
        self._timers[name] = asyncio.create_task(self._fire(name, delay, action))

    async def _fire(self, name: str, delay: float, action: Callable[[], Awaitable[None]]) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            del self._timers[name]
            try:
                await action()
            except Exception:
                logger.exception("the %s failed; returning to idle", name)
                await self._force_idle()

    def _cancel_timers(self, *names: str) -> None:
        for name in names:
            timer = self._timers.pop(name, None)
            if timer is not None:
                timer.cancel()

    def _set_idle(self, message: str | None = None) -> None:
        self._set_state(BoothState(Screen.IDLE, message=message, fault=self.state.fault))

    def _update(self, **changes: object) -> None:
        self._set_state(replace(self.state, **changes))

    def _set_state(self, state: BoothState) -> None:
        self.state = state
        for queue in self._subscribers:
            queue.put_nowait(state)


def _measure_fps(frames: Sequence[Frame]) -> float:
    span = frames[-1].t - frames[0].t if len(frames) >= 2 else 0.0
    return (len(frames) - 1) / span if span > 0 else FALLBACK_FPS


def _write_inputs(scratch: Path, photo: bytes, clip: Sequence[bytes]) -> None:
    (scratch / PHOTO).write_bytes(photo)
    if clip:
        (scratch / CLIP).write_bytes(b"".join(clip))
