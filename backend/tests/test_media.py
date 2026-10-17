import io
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from photobooth import media
from photobooth.media import OUTPUT_SIZE, MediaError, build_boomerang, build_video

FPS = 30.0


def grey_jpeg(level: int, size: tuple[int, int] = OUTPUT_SIZE) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (level, level, level)).save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def write_clip(path: Path, frames: int) -> Path:
    path.write_bytes(b"".join(grey_jpeg(8 * i) for i in range(frames)))
    return path


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    return write_clip(tmp_path / "clip.mjpeg", 30)


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpg"
    path.write_bytes(grey_jpeg(100))
    return path


def frame_at(video: Path, index: int) -> Image.Image:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(video),
            "-vf", f"select='eq(n,{index})'", "-frames:v", "1",
            "-f", "image2pipe", "-c:v", "png", "-",
        ],
        check=True,
        capture_output=True,
    )
    return Image.open(io.BytesIO(result.stdout))


def grey_of(image: Image.Image) -> float:
    return ImageStat.Stat(image.convert("L")).mean[0]


def probe(video: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", *args, "-of", "csv=p=0", str(video)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().split(",")


def frame_count(video: Path) -> int:
    fields = probe(video, "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames")
    return int(fields[0])


def test_boomerang_plays_forward_then_back(clip: Path, tmp_path: Path):
    out = tmp_path / "boomerang.mp4"
    build_boomerang(clip, FPS, 0.5, out)

    assert frame_count(out) == 30
    for k in range(15):
        assert abs(grey_of(frame_at(out, k)) - grey_of(frame_at(out, 29 - k))) < 6
    assert abs(grey_of(frame_at(out, 0)) - 8 * 15) < 6
    assert abs(grey_of(frame_at(out, 14)) - 8 * 29) < 6


def test_boomerang_short_clip_uses_all_frames(tmp_path: Path):
    out = tmp_path / "boomerang.mp4"
    build_boomerang(write_clip(tmp_path / "short.mjpeg", 5), FPS, 1.0, out)

    assert frame_count(out) == 10


def test_boomerang_empty_clip_raises(tmp_path: Path):
    empty = tmp_path / "empty.mjpeg"
    empty.write_bytes(b"")
    out = tmp_path / "boomerang.mp4"

    with pytest.raises(MediaError, match="empty clip"):
        build_boomerang(empty, FPS, 1.0, out)
    assert not out.exists()


def test_boomerang_garbage_clip_raises_with_ffmpeg_output(tmp_path: Path):
    garbage = tmp_path / "garbage.mjpeg"
    garbage.write_bytes(b"not a jpeg stream")
    out = tmp_path / "boomerang.mp4"

    with pytest.raises(MediaError):
        build_boomerang(garbage, FPS, 1.0, out)
    assert not out.exists()
    assert not out.with_suffix(".part.mp4").exists()


def test_video_ends_in_fading_flash(clip: Path, photo: Path, tmp_path: Path):
    out = tmp_path / "video.mp4"
    build_video(clip, FPS, photo, 2.0, 0.95, out)

    assert frame_count(out) == 90
    assert abs(grey_of(frame_at(out, 30)) - (100 + 0.95 * (255 - 100))) < 8
    assert abs(grey_of(frame_at(out, 89)) - 100) < 8
    assert abs(grey_of(frame_at(out, 15)) - 8 * 15) < 8


def test_video_without_clip_is_flash_only(photo: Path, tmp_path: Path):
    out = tmp_path / "video.mp4"
    build_video(None, FPS, photo, 2.0, 0.95, out)

    assert frame_count(out) == 60
    assert abs(grey_of(frame_at(out, 0)) - (100 + 0.95 * (255 - 100))) < 8


def test_video_with_fractional_fps(tmp_path: Path, photo: Path):
    clip = write_clip(tmp_path / "clip.mjpeg", 17)
    out = tmp_path / "video.mp4"
    build_video(clip, 17.3, photo, 2.0, 0.95, out)

    assert frame_count(out) == 17 + round(2.0 * 17.3)


def test_output_is_1280x720_h264(clip: Path, photo: Path, tmp_path: Path):
    boomerang = tmp_path / "boomerang.mp4"
    video = tmp_path / "video.mp4"
    build_boomerang(clip, FPS, 0.5, boomerang)
    build_video(clip, FPS, photo, 1.0, 0.95, video)

    for path in (boomerang, video):
        codec, width, height, pix_fmt = probe(
            path, "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,pix_fmt"
        )
        assert (codec, int(width), int(height), pix_fmt) == ("h264", 1280, 720, "yuv420p")
        assert probe(path, "-select_streams", "a", "-show_entries", "stream=codec_type") == [""]


def test_non_16_by_9_input_is_letterboxed_not_stretched(tmp_path: Path):
    clip = tmp_path / "square.mjpeg"
    clip.write_bytes(b"".join(grey_jpeg(200, (720, 720)) for _ in range(5)))
    out = tmp_path / "boomerang.mp4"
    build_boomerang(clip, FPS, 1.0, out)

    frame = frame_at(out, 0).convert("L")
    assert frame.getpixel((640, 360)) > 180
    assert frame.getpixel((20, 360)) < 30
    assert frame.getpixel((1260, 360)) < 30


def test_output_is_written_atomically(clip: Path, tmp_path: Path):
    out = tmp_path / "boomerang.mp4"
    build_boomerang(clip, FPS, 0.5, out)

    assert out.exists()
    assert not out.with_suffix(".part.mp4").exists()


def test_missing_ffmpeg_raises_media_error(clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(media.subprocess, "run", missing)
    out = tmp_path / "boomerang.mp4"

    with pytest.raises(MediaError, match="ffmpeg"):
        build_boomerang(clip, FPS, 0.5, out)
    assert not out.with_suffix(".part.mp4").exists()


def test_hung_ffmpeg_raises_media_error_and_removes_part(
    clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out = tmp_path / "boomerang.mp4"
    part = out.with_suffix(".part.mp4")

    def hang(*args, **kwargs):
        part.write_bytes(b"partial")
        assert kwargs["timeout"] == media.FFMPEG_TIMEOUT_S
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(media.subprocess, "run", hang)

    with pytest.raises(MediaError, match="timed out"):
        build_boomerang(clip, FPS, 0.5, out)
    assert not part.exists()
    assert not out.exists()


def test_failed_rename_raises_media_error_and_removes_part(
    clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def locked(*args, **kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(media.os, "replace", locked)
    out = tmp_path / "boomerang.mp4"

    with pytest.raises(MediaError, match="in use"):
        build_boomerang(clip, FPS, 0.5, out)
    assert not out.with_suffix(".part.mp4").exists()
    assert not out.exists()


def test_part_file_is_fsynced_before_it_is_renamed(clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = tmp_path / "boomerang.mp4"
    events: list[tuple[str, int]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd: int) -> None:
        events.append(("fsync", os.fstat(fd).st_size))
        real_fsync(fd)

    def spy_replace(src, dst) -> None:
        events.append(("replace", Path(src).stat().st_size))
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)

    build_boomerang(clip, FPS, 0.5, out)

    assert [name for name, _ in events] == ["fsync", "replace"]
    assert events[0][1] == events[1][1] > 0


def test_encoder_runs_with_the_configured_preset(clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    commands: list[list[str]] = []
    real_run = subprocess.run

    def spy(command, **kwargs):
        commands.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(media.subprocess, "run", spy)
    monkeypatch.setattr(media, "X264_PRESET", "ultrafast")

    build_boomerang(clip, FPS, 0.5, tmp_path / "boomerang.mp4")

    command = commands[0]
    assert command[command.index("-preset") + 1] == "ultrafast"
