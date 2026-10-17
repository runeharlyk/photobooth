import os
import subprocess
from pathlib import Path

OUTPUT_SIZE = (1280, 720)
FFMPEG_TIMEOUT_S = 60

_STDERR_TAIL_CHARS = 2000
_YUV420P = "scale=out_range=tv,format=yuv420p"


class MediaError(Exception):
    pass


def build_boomerang(clip: Path, fps: float, seconds: float, out: Path) -> None:
    if clip.stat().st_size == 0:
        raise MediaError("empty clip")
    frames = max(1, round(seconds * fps))
    graph = (
        f"[0:v]{_fit()},reverse,trim=end_frame={frames},split[back][tail];"
        f"[tail]reverse[forward];"
        f"[forward][back]concat=n=2:v=1:a=0,{_YUV420P}[v]"
    )
    _render(["-f", "mjpeg", "-framerate", str(fps), "-i", str(clip)], graph, fps, out)


def build_video(
    clip: Path | None,
    fps: float,
    photo: Path,
    flash_s: float,
    flash_opacity: float,
    out: Path,
) -> None:
    width, height = OUTPUT_SIZE
    flash = (
        f"[{0 if clip is None else 1}:v]{_fit()},format=rgba[photo];"
        f"color=c=white:s={width}x{height}:r={fps}:d={flash_s},format=rgba,"
        f"colorchannelmixer=aa={flash_opacity},fade=out:st=0:d={flash_s}:alpha=1[layer];"
        f"[photo][layer]overlay=format=auto:shortest=1,{_YUV420P}"
    )
    photo_input = ["-loop", "1", "-framerate", str(fps), "-t", str(flash_s), "-i", str(photo)]
    if clip is None:
        _render(photo_input, f"{flash}[v]", fps, out)
        return
    graph = (
        f"[0:v]{_fit()},{_YUV420P}[clip];"
        f"{flash}[flash];"
        f"[clip][flash]concat=n=2:v=1:a=0[v]"
    )
    _render(["-f", "mjpeg", "-framerate", str(fps), "-i", str(clip), *photo_input], graph, fps, out)


def _fit() -> str:
    width, height = OUTPUT_SIZE
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


def _render(inputs: list[str], graph: str, fps: float, out: Path) -> None:
    part = out.with_suffix(".part.mp4")
    command = [
        "ffmpeg", "-v", "error", "-y",
        *inputs,
        "-filter_complex", graph,
        "-map", "[v]",
        "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-an", "-movflags", "+faststart",
        str(part),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as error:
        part.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg timed out after {FFMPEG_TIMEOUT_S} s") from error
    except OSError as error:
        part.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg could not run: {error}") from error
    if result.returncode != 0:
        part.unlink(missing_ok=True)
        tail = result.stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:]
        raise MediaError(f"ffmpeg exited with {result.returncode}: {tail}")
    try:
        os.replace(part, out)
    except OSError as error:
        part.unlink(missing_ok=True)
        raise MediaError(f"could not write {out.name}: {error}") from error
