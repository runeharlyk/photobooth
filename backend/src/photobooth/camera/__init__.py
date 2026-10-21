import threading
import time
from dataclasses import dataclass
from typing import Protocol

from photobooth.config import CameraConfig

START_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Frame:
    seq: int
    t: float
    jpeg: bytes


class CameraError(Exception):
    pass


class Camera(Protocol):
    """A camera whose preview frames and stills are all mirrored, as a guest sees themself."""

    @property
    def running(self) -> bool: ...

    def start(self) -> None:
        """Return once frames flow; raise CameraError if none arrives in time."""

    def stop(self) -> None: ...

    def next_frame(self, after_seq: int, timeout: float) -> Frame:
        """Return the newest frame with a seq above after_seq.

        Raises TimeoutError when none arrives within timeout, and CameraError
        when the camera has died or is not running.
        """

    def capture_still(self) -> bytes: ...


class FrameHub:
    """Hands the newest frame from one producer thread to any number of consumers."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: Frame | None = None
        self._seq = 0
        self._running = False
        self._failure = "camera is not running"

    @property
    def running(self) -> bool:
        with self._condition:
            return self._running

    def open(self) -> int:
        with self._condition:
            self._latest = None
            self._running = True
            return self._seq

    def close(self) -> None:
        with self._condition:
            self._finish("camera is not running")

    def fail(self, reason: str) -> None:
        with self._condition:
            if self._running:
                self._finish(reason)

    def _finish(self, reason: str) -> None:
        self._running = False
        self._failure = reason
        self._condition.notify_all()

    def publish(self, jpeg: bytes) -> None:
        with self._condition:
            if not self._running:
                return
            self._seq += 1
            self._latest = Frame(self._seq, time.monotonic(), jpeg)
            self._condition.notify_all()

    def next_frame(self, after_seq: int, timeout: float) -> Frame:
        with self._condition:
            self._condition.wait_for(
                lambda: not self._running or (self._latest is not None and self._latest.seq > after_seq),
                timeout,
            )
            if not self._running:
                raise CameraError(self._failure)
            if self._latest is None or self._latest.seq <= after_seq:
                raise TimeoutError(f"no frame within {timeout} s")
            return self._latest

    def latest(self) -> Frame:
        with self._condition:
            if not self._running:
                raise CameraError(self._failure)
            if self._latest is None:
                raise CameraError("no frame has arrived yet")
            return self._latest

    def await_first_frame(self, baseline: int, timeout: float) -> None:
        try:
            self.next_frame(baseline, timeout)
        except TimeoutError:
            raise CameraError(f"no frame within {timeout} s of starting") from None


def make_camera(config: CameraConfig) -> Camera:
    if config.kind == "fake":
        from photobooth.camera.fake import FakeCamera

        return FakeCamera()
    if config.kind == "picamera2":
        from photobooth.camera.picamera2 import Picamera2Camera

        return Picamera2Camera()
    from photobooth.camera.ffmpeg import FfmpegCamera

    return FfmpegCamera(config)
