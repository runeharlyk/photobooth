import io

import pytest
from PIL import Image

from photobooth.camera import CameraError, make_camera
from photobooth.config import CameraConfig

picamera2_camera = pytest.importorskip("photobooth.camera.picamera2")
PREVIEW_SIZE = picamera2_camera.PREVIEW_SIZE
Picamera2Camera = picamera2_camera.Picamera2Camera


@pytest.fixture
def camera():
    camera = Picamera2Camera()
    yield camera
    camera.stop()


def test_preview_frames_are_jpegs_at_preview_size(camera):
    camera.start()
    first = camera.next_frame(0, timeout=2)
    second = camera.next_frame(first.seq, timeout=2)
    assert second.seq > first.seq and second.t > first.t
    assert Image.open(io.BytesIO(second.jpeg)).size == PREVIEW_SIZE


def test_preview_delivers_close_to_thirty_fps(camera):
    camera.start()
    frame = camera.next_frame(0, timeout=2)
    first = frame
    for _ in range(30):
        frame = camera.next_frame(frame.seq, timeout=2)
    fps = (frame.seq - first.seq) / (frame.t - first.t)
    assert fps > 20


def test_preview_sees_the_whole_sensor_like_the_still(camera):
    camera.start()
    crop = camera._picam.capture_metadata()["ScalerCrop"]
    assert (crop[2], crop[3]) == (4608, 2592)


def test_still_is_full_sensor_resolution(camera):
    camera.start()
    still = camera.capture_still()
    assert Image.open(io.BytesIO(still)).size == (4608, 2592)


def test_stop_ends_frames_and_restart_works(camera):
    camera.start()
    camera.stop()
    assert not camera.running
    with pytest.raises(CameraError):
        camera.next_frame(0, timeout=0.5)
    camera.start()
    assert camera.next_frame(0, timeout=2).jpeg[:2] == b"\xff\xd8"


def test_make_camera_builds_the_picamera2_camera():
    assert isinstance(make_camera(CameraConfig(kind="picamera2")), Picamera2Camera)
