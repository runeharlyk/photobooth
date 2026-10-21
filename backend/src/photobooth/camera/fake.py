import io
import threading
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps

from photobooth.camera import START_TIMEOUT_S, CameraError, Frame, FrameHub


class FakeCamera:
    def __init__(
        self,
        fps: float = 30,
        size: tuple[int, int] = (1280, 720),
        still_size: tuple[int, int] = (1920, 1080),
        fail_start: bool = False,
    ) -> None:
        self._period = 1 / fps
        self._size = size
        self._still_size = still_size
        self._fail_start = fail_start
        self._hub = FrameHub()
        self._stopping = threading.Event()
        self._gate = threading.Lock()
        self._held = False
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._hub.running

    def start(self) -> None:
        if self._fail_start:
            raise CameraError("fake camera configured to fail on start")
        if self.running:
            return
        self.stop()
        baseline = self._hub.open()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()
        try:
            self._hub.await_first_frame(baseline, START_TIMEOUT_S)
        except CameraError:
            self.stop()
            raise

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._hub.close()

    def kill(self) -> None:
        self._stopping.set()
        self._hub.fail("fake camera was killed")

    def hold(self) -> None:
        with self._gate:
            self._held = True

    def release(self) -> None:
        with self._gate:
            self._held = False

    def next_frame(self, after_seq: int, timeout: float) -> Frame:
        return self._hub.next_frame(after_seq, timeout)

    def capture_still(self) -> bytes:
        self._hub.latest()
        return _render(self._still_size, "STILL")

    def _produce(self) -> None:
        number = 0
        while not self._stopping.is_set():
            rendering_started = time.monotonic()
            number += 1
            jpeg = _render(self._size, str(number))
            with self._gate:
                if not self._held:
                    self._hub.publish(jpeg)
            self._stopping.wait(max(0.0, self._period - (time.monotonic() - rendering_started)))


def _render(size: tuple[int, int], label: str) -> bytes:
    image = Image.new("RGB", size, (20, 60, 40))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=size[1] // 4)
    draw.text((size[0] // 2, size[1] // 2), label, fill=(43, 217, 107), font=font, anchor="mm")
    buffer = io.BytesIO()
    ImageOps.mirror(image).save(buffer, "JPEG", quality=80)
    return buffer.getvalue()
