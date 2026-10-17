import io
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from photobooth.camera import CameraError, make_camera
from photobooth.camera import ffmpeg as ffmpeg_module
from photobooth.camera.fake import FakeCamera
from photobooth.camera.ffmpeg import FfmpegCamera, build_command, split_mjpeg
from photobooth.config import CameraConfig


def jpeg(level: int, size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (level, level, level)).save(buffer, "JPEG")
    return buffer.getvalue()


def size_of(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


@pytest.fixture
def camera():
    fake = FakeCamera()
    fake.start()
    yield fake
    fake.stop()


def test_split_mjpeg_handles_partial_tail():
    first, second, third = jpeg(10), jpeg(100), jpeg(200)
    buffer = bytearray(first + second + third[: len(third) // 2])

    assert split_mjpeg(buffer) == [first, second]
    assert bytes(buffer) == third[: len(third) // 2]

    buffer += third[len(third) // 2 :]
    assert split_mjpeg(buffer) == [third]
    assert buffer == b""


def test_split_mjpeg_discards_bytes_before_the_first_frame():
    frame = jpeg(10)
    buffer = bytearray(b"\x00\x01garbage" + frame)

    assert split_mjpeg(buffer) == [frame]


def test_fake_camera_delivers_increasing_frames(camera):
    frames = []
    after = 0
    for _ in range(3):
        frame = camera.next_frame(after, timeout=1)
        frames.append(frame)
        after = frame.seq

    assert all(a.seq < b.seq and a.t < b.t for a, b in zip(frames, frames[1:]))
    assert all(size_of(f.jpeg) == (1280, 720) for f in frames)


def test_fake_still_is_full_size(camera):
    assert size_of(camera.capture_still()) == (1920, 1080)


def test_fake_fail_start_raises():
    with pytest.raises(CameraError):
        FakeCamera(fail_start=True).start()


def test_next_frame_after_kill_raises(camera):
    camera.kill()

    with pytest.raises(CameraError):
        camera.next_frame(0, timeout=1)
    assert not camera.running


def test_next_frame_times_out_without_frames(camera):
    camera.hold()
    latest = camera.next_frame(0, timeout=1)

    with pytest.raises(TimeoutError):
        camera.next_frame(latest.seq, timeout=0.2)
    assert camera.running


def test_held_camera_still_captures_and_release_resumes_frames(camera):
    camera.hold()
    latest = camera.next_frame(0, timeout=1)

    assert size_of(camera.capture_still()) == (1920, 1080)

    camera.release()
    assert camera.next_frame(latest.seq, timeout=1).seq > latest.seq


def test_stopped_camera_refuses_frames_and_restarts(camera):
    camera.stop()
    camera.stop()

    assert not camera.running
    with pytest.raises(CameraError):
        camera.next_frame(0, timeout=1)
    with pytest.raises(CameraError):
        camera.capture_still()

    camera.start()
    assert camera.running
    assert camera.next_frame(0, timeout=1).jpeg


def test_make_camera_selects_by_kind():
    assert isinstance(make_camera(CameraConfig(kind="fake")), FakeCamera)
    assert isinstance(make_camera(CameraConfig(kind="ffmpeg", device="cam")), FfmpegCamera)


def test_build_command_copies_the_mjpeg_stream_from_the_configured_device():
    config = CameraConfig(kind="ffmpeg", device="Innomaker-U20CAM-1080p-S1")

    assert build_command(config) == [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "dshow", "-vcodec", "mjpeg", "-video_size", "1920x1080", "-framerate", "30",
        "-i", "video=Innomaker-U20CAM-1080p-S1",
        "-c:v", "copy", "-f", "mjpeg", "pipe:1",
    ]  # fmt: skip


STUB_STREAMING = """
import sys, time
data = open(sys.argv[1], "rb").read()
out = sys.stdout.buffer
while True:
    out.write(data[: len(data) // 2]); out.flush(); time.sleep(0.01)
    out.write(data[len(data) // 2 :]); out.flush(); time.sleep(0.02)
"""

STUB_ONE_FRAME_THEN_EXIT = """
import sys
sys.stdout.buffer.write(open(sys.argv[1], "rb").read()); sys.stdout.buffer.flush()
sys.stderr.write("device vanished"); sys.exit(3)
"""

STUB_FAILING = 'import sys; sys.stderr.write("no such device"); sys.exit(1)'

STUB_SILENT = "import time; time.sleep(60)"


@pytest.fixture
def stub(monkeypatch, tmp_path: Path):
    spawned: list[subprocess.Popen] = []

    class RecordingPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self)

    frame_file = tmp_path / "frame.jpg"
    frame_file.write_bytes(jpeg(128, (1920, 1080)))
    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)

    def use(script: str) -> list[subprocess.Popen]:
        monkeypatch.setattr(
            ffmpeg_module,
            "build_command",
            lambda config: [sys.executable, "-c", script, str(frame_file)],
        )
        return spawned

    return use


@pytest.fixture
def ffmpeg_camera():
    cam = FfmpegCamera(CameraConfig(kind="ffmpeg", device="stub"))
    yield cam
    cam.stop()


def test_ffmpeg_camera_streams_frames_and_stop_ends_the_process(stub, ffmpeg_camera):
    spawned = stub(STUB_STREAMING)

    ffmpeg_camera.start()
    first = ffmpeg_camera.next_frame(0, timeout=2)
    second = ffmpeg_camera.next_frame(first.seq, timeout=2)

    assert second.seq > first.seq and second.t > first.t
    assert size_of(second.jpeg) == (1920, 1080)
    assert size_of(ffmpeg_camera.capture_still()) == (1920, 1080)

    ffmpeg_camera.stop()
    assert spawned[0].poll() is not None
    assert not ffmpeg_camera.running
    with pytest.raises(CameraError):
        ffmpeg_camera.next_frame(second.seq, timeout=1)

    ffmpeg_camera.start()
    assert ffmpeg_camera.next_frame(0, timeout=2).jpeg
    assert len(spawned) == 2


def test_ffmpeg_camera_reports_unexpected_exit_with_stderr(stub, ffmpeg_camera):
    stub(STUB_ONE_FRAME_THEN_EXIT)

    ffmpeg_camera.start()
    first = ffmpeg_camera.next_frame(0, timeout=2)

    with pytest.raises(CameraError, match="device vanished"):
        ffmpeg_camera.next_frame(first.seq, timeout=2)
    assert not ffmpeg_camera.running

    ffmpeg_camera.stop()
    with pytest.raises(CameraError, match="not running"):
        ffmpeg_camera.next_frame(first.seq, timeout=1)


def test_ffmpeg_camera_start_fails_when_ffmpeg_exits_at_once(stub, ffmpeg_camera):
    stub(STUB_FAILING)

    with pytest.raises(CameraError, match="no such device"):
        ffmpeg_camera.start()
    assert not ffmpeg_camera.running


def test_ffmpeg_camera_start_fails_without_a_first_frame(monkeypatch, stub, ffmpeg_camera):
    spawned = stub(STUB_SILENT)
    monkeypatch.setattr(ffmpeg_module, "START_TIMEOUT_S", 0.3)

    started = time.monotonic()
    with pytest.raises(CameraError, match="no frame"):
        ffmpeg_camera.start()

    assert time.monotonic() - started < 3
    assert spawned[0].poll() is not None
    assert not ffmpeg_camera.running
