import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from photobooth.api import create_app
from photobooth.config import UploadConfig
from photobooth.reloader import ConfigReloader
from photobooth.session import Screen
from photobooth.store import BOOMERANG, META, PHOTO, SHARED_META, VIDEO, CaptureStore
from photobooth.uploader import Uploader

TOKEN = "s3cret-upload-token"
CONTENT_TYPES = {PHOTO: "image/jpeg", SHARED_META: "application/json", BOOMERANG: "video/mp4", VIDEO: "video/mp4"}
ROUTE = re.compile(r"/api/captures/([A-Za-z0-9_-]{22})/(photo\.jpg|meta\.json|boomerang\.mp4|video\.mp4)")
EVENT = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


@dataclass(frozen=True)
class Request:
    capture_id: str
    name: str
    authorization: str | None
    content_type: str | None
    user_agent: str | None
    body: bytes
    at: float


class ShareWorker:
    """A real HTTP server on 127.0.0.1 implementing the contract's PUT route and its token check.

    `uploads` holds the requests it stored, `attempts` every request that matched the route.
    `answer` forces a status for a file name; `hold` keeps one request waiting until released.
    """

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.uploads: list[Request] = []
        self.attempts: list[Request] = []
        self.answer: dict[str, HTTPStatus] = {}
        self._holds: dict[tuple[str, str], tuple[threading.Event, threading.Event]] = {}
        self._port = 0
        self._start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def names(self, capture_id: str | None = None, attempts: bool = False) -> list[tuple[str, str]]:
        requests = self.attempts if attempts else self.uploads
        return [(r.capture_id, r.name) for r in requests if capture_id in (None, r.capture_id)]

    def hold(self, capture_id: str, name: str) -> tuple[threading.Event, threading.Event]:
        arrived, release = threading.Event(), threading.Event()
        self._holds[(capture_id, name)] = (arrived, release)
        return arrived, release

    def stop(self) -> None:
        """Closes the listening socket, so the URL refuses connections."""
        for _, release in self._holds.values():
            release.set()
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join()
        self._server.server_close()

    def restart(self) -> None:
        self._start()

    def _start(self) -> None:
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                match = ROUTE.fullmatch(self.path)
                if match is None:
                    self._reply(HTTPStatus.BAD_REQUEST)
                    return
                request = Request(
                    *match.groups(),
                    self.headers.get("Authorization"),
                    self.headers.get("Content-Type"),
                    self.headers.get("User-Agent"),
                    body,
                    time.monotonic(),
                )
                worker.attempts.append(request)
                if gate := worker._holds.get(match.groups()):
                    gate[0].set()
                    gate[1].wait(10)
                self._reply(worker._status(request))

            def _reply(self, status: HTTPStatus) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), Handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05})
        self._thread.start()

    def _status(self, request: Request) -> HTTPStatus:
        if request.authorization != f"Bearer {self.token}":
            return HTTPStatus.UNAUTHORIZED
        if request.content_type != CONTENT_TYPES[request.name] or not _valid_meta(request):
            return HTTPStatus.BAD_REQUEST
        if forced := self.answer.get(request.name):
            return forced
        self.uploads.append(request)
        return HTTPStatus.NO_CONTENT


def _valid_meta(request: Request) -> bool:
    if request.name != SHARED_META:
        return True
    try:
        meta = json.loads(request.body)
    except ValueError:
        return False
    return set(meta) == {"captured_at", "event"} and bool(EVENT.fullmatch(meta["event"]))


@pytest.fixture
def worker():
    server = ShareWorker()
    yield server
    server.stop()


@pytest.fixture
async def start_uploader(store):
    running: list[asyncio.Task] = []

    def start(
        url: str,
        uploader_store: CaptureStore | None = None,
        slot: asyncio.Semaphore | None = None,
        wake: asyncio.Event | None = None,
        poll_s: float = 60.0,
        retry_first_s: float = 0.02,
        retry_cap_s: float = 0.1,
    ) -> tuple[asyncio.Task, asyncio.Event]:
        wake = wake or asyncio.Event()
        uploader = Uploader(
            uploader_store or store,
            UploadConfig(url, TOKEN),
            slot or asyncio.Semaphore(1),
            wake,
            poll_s=poll_s,
            retry_first_s=retry_first_s,
            retry_cap_s=retry_cap_s,
        )
        task = asyncio.create_task(uploader.run())
        running.append(task)
        return task, wake

    yield start
    for task in running:
        task.cancel()
    if running:
        await asyncio.wait(running)


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def captured(store: CaptureStore, captured_at: str, event: str = "party") -> str:
    capture_id = store.new_id()
    store.create(capture_id, f"photo of {capture_id}".encode(), [b"frame"], 10.0, event)
    meta_path = store.path(capture_id, META)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["captured_at"] = captured_at
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return capture_id


def put_video(store: CaptureStore, capture_id: str, name: str) -> None:
    source = store.root / f".tmp-build-{name}"
    source.write_bytes(f"{name} of {capture_id}".encode())
    store.put_file(capture_id, name, source)


def kept(
    store: CaptureStore,
    captured_at: str = "2026-10-18T20:00:00+00:00",
    videos: tuple[str, ...] = (),
    event: str = "party",
    finished: bool = True,
) -> str:
    capture_id = captured(store, captured_at, event)
    store.keep(capture_id)
    for name in videos:
        put_video(store, capture_id, name)
    if finished:
        store.finish_build(capture_id)
    return capture_id


def logged(caplog, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == level]


def at_or_above(caplog, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= level]


async def test_uploads_follow_the_contract_order_headers_and_bodies(store, worker, start_uploader):
    older = kept(store, "2026-10-18T20:00:00+00:00", videos=(BOOMERANG, VIDEO))
    newer = kept(store, "2026-10-18T20:05:00+00:00", videos=(BOOMERANG, VIDEO), event="kitchen")

    start_uploader(worker.url)
    await until(lambda: store.meta(older).uploaded and store.meta(newer).uploaded)

    assert worker.names() == [
        (older, PHOTO),
        (older, SHARED_META),
        (newer, PHOTO),
        (newer, SHARED_META),
        (older, BOOMERANG),
        (older, VIDEO),
        (newer, BOOMERANG),
        (newer, VIDEO),
    ]
    for request in worker.uploads:
        assert request.authorization == f"Bearer {TOKEN}"
        assert request.content_type == CONTENT_TYPES[request.name]
        assert request.user_agent.startswith("photobooth")
        if request.name == SHARED_META:
            continue
        assert request.body == store.path(request.capture_id, request.name).read_bytes()
    metas = {r.capture_id: json.loads(r.body) for r in worker.uploads if r.name == SHARED_META}
    assert metas == {
        older: {"captured_at": "2026-10-18T20:00:00+00:00", "event": "party"},
        newer: {"captured_at": "2026-10-18T20:05:00+00:00", "event": "kitchen"},
    }


async def test_a_new_capture_goes_ahead_of_the_videos_still_queued(store, worker, start_uploader):
    backlog = kept(store, "2026-10-18T20:00:00+00:00", videos=(BOOMERANG, VIDEO))
    arrived, release = worker.hold(backlog, BOOMERANG)
    _, wake = start_uploader(worker.url)
    assert await asyncio.to_thread(arrived.wait, 5)

    fresh = kept(store, "2026-10-18T21:00:00+00:00")
    wake.set()
    release.set()
    await until(lambda: store.meta(backlog).uploaded and store.meta(fresh).uploaded)

    assert worker.names() == [
        (backlog, PHOTO),
        (backlog, SHARED_META),
        (backlog, BOOMERANG),
        (fresh, PHOTO),
        (fresh, SHARED_META),
        (backlog, VIDEO),
    ]


async def test_pending_and_deleted_captures_are_never_uploaded(store, worker, start_uploader, caplog):
    pending = captured(store, "2026-10-18T19:00:00+00:00")
    deleted = kept(store, "2026-10-18T19:30:00+00:00")
    store.delete(deleted)
    shared = kept(store, "2026-10-18T20:00:00+00:00")

    with caplog.at_level(logging.WARNING):
        start_uploader(worker.url, poll_s=0.02)
        await until(lambda: store.meta(shared).uploaded)
        await asyncio.sleep(0.3)

    assert {capture_id for capture_id, _ in worker.names(attempts=True)} == {shared}
    assert store.meta(pending).uploaded_files == []
    assert at_or_above(caplog, logging.WARNING) == []


async def test_a_capture_deleted_between_listing_and_upload_is_skipped_quietly(
    store, worker, start_uploader, caplog
):
    first = kept(store, "2026-10-18T20:00:00+00:00")
    second = kept(store, "2026-10-18T20:01:00+00:00")
    arrived, release = worker.hold(first, PHOTO)

    with caplog.at_level(logging.WARNING):
        start_uploader(worker.url)
        assert await asyncio.to_thread(arrived.wait, 5)
        store.delete(second)
        release.set()
        await until(lambda: store.meta(first).uploaded)
        await asyncio.sleep(0.2)

    assert worker.names(attempts=True) == [(first, PHOTO), (first, SHARED_META)]
    assert at_or_above(caplog, logging.WARNING) == []


async def test_a_kept_capture_uploads_promptly_and_its_videos_follow_as_they_are_built(
    make_booth, store, worker, start_uploader, await_screen
):
    booth = make_booth()
    await booth.start()
    start_uploader(worker.url, slot=booth.build_slot, wake=booth.stored, poll_s=60.0)

    await booth.tap()
    result = await await_screen(booth, Screen.RESULT)
    assert worker.attempts == []
    capture_id = result.capture_id

    await until(lambda: (capture_id, PHOTO) in worker.names(), timeout=3)
    await until(lambda: store.meta(capture_id).uploaded, timeout=15)

    assert worker.names() == [(capture_id, n) for n in (PHOTO, SHARED_META, BOOMERANG, VIDEO)]
    assert worker.uploads[2].body == store.path(capture_id, BOOMERANG).read_bytes()


async def test_videos_built_later_are_uploaded_when_they_appear(store, worker, start_uploader):
    capture_id = kept(store, finished=False)
    _, wake = start_uploader(worker.url, poll_s=60.0)
    await until(lambda: len(worker.uploads) == 2)

    put_video(store, capture_id, BOOMERANG)
    wake.set()
    await until(lambda: len(worker.uploads) == 3)
    assert store.meta(capture_id).uploaded is False

    put_video(store, capture_id, VIDEO)
    store.finish_build(capture_id)
    wake.set()
    await until(lambda: store.meta(capture_id).uploaded)

    assert worker.names() == [(capture_id, n) for n in (PHOTO, SHARED_META, BOOMERANG, VIDEO)]


async def test_a_build_that_ends_without_a_video_leaves_the_capture_uploaded(store, worker, start_uploader):
    capture_id = kept(store, finished=False)
    _, wake = start_uploader(worker.url, poll_s=60.0)
    await until(lambda: len(worker.uploads) == 2)
    assert store.meta(capture_id).uploaded is False

    store.finish_build(capture_id)
    wake.set()
    await asyncio.sleep(0.2)

    assert store.meta(capture_id).uploaded is True
    assert worker.names() == [(capture_id, PHOTO), (capture_id, SHARED_META)]


async def test_a_restart_resumes_without_uploading_a_finished_file_again(store, worker, start_uploader):
    capture_id = kept(store, videos=(BOOMERANG, VIDEO))
    worker.answer[BOOMERANG] = HTTPStatus.SERVICE_UNAVAILABLE
    first_run, _ = start_uploader(worker.url)
    await until(lambda: (capture_id, BOOMERANG) in worker.names(attempts=True))
    first_run.cancel()
    await asyncio.wait({first_run})
    del worker.answer[BOOMERANG]

    rebooted = CaptureStore(store.root)
    rebooted.recover()
    start_uploader(worker.url, uploader_store=rebooted)
    await until(lambda: rebooted.meta(capture_id).uploaded)

    assert worker.names() == [(capture_id, n) for n in (PHOTO, SHARED_META, BOOMERANG, VIDEO)]


async def test_an_offline_backlog_drains_when_the_network_returns_with_one_warning(
    store, worker, start_uploader, caplog
):
    worker.stop()
    backlog = [kept(store, f"2026-10-18T2{hour}:00:00+00:00", videos=(BOOMERANG,)) for hour in range(3)]

    with caplog.at_level(logging.DEBUG):
        start_uploader(worker.url, retry_first_s=0.01, retry_cap_s=0.05)
        await until(lambda: at_or_above(caplog, logging.WARNING), timeout=10)
        await asyncio.sleep(0.3)
        worker.restart()
        await until(lambda: all(store.meta(capture_id).uploaded for capture_id in backlog))

    assert len(worker.uploads) == 9
    [warning] = at_or_above(caplog, logging.WARNING)
    assert "ConnectionRefusedError" in warning
    assert any("resumed" in message for message in logged(caplog, logging.INFO))
    assert TOKEN not in caplog.text


async def test_server_errors_back_off_exponentially_up_to_the_cap_and_warn_once_per_distinct_error(
    store, worker, start_uploader, caplog
):
    capture_id = kept(store)
    worker.answer[PHOTO] = HTTPStatus.SERVICE_UNAVAILABLE

    with caplog.at_level(logging.WARNING):
        start_uploader(worker.url, retry_first_s=0.1, retry_cap_s=0.4)
        await until(lambda: len(worker.attempts) == 6, timeout=5)
        worker.answer[PHOTO] = HTTPStatus.INTERNAL_SERVER_ERROR
        await until(lambda: len(worker.attempts) == 8, timeout=5)
        del worker.answer[PHOTO]
        await until(lambda: store.meta(capture_id).uploaded, timeout=5)

    gaps = [later.at - earlier.at for earlier, later in zip(worker.attempts[:6], worker.attempts[1:6])]
    for gap, expected in zip(gaps, [0.1, 0.2, 0.4, 0.4, 0.4]):
        assert expected * 0.8 <= gap <= expected + 0.25
    warnings = at_or_above(caplog, logging.WARNING)
    assert len(warnings) == 2
    assert "HTTP 503" in warnings[0]
    assert "HTTP 500" in warnings[1]


async def test_a_rejected_token_pauses_uploads_with_one_error(store, start_uploader, caplog):
    worker = ShareWorker(token="the-worker-expects-another-token")
    try:
        kept(store, "2026-10-18T20:00:00+00:00")
        kept(store, "2026-10-18T20:01:00+00:00")
        with caplog.at_level(logging.DEBUG):
            task, wake = start_uploader(worker.url, poll_s=0.02)
            await until(lambda: len(worker.attempts) == 1)
            for _ in range(5):
                wake.set()
                await asyncio.sleep(0.05)
    finally:
        worker.stop()

    assert len(worker.attempts) == 1
    assert task.done() and not task.cancelled()
    [error] = at_or_above(caplog, logging.WARNING)
    assert "token" in error
    assert logged(caplog, logging.ERROR) == [error]
    assert TOKEN not in caplog.text


async def test_a_file_rejected_as_invalid_is_marked_failed_and_not_retried(store, worker, start_uploader, caplog):
    capture_id = kept(store, videos=(BOOMERANG, VIDEO))
    worker.answer[SHARED_META] = HTTPStatus.BAD_REQUEST

    with caplog.at_level(logging.WARNING):
        first_run, _ = start_uploader(worker.url, poll_s=0.02)
        await until(lambda: (capture_id, VIDEO) in worker.names())
        await asyncio.sleep(0.2)
        first_run.cancel()
        await asyncio.wait({first_run})
        start_uploader(worker.url, uploader_store=CaptureStore(store.root), poll_s=0.02)
        await asyncio.sleep(0.2)

    assert worker.names(attempts=True).count((capture_id, SHARED_META)) == 1
    assert worker.names() == [(capture_id, n) for n in (PHOTO, BOOMERANG, VIDEO)]
    meta = store.meta(capture_id)
    assert (meta.failed_files, meta.uploaded) == ([SHARED_META], False)
    [error] = at_or_above(caplog, logging.WARNING)
    assert SHARED_META in error and capture_id in error and "400" in error


async def test_a_capture_from_before_uploads_is_uploaded_in_full_after_a_restart(store, worker, start_uploader):
    capture_id = kept(store, videos=(BOOMERANG, VIDEO), finished=False)
    meta_path = store.path(capture_id, META)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for key in ("uploaded_files", "failed_files", "build_finished"):
        del meta[key]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    store.recover()
    start_uploader(worker.url)
    await until(lambda: store.meta(capture_id).uploaded)

    assert worker.names() == [(capture_id, n) for n in (PHOTO, SHARED_META, BOOMERANG, VIDEO)]


async def test_uploads_wait_while_a_media_build_holds_the_slot(store, worker, start_uploader):
    capture_id = kept(store)
    slot = asyncio.Semaphore(1)

    async with slot:
        start_uploader(worker.url, slot=slot)
        await asyncio.sleep(0.3)
        assert worker.attempts == []
    await until(lambda: store.meta(capture_id).uploaded)


async def test_the_token_and_the_upload_paths_never_reach_the_log(store, worker, start_uploader, caplog):
    capture_id = kept(store, videos=(BOOMERANG,))
    worker.answer[PHOTO] = HTTPStatus.BAD_GATEWAY
    worker.answer[BOOMERANG] = HTTPStatus.BAD_REQUEST

    with caplog.at_level(logging.DEBUG):
        start_uploader(worker.url, poll_s=0.02)
        await until(lambda: len(worker.attempts) >= 2)
        del worker.answer[PHOTO]
        await until(lambda: BOOMERANG in store.meta(capture_id).failed_files)
        worker.token = "rotated"
        put_video(store, capture_id, VIDEO)
        await until(lambda: any("token" in m for m in logged(caplog, logging.ERROR)))

    assert TOKEN not in caplog.text
    assert "/api/captures" not in caplog.text
    assert f"127.0.0.1:{worker.url.rsplit(':', 1)[1]}" not in caplog.text


def write_config(tmp_path: Path, worker_url: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        'share_base_url = "https://photo.example.invalid/p"\nmedia_dir = "media"\n'
        f'[camera]\nkind = "fake"\n[upload]\nurl = "{worker_url}"\ntoken = "{TOKEN}"\n',
        encoding="utf-8",
    )
    return path


async def test_the_app_uploads_kept_captures_and_shutdown_cancels_cleanly_mid_upload(
    tmp_path, store, worker, make_booth, camera, caplog
):
    reloader = ConfigReloader(write_config(tmp_path, worker.url))
    assert reloader.config.media_dir == store.root
    capture_id = kept(store, videos=(BOOMERANG,))
    arrived, release = worker.hold(capture_id, BOOMERANG)
    app = create_app(make_booth(settings=reloader.settings), camera, store, reloader, None)

    with caplog.at_level(logging.WARNING):
        async with asyncio.timeout(5):
            async with app.router.lifespan_context(app):
                assert await asyncio.to_thread(arrived.wait, 5)
        release.set()
        await asyncio.sleep(0.3)

    assert worker.names(attempts=True) == [(capture_id, n) for n in (PHOTO, SHARED_META, BOOMERANG)]
    assert at_or_above(caplog, logging.WARNING) == []


async def test_the_app_does_not_upload_without_an_upload_table(tmp_path, store, make_booth, camera, worker):
    path = write_config(tmp_path, worker.url)
    path.write_text(path.read_text(encoding="utf-8").split("[upload]")[0], encoding="utf-8")
    reloader = ConfigReloader(path)
    kept(store)
    app = create_app(make_booth(settings=reloader.settings), camera, store, reloader, None)

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.3)

    assert worker.attempts == []
