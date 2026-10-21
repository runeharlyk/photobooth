import io

from libcamera import Transform, controls
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

from photobooth.camera import START_TIMEOUT_S, CameraError, Frame, FrameHub

PREVIEW_SIZE = (1280, 720)
FULL_VIEW_SENSOR_SIZE = (2304, 1296)
MIRRORED = Transform(hflip=True)
STILL_QUALITY = 95


class _HubOutput(io.BufferedIOBase):
    def __init__(self, hub: FrameHub) -> None:
        self._hub = hub

    def writable(self) -> bool:
        return True

    def write(self, buffer) -> int:
        self._hub.publish(bytes(buffer))
        return len(buffer)


class Picamera2Camera:
    """Camera Module 3 through picamera2: hardware MJPEG preview, full-resolution stills.

    The preview reads the 2x2-binned full-sensor mode; left to choose, libcamera picks the
    1536x864 mode, which is cropped to the centre and so looks zoomed in next to the still.
    """

    def __init__(self) -> None:
        self._hub = FrameHub()
        self._picam: Picamera2 | None = None

    @property
    def running(self) -> bool:
        return self._hub.running

    def start(self) -> None:
        if self.running:
            return
        self.stop()
        baseline = self._hub.open()
        try:
            self._picam = Picamera2()
            self._picam.configure(
                self._picam.create_video_configuration(
                    main={"size": PREVIEW_SIZE, "format": "YUV420"},
                    sensor={"output_size": FULL_VIEW_SENSOR_SIZE},
                    transform=MIRRORED,
                )
            )
            self._picam.options["quality"] = STILL_QUALITY
            if "AfMode" in self._picam.camera_controls:
                self._picam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            self._picam.start_encoder(MJPEGEncoder(), FileOutput(_HubOutput(self._hub)))
            self._picam.start()
            self._hub.await_first_frame(baseline, START_TIMEOUT_S)
        except Exception as error:
            self.stop()
            raise CameraError(f"cannot start the camera: {error}") from error

    def stop(self) -> None:
        self._hub.close()
        picam, self._picam = self._picam, None
        if picam is None:
            return
        try:
            picam.stop_encoder()
            picam.stop()
        finally:
            picam.close()

    def next_frame(self, after_seq: int, timeout: float) -> Frame:
        return self._hub.next_frame(after_seq, timeout)

    def capture_still(self) -> bytes:
        if self._picam is None or not self.running:
            raise CameraError("camera is not running")
        still = io.BytesIO()
        try:
            self._picam.stop_encoder()
            self._picam.switch_mode_and_capture_file(
                self._picam.create_still_configuration(transform=MIRRORED), still, format="jpeg"
            )
        except Exception as error:
            self._hub.fail(f"still capture failed: {error}")
            raise CameraError(f"still capture failed: {error}") from error
        return still.getvalue()
