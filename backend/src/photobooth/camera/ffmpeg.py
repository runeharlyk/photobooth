import subprocess
import threading
from collections import deque

from photobooth.camera import START_TIMEOUT_S, CameraError, Frame, FrameHub
from photobooth.config import CameraConfig

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_READ_CHUNK = 1 << 16
_STDERR_TAIL_LINES = 10
_TERMINATE_TIMEOUT_S = 5


def build_command(config: CameraConfig) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", config.input_format, "-vcodec", "mjpeg",
        "-video_size", config.video_size, "-framerate", str(config.framerate),
        "-i", f"video={config.device}",
        "-vf", "hflip", "-c:v", "mjpeg", "-q:v", "2", "-f", "mjpeg", "pipe:1",
    ]  # fmt: skip


def split_mjpeg(buffer: bytearray) -> list[bytes]:
    frames = []
    while True:
        start = buffer.find(_SOI)
        if start < 0:
            keep = 1 if buffer.endswith(_SOI[:1]) else 0
            del buffer[: len(buffer) - keep]
            return frames
        del buffer[:start]
        end = buffer.find(_EOI, len(_SOI))
        if end < 0:
            return frames
        frames.append(bytes(buffer[: end + len(_EOI)]))
        del buffer[: end + len(_EOI)]


class FfmpegCamera:
    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._hub = FrameHub()
        self._process: subprocess.Popen[bytes] | None = None
        self._threads: list[threading.Thread] = []
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

    @property
    def running(self) -> bool:
        return self._hub.running

    def start(self) -> None:
        if self.running:
            return
        self.stop()
        try:
            self._process = subprocess.Popen(
                build_command(self._config),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise CameraError(f"cannot launch ffmpeg: {error}") from error
        baseline = self._hub.open()
        self._stderr_tail.clear()
        stderr_reader = threading.Thread(target=self._drain_stderr, args=(self._process,), daemon=True)
        frame_reader = threading.Thread(target=self._read_frames, args=(self._process, stderr_reader), daemon=True)
        self._threads = [stderr_reader, frame_reader]
        for thread in self._threads:
            thread.start()
        try:
            self._hub.await_first_frame(baseline, START_TIMEOUT_S)
        except CameraError:
            self.stop()
            raise

    def stop(self) -> None:
        self._hub.close()
        process, self._process = self._process, None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(_TERMINATE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for thread in self._threads:
            thread.join()
        process.stdout.close()
        process.stderr.close()

    def next_frame(self, after_seq: int, timeout: float) -> Frame:
        return self._hub.next_frame(after_seq, timeout)

    def capture_still(self) -> bytes:
        return self._hub.latest().jpeg

    def _read_frames(self, process: subprocess.Popen[bytes], stderr_reader: threading.Thread) -> None:
        buffer = bytearray()
        while chunk := process.stdout.read1(_READ_CHUNK):
            buffer += chunk
            for jpeg in split_mjpeg(buffer):
                self._hub.publish(jpeg)
        returncode = process.wait()
        stderr_reader.join()
        detail = " | ".join(self._stderr_tail) or "no output"
        self._hub.fail(f"ffmpeg exited with code {returncode}: {detail}")

    def _drain_stderr(self, process: subprocess.Popen[bytes]) -> None:
        for line in process.stderr:
            self._stderr_tail.append(line.decode(errors="replace").strip())
