# Photobooth app (PC milestone) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The booth app running end to end on the Windows development PC with the U20Cam: idle, countdown, capture, result with QR code, Delete within 15 s, and photo, boomerang and video built from one capture.

**Architecture:** A Python backend owns the camera, the capture sequence (an asyncio state machine), the capture store on disk and media building with ffmpeg, and serves a Svelte frontend plus a small HTTP API: state pushed over Server-Sent Events, actions as POSTs, the live preview as multipart MJPEG. The camera sits behind one interface; this milestone implements a fake camera (tests, UI work) and an ffmpeg-driven webcam; picamera2 is the Pi milestone.

**Tech Stack:** Python >= 3.11 with uv, FastAPI + uvicorn, segno (QR), Pillow (fake camera, tests), pytest + pytest-asyncio + httpx; ffmpeg 8 on PATH; Svelte 5 + Vite + TypeScript with pnpm; @fontsource fonts bundled.

**Spec:** `docs/superpowers/specs/2026-10-17-photobooth-app-design.md`

## Global Constraints

- English for all code, comments, docs, UI text and commit messages; no dead code.
- Commit messages: one line, `[Gitmoji] [Verb] ...` (e.g. `✨ Adds capture store`), empty body, no trailers of any kind.
- Python runs only through uv (`uv run ...`, `uv add ...`); the frontend only through pnpm.
- UI text, verbatim: idle sticker "TAP HERE", helper "Take a photo"; result sticker "SCAN TO GET YOUR PHOTO"; buttons "Delete" and "New photo"; failed capture "Something went wrong - try again". No notice about where photos go.
- One look: black background, scattered green confetti, green `#2bd96b`; the main label of each screen is a sticker: black rectangle, green capitals, green outline, hard drop shadow, rotated a few degrees, laid across what it labels. No other button style.
- Defaults: countdown 3 s, recording from 1 s into it, flash 2 s starting at 95 % white, boomerang 1 s of clip, delete window 15 s, result timeout 45 s.
- The camera is off at idle; no preview, capture or gallery before a tap.
- Every stream is 16:9; the preview is mirrored and letterboxed (`object-fit: contain`) on the 1280 x 800 screen.
- The QR code encodes `{share_base_url}/{id}` and is available the instant the result screen opens, offline.
- A capture directory is written atomically; startup removes half-written captures and turns `pending` into `kept`.
- Webcam on this PC: `ffmpeg -f dshow -vcodec mjpeg -video_size 1920x1080 -framerate 30 -i video="Innomaker-U20CAM-1080p-S1"`, frames copied (`-c:v copy`), not re-encoded. Measured on 2026-10-17: about 17 fps delivered indoors and roughly 1 s before the first frames arrive, so clip fps is measured from frame timestamps, never assumed.

## Review Focus

1. A second tap during the countdown or result screen, or a double tap on idle: exactly one capture happens.
2. Delete pressed just after the 15 s window closed (the button was still visible on a slow screen): the request is refused and the capture stays `kept`.
3. The camera fails to start, or stops delivering frames mid-countdown: the booth returns to idle with "Something went wrong - try again" and the camera is off.
4. A short or empty clip (camera slow to start): the boomerang uses whatever frames exist, an empty clip skips the boomerang, and the video is then the photo with the flash alone; the photo and QR are never lost.
5. New photo while the previous capture's media is still building: the new countdown starts at once and the old capture's boomerang and video still finish in its own directory.

---

## File Structure

```
backend/
  pyproject.toml
  config.example.toml           # PC defaults, copied to config.toml (ignored by git)
  src/photobooth/
    __init__.py
    __main__.py                 # CLI: load config, recover store, run uvicorn
    config.py                   # Config dataclasses + load_config
    store.py                    # CaptureStore: atomic capture dirs, state, recovery
    media.py                    # ffmpeg: boomerang and video with flash
    camera/__init__.py          # Frame, Camera protocol, CameraError, make_camera
    camera/fake.py              # FakeCamera (Pillow test pattern)
    camera/ffmpeg.py            # FfmpegCamera + split_mjpeg
    session.py                  # Booth state machine
    sharing.py                  # share_url, qr_svg
    api.py                      # FastAPI app factory
  tests/
    conftest.py                 # timings scaled down, fixtures
    test_config.py  test_store.py  test_media.py  test_camera.py  test_session.py  test_api.py
frontend/
  package.json  vite.config.ts  tsconfig.json  index.html
  src/main.ts  src/App.svelte  src/app.css
  src/lib/booth.ts              # SSE state store + action calls
  src/lib/Sticker.svelte  src/lib/Confetti.svelte
  src/screens/Idle.svelte  src/screens/Countdown.svelte  src/screens/Result.svelte
.gitignore                      # backend/.venv, backend/config.toml, media/, frontend/node_modules, frontend/dist
```

---

### Task 1: Backend project and configuration

**Files:**
- Create: `.gitignore`, `backend/pyproject.toml`, `backend/config.example.toml`, `backend/src/photobooth/__init__.py`, `backend/src/photobooth/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces (`photobooth.config`):
  - `@dataclass(frozen=True) CameraConfig(kind: Literal["fake", "ffmpeg"], device: str = "", input_format: str = "dshow", video_size: str = "1920x1080", framerate: int = 30)`
  - `@dataclass(frozen=True) Timings(countdown_s=3.0, record_start_s=1.0, flash_s=2.0, flash_opacity=0.95, boomerang_s=1.0, delete_window_s=15.0, result_timeout_s=45.0, message_s=4.0)`
  - `@dataclass(frozen=True) Config(camera: CameraConfig, timings: Timings, share_base_url: str, media_dir: Path, host: str = "127.0.0.1", port: int = 8000)`
  - `load_config(path: Path) -> Config`: TOML via `tomllib`, tables `[camera]` and `[timings]`; missing keys take defaults; an unknown key or a `record_start_s >= countdown_s` raises `ValueError` naming it; `media_dir` relative paths resolve against the config file's directory.

- [ ] **Step 1:** `uv init --package --name photobooth backend`, then `uv add fastapi uvicorn segno pillow` and `uv add --dev pytest pytest-asyncio httpx` in `backend/`; set `requires-python = ">=3.11"`, pytest `asyncio_mode = "auto"`, and a `photobooth = "photobooth.__main__:main"` script. Write `.gitignore` as in File Structure.
- [ ] **Step 2: Write failing tests** in `test_config.py`:
  - `test_defaults_fill_missing_keys`: a TOML with only `share_base_url`, `media_dir` and `[camera] kind = "fake"` gives `Timings()` defaults, `countdown_s == 3.0`, `delete_window_s == 15.0`.
  - `test_unknown_key_is_rejected`: `[timings] countdwn_s = 2` raises `ValueError` matching `countdwn_s`.
  - `test_record_start_must_precede_shot`: `record_start_s = 3.0` with default countdown raises `ValueError`.
  - `test_media_dir_relative_to_config_file`: `media_dir = "media"` in `tmp_path/cfg/config.toml` resolves to `tmp_path/cfg/media`.
- [ ] **Step 3:** Run `uv run pytest tests/test_config.py -v`; expect failures (module missing).
- [ ] **Step 4:** Implement `config.py`. Write `config.example.toml` with `kind = "ffmpeg"`, `device = "Innomaker-U20CAM-1080p-S1"`, `share_base_url = "https://photo.example.invalid/p"`, `media_dir = "../media"`.
- [ ] **Step 5:** Run the tests; expect 4 passed.
- [ ] **Step 6:** Commit: `🎉 Adds the backend project and configuration`

### Task 2: Capture store

**Files:**
- Create: `backend/src/photobooth/store.py`
- Test: `backend/tests/test_store.py`

**Interfaces:**
- Produces (`photobooth.store`):
  - Constants `PHOTO = "photo.jpg"`, `CLIP = "clip.mjpeg"`, `BOOMERANG = "boomerang.mp4"`, `VIDEO = "video.mp4"`, `META = "capture.json"`; `PUBLIC_FILES = (PHOTO, BOOMERANG, VIDEO)`.
  - `class CaptureState(StrEnum): PENDING = "pending"; KEPT = "kept"`
  - `@dataclass CaptureMeta(id: str, captured_at: str, state: CaptureState, uploaded: bool, clip_fps: float)` (`captured_at` ISO 8601 UTC)
  - `class CaptureStore(root: Path)` with `new_id() -> str` (`secrets.token_urlsafe(16)`), `create(capture_id: str, photo: bytes, clip_frames: Sequence[bytes], clip_fps: float) -> None`, `put_file(capture_id: str, name: str, source: Path) -> None`, `path(capture_id: str, name: str) -> Path`, `exists(capture_id: str) -> bool`, `meta(capture_id: str) -> CaptureMeta`, `keep(capture_id: str) -> None`, `delete(capture_id: str) -> None`, `recover() -> None`.
  - `create` writes photo, clip (frames concatenated) and metadata (state `pending`, `uploaded` false), metadata last, into `root/.tmp-<id>`, then renames to `root/<id>`. `put_file` moves `source` into the capture directory with `os.replace`. `delete` removes the directory and is a no-op if it is gone. `recover` removes every `.tmp-*` directory and sets `pending` captures to `kept`. Ids are validated (`[A-Za-z0-9_-]{22}`) on every call; anything else raises `ValueError`.

- [ ] **Step 1: Write failing tests:**
  - `test_create_writes_complete_capture`: after `create`, `photo.jpg` bytes equal the input, `clip.mjpeg` equals `b"".join(frames)`, `meta(id).state == CaptureState.PENDING`, `uploaded is False`, `clip_fps` round-trips, and no `.tmp-*` remains.
  - `test_keep_and_delete`: `keep` gives `KEPT`; `delete` removes the directory; a second `delete` does not raise.
  - `test_recover_removes_partial_and_keeps_pending`: a hand-made `.tmp-x` directory is gone after `recover`, and a `pending` capture becomes `kept`.
  - `test_rejects_path_traversal_ids`: `path("../etc", PHOTO)` raises `ValueError`.
  - `test_put_file_replaces_atomically`: a file put twice under `VIDEO` holds the second content.
- [ ] **Step 2:** Run `uv run pytest tests/test_store.py -v`; expect failures.
- [ ] **Step 3:** Implement `store.py`.
- [ ] **Step 4:** Run; expect 5 passed.
- [ ] **Step 5:** Commit: `✨ Adds the atomic capture store`

### Task 3: Media building

**Files:**
- Create: `backend/src/photobooth/media.py`
- Test: `backend/tests/test_media.py`

**Interfaces:**
- Produces (`photobooth.media`):
  - `class MediaError(Exception)`
  - `OUTPUT_SIZE = (1280, 720)`
  - `build_boomerang(clip: Path, fps: float, seconds: float, out: Path) -> None`: the last `seconds` of the clip (all of it if shorter), forwards then reversed, H.264 yuv420p at `OUTPUT_SIZE`, `+faststart`, no audio. Raises `MediaError("empty clip")` if the clip has no frames.
  - `build_video(clip: Path | None, fps: float, photo: Path, flash_s: float, flash_opacity: float, out: Path) -> None`: the clip (omitted when `None`), then the photo held `flash_s` seconds under a white layer whose opacity falls linearly from `flash_opacity` to 0. Same encoding as above.
  - Both write to `out.with_suffix(".part.mp4")` first and rename; any ffmpeg failure raises `MediaError` with ffmpeg's stderr tail.
- Notes: a `fps` measured from timestamps may be fractional; pass it as `-framerate`. Scale with `force_original_aspect_ratio=decrease` and pad, so a non-16:9 input is letterboxed, never stretched. A white `color` source with an alpha fade (`format=rgba`, `fade=out:alpha=1`, scaled by `flash_opacity`) overlaid on the looped photo is one way to build the flash.

- [ ] **Step 1: Write failing tests.** A fixture writes N numbered test frames with Pillow (1280 x 720, each a distinct solid grey level `10 * i`) as an MJPEG clip and a photo (mid-grey). A helper `frame_at(video, index) -> Image` extracts one frame with ffmpeg (`select=eq(n\,index)`); a helper `frame_count(video)` uses ffprobe `-count_frames`.
  - `test_boomerang_plays_forward_then_back`: 30-frame clip at 30 fps, `seconds=0.5` gives 30 frames (15 forward + 15 reversed); frame k and frame 29-k have mean grey within 6 of each other; frame 0's grey is near that of clip frame 15.
  - `test_boomerang_short_clip_uses_all_frames`: a 5-frame clip with `seconds=1.0` gives 10 frames.
  - `test_boomerang_empty_clip_raises`: an empty clip file raises `MediaError`.
  - `test_video_ends_in_fading_flash`: 30-frame clip at 30 fps, photo grey 100, `flash_s=2.0`, `flash_opacity=0.95`: total frames 90; frame 30 mean is within 8 of `100 + 0.95 * (255 - 100)`; the last frame mean is within 8 of 100; frame 15 is near clip frame 15.
  - `test_video_without_clip_is_flash_only`: `clip=None` gives 60 frames.
  - `test_output_is_1280x720_h264`: ffprobe reports `h264`, `1280x720`, `yuv420p`.
- [ ] **Step 2:** Run `uv run pytest tests/test_media.py -v`; expect failures.
- [ ] **Step 3:** Implement `media.py` with `subprocess.run([...], check=False, capture_output=True)`.
- [ ] **Step 4:** Run; expect 6 passed.
- [ ] **Step 5:** Commit: `✨ Adds boomerang and flash video building`

### Task 4: Camera layer

**Files:**
- Create: `backend/src/photobooth/camera/__init__.py`, `camera/fake.py`, `camera/ffmpeg.py`
- Test: `backend/tests/test_camera.py`

**Interfaces:**
- Produces (`photobooth.camera`):
  - `@dataclass(frozen=True) Frame(seq: int, t: float, jpeg: bytes)` (`t` from `time.monotonic()`)
  - `class CameraError(Exception)`
  - `class Camera(Protocol)`: `start() -> None` (returns once running; raises `CameraError` if no frame arrives within 5 s), `stop() -> None` (idempotent), `running: bool`, `next_frame(after_seq: int, timeout: float) -> Frame` (blocks until a frame newer than `after_seq`; raises `CameraError` on timeout or if the camera died), `capture_still() -> bytes` (JPEG).
  - `FakeCamera(fps: float = 30, size=(1280, 720), still_size=(1920, 1080), fail_start: bool = False)`: a thread renders numbered frames with Pillow; `fail_start` makes `start` raise; `kill()` simulates the camera dying (later `next_frame` raises).
  - `FfmpegCamera(config: CameraConfig)`: runs the ffmpeg command from Global Constraints with `-c:v copy -f mjpeg pipe:1`, reads stdout in a thread, splits frames with `split_mjpeg`; `capture_still` returns the newest frame (the 1080p webcam frame is the still on this machine).
  - `split_mjpeg(buffer: bytearray) -> list[bytes]`: removes and returns every complete JPEG (SOI `FFD8` to EOI `FFD9`) from the front of `buffer`, leaving an incomplete tail.
  - `make_camera(config: CameraConfig) -> Camera`

- [ ] **Step 1: Write failing tests:**
  - `test_split_mjpeg_handles_partial_tail`: two whole JPEGs plus half of a third yield two frames, and the half remains in the buffer; feeding the rest yields the third.
  - `test_fake_camera_delivers_increasing_frames`: after `start`, three `next_frame` calls return strictly increasing `seq` and `t`, and each `jpeg` opens with Pillow at 1280 x 720.
  - `test_fake_still_is_full_size`: `capture_still()` opens at 1920 x 1080.
  - `test_fake_fail_start_raises`: `FakeCamera(fail_start=True).start()` raises `CameraError`.
  - `test_next_frame_after_kill_raises`: after `kill()`, `next_frame(..., timeout=1)` raises `CameraError`.
- [ ] **Step 2:** Run `uv run pytest tests/test_camera.py -v`; expect failures.
- [ ] **Step 3:** Implement the package.
- [ ] **Step 4:** Run; expect 5 passed.
- [ ] **Step 5: Manual check on this PC:** a throwaway `uv run python -c` that starts `FfmpegCamera` with the example config, reads frames for 3 s and prints the count and size; expect roughly 15-30 fps at 1920 x 1080 after a warm-up of about 1 s, and the process gone after `stop()`.
- [ ] **Step 6:** Commit: `✨ Adds the camera layer with fake and ffmpeg cameras`

### Task 5: Booth state machine

**Files:**
- Create: `backend/src/photobooth/session.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_session.py`

**Interfaces:**
- Consumes: `Camera`, `CameraError`, `Frame` (Task 4); `CaptureStore`, file constants (Task 2); `build_boomerang`, `build_video`, `MediaError` (Task 3); `Timings` (Task 1).
- Produces (`photobooth.session`):
  - `class Screen(StrEnum): IDLE, COUNTDOWN, CAPTURING, RESULT` (values lowercase)
  - `@dataclass(frozen=True) BoothState(screen: Screen, remaining: int | None, capture_id: str | None, delete_available: bool, boomerang_ready: bool, video_ready: bool, message: str | None, fault: str | None)`
  - `class Booth(camera: Camera, store: CaptureStore, timings: Timings)` with `state: BoothState`, `subscribe() -> asyncio.Queue[BoothState]`, `unsubscribe(queue) -> None`, `async start() -> None`, `async tap() -> None`, `async delete() -> bool`, `async new_photo() -> None`, `async close() -> None`.
  - `MIN_FREE_BYTES = 500 * 1024 * 1024`
- Behaviour:
  - `start` checks for lasting faults: the media dir must be writable and have at least `MIN_FREE_BYTES` free (`shutil.disk_usage`), and the camera is probed once (`start` then `stop`). The first failure sets `fault` to "Camera not found", "Cannot save photos" or "Storage is full". The storage checks repeat before every capture.
  - `tap` is honoured only on `IDLE` without `fault`; it starts the camera (blocking calls via `asyncio.to_thread`), enters `COUNTDOWN` with `remaining` 3, 2, 1, one per second. Recording collects frames from `record_start_s` until zero.
  - At zero: `CAPTURING`, `capture_still`, `store.create`, camera stopped, then `RESULT` with `delete_available=True`. Media builds in a background task per capture, into the capture's own directory via `store.put_file`; the readiness flags flip only if that capture is still on screen.
  - After `delete_window_s`: `delete_available=False`, `store.keep`. `delete` succeeds only while available, removes the capture and returns to `IDLE`. `new_photo` keeps the current capture and runs a fresh countdown. After `result_timeout_s` the screen returns to `IDLE` (capture kept if not already).
  - Any `CameraError` or failed `store.create`: camera stopped, `IDLE` with `message="Something went wrong - try again"` cleared after `message_s`. `MediaError` is logged; the result screen stays.
- `conftest.py` provides `fast_timings` (countdown 0.6 s, record start 0.2 s, flash 0.3 s, boomerang 0.2 s, delete window 0.5 s, result timeout 1.0 s, message 0.3 s), a `store` on `tmp_path`, and a `booth` fixture with a `FakeCamera(fps=30)`, plus `await_screen(booth, screen, timeout)`.

- [ ] **Step 1: Write failing tests:**
  - `test_full_cycle_produces_three_media`: tap; screens pass through countdown, capturing, result; within 10 s `boomerang_ready` and `video_ready` are true and all three public files exist; the camera is stopped on the result screen.
  - `test_camera_off_at_idle`: before any tap and after the result timeout, `camera.running is False`.
  - `test_double_tap_makes_one_capture`: tap twice quickly and once during the countdown; after the result screen, the store holds exactly one capture. (Review Focus 1)
  - `test_delete_inside_window_removes_capture`: on result, `await booth.delete()` returns True, the directory is gone, screen is idle.
  - `test_delete_after_window_is_refused`: wait past `delete_window_s`; `delete()` returns False and the capture is `kept`. (Review Focus 2)
  - `test_timeout_keeps_capture`: after `result_timeout_s` the screen is idle and the capture is `kept`.
  - `test_camera_start_failure_returns_to_idle`: `FakeCamera(fail_start=True)`: after tap the screen is idle with the failure message, then the message clears. (Review Focus 3)
  - `test_camera_dies_mid_countdown`: call `camera.kill()` after the countdown starts: idle with the message, no capture stored, camera not running. (Review Focus 3)
  - `test_empty_clip_still_gives_photo_and_video`: `record_start_s` set equal to just under `countdown_s` and a 1 fps fake so no frame is recorded: the photo exists, the video exists, the boomerang does not, and the result screen is shown. (Review Focus 4)
  - `test_new_photo_during_build_finishes_old_media`: press `new_photo` as soon as the result screen shows; the next countdown starts at once, and the first capture's video eventually exists. (Review Focus 5)
  - `test_unwritable_media_dir_is_a_fault`: a media dir that is a file makes `start()` set `fault == "Cannot save photos"`, and `tap` is ignored.
  - `test_missing_camera_at_startup_is_a_fault`: with `FakeCamera(fail_start=True)`, `start()` sets `fault == "Camera not found"` and the camera is not running afterwards.
  - `test_full_disk_is_a_fault`: with `shutil.disk_usage` monkeypatched to report 100 MB free, `start()` sets `fault == "Storage is full"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_session.py -v`; expect failures.
- [ ] **Step 3:** Implement `session.py`; a single `asyncio.Lock` serialises transitions, and every timer task is cancelled on the transition that obsoletes it.
- [ ] **Step 4:** Run the whole suite `uv run pytest -v`; expect all passed.
- [ ] **Step 5:** Commit: `✨ Adds the booth capture state machine`

### Task 6: Sharing and HTTP API

**Files:**
- Create: `backend/src/photobooth/sharing.py`, `backend/src/photobooth/api.py`, `backend/src/photobooth/__main__.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `Booth`, `BoothState`, `Screen` (Task 5); `CaptureStore`, `PUBLIC_FILES` (Task 2); `Camera` (Task 4); `load_config`, `Config` (Task 1).
- Produces:
  - `sharing.share_url(base: str, capture_id: str) -> str` (`base.rstrip("/") + "/" + capture_id`), `sharing.qr_svg(url: str) -> str` (segno, error level M, black on white, quiet zone 2).
  - `api.create_app(booth: Booth, camera: Camera, store: CaptureStore, share_base_url: str, frontend_dir: Path | None) -> FastAPI`, routes:
    - `GET /api/events`: SSE, one `data:` JSON per state, starting with the current one: the `BoothState` fields plus `share_url` (null without a capture).
    - `POST /api/tap`, `POST /api/new-photo`: 204. `POST /api/delete`: 204 if deleted, 409 if refused.
    - `GET /api/preview.mjpg`: `multipart/x-mixed-replace; boundary=frame` from `camera.next_frame` while the camera runs; the stream ends when it stops; 404 when not running.
    - `GET /api/captures/{id}/qr.svg`: `image/svg+xml`.
    - `GET /media/{id}/{name}`: only names in `PUBLIC_FILES`; unknown names, invalid ids or missing files give 404.
    - `/`: static frontend from `frontend_dir` when given.
  - `__main__.main()`: `photobooth --config PATH`; loads config, `store.recover()`, builds camera, booth and app, `await booth.start()` on startup, `booth.close()` and `camera.stop()` on shutdown, serves on `config.host:config.port`, frontend from `../frontend/dist` relative to the package's repo when that directory exists.

- [ ] **Step 1: Write failing tests** (httpx `AsyncClient` with `ASGITransport`, the `booth` fixture from Task 5):
  - `test_share_url_joins_cleanly`: `share_url("https://x/p/", "abc")` is `"https://x/p/abc"`.
  - `test_events_start_with_current_state`: the first SSE event parses to `screen == "idle"` and `share_url is None`.
  - `test_tap_to_result_over_http`: POST tap; poll events until `screen == "result"`; `share_url` ends with the `capture_id`; `GET /api/captures/{id}/qr.svg` is 200 `image/svg+xml`; `GET /media/{id}/photo.jpg` is 200.
  - `test_delete_after_window_is_409`: after the window, POST delete is 409.
  - `test_media_rejects_unlisted_names`: `/media/{id}/capture.json`, `/media/{id}/clip.mjpeg` and `/media/..%2F/photo.jpg` are 404.
  - `test_preview_is_404_at_idle`.
- [ ] **Step 2:** Run `uv run pytest tests/test_api.py -v`; expect failures.
- [ ] **Step 3:** Implement `sharing.py`, `api.py`, `__main__.py`.
- [ ] **Step 4:** Run the whole suite; expect all passed.
- [ ] **Step 5: Manual check:** copy `config.example.toml` to `config.toml`, set `kind = "fake"`, run `uv run photobooth --config config.toml`, `curl -N http://127.0.0.1:8000/api/events` shows the idle state, `curl -X POST .../api/tap` drives it to result.
- [ ] **Step 6:** Commit: `✨ Adds the HTTP API and QR sharing`

### Task 7: Frontend screens

**Files:**
- Create: everything under `frontend/` in File Structure (scaffold with `pnpm create vite frontend --template svelte-ts`, then trim the template's demo files).
- Test: `pnpm check` (svelte-check) and the manual run below; the spec's automated-test requirement covers the backend.

**Interfaces:**
- Consumes: the API of Task 6.
- Produces: `src/lib/booth.ts` exporting `booth` (a readable store of the SSE state, reconnecting with `EventSource`'s own retry) and `tap()`, `deleteCapture()`, `newPhoto()` (POSTs).
- Design values: canvas 1280 x 800, black background; green `#2bd96b` with a darker rim `#1fa652`; sticker font Archivo Black (`@fontsource/archivo-black`), text font Inter (`@fontsource/inter`); sticker = `#111` fill, green capitals, 4 px green border, `box-shadow: 5px 6px 0 #0008`, rotation -7 deg on idle, -5 deg on result.
- Screens (from the spec's Screens section and the approved mockups):
  - **Idle:** confetti, a 230 px green circle centred with the "TAP HERE" sticker across it, "Take a photo" in grey below, replaced by `message` or `fault` when set; the circle and sticker are one button calling `tap()`, inert while `fault` is set.
  - **Countdown:** `<img src="/api/preview.mjpg">` filling the screen with `object-fit: contain` and `transform: scaleX(-1)`; the green circle animates from the centre to the bottom-right corner (about 92 px, 22 px from the edges) and shows `remaining`.
  - **Capturing:** a white full-screen overlay fading out over about 300 ms.
  - **Result:** the photo in an 8 px white frame rotated -2 deg on the left, replaced by a looping muted `<video>` of the boomerang once `boomerang_ready`; on the right the "SCAN TO GET YOUR PHOTO" sticker over the QR (`/api/captures/{id}/qr.svg`), then "Delete" (outlined, shown only while `delete_available`) and "New photo" (green).
- `vite.config.ts` proxies `/api` and `/media` to `http://127.0.0.1:8000`; `pnpm build` outputs `frontend/dist`.

- [ ] **Step 1:** Scaffold, add the two fontsource packages, set up the proxy, and write `booth.ts` and the screens.
- [ ] **Step 2:** Run `pnpm check`; expect 0 errors and 0 warnings.
- [ ] **Step 3:** Run the backend with the fake camera and `pnpm dev`; in a 1280 x 800 window, tap through idle, countdown, result, Delete and New photo, and check each screen against the spec text above.
- [ ] **Step 4:** Run `pnpm build`; restart the backend and confirm `http://127.0.0.1:8000/` serves the built app.
- [ ] **Step 5:** Commit: `✨ Adds the booth screens`

### Task 8: End to end on the PC with the U20Cam

**Files:**
- Modify: `backend/config.example.toml` only if the run shows a wrong default.

- [ ] **Step 1:** With `config.toml` at `kind = "ffmpeg"` and the U20Cam, run the backend and open `http://127.0.0.1:8000/` in Chrome at 1280 x 800.
- [ ] **Step 2:** Verify each "Done means" item of the spec: the full cycle; photo, boomerang and video play and look right (the video ends on the photo under a fading flash); Delete within 15 s removes the directory, after 15 s the button is gone; the QR appears with the result screen and decodes to `{share_base_url}/{id}`; the U20Cam's light is off at idle.
- [ ] **Step 3:** Run `uv run pytest -v` and `pnpm check` once more; expect all green.
- [ ] **Step 4:** Commit any default fixed in Step 2: `🐛 Fixes <default> found on the PC run`
