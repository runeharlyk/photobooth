import asyncio
import threading
from collections.abc import Callable
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from photobooth.camera.fake import FakeCamera
from photobooth.config import LiveSettings, Timings
from photobooth.session import Booth, BoothState, Screen
from photobooth.store import CaptureStore


@pytest.fixture
def fast_timings() -> Timings:
    return Timings(
        countdown_s=0.6,
        record_start_s=0.2,
        flash_s=0.3,
        boomerang_s=0.2,
        delete_window_s=0.5,
        result_timeout_s=1.0,
        message_s=0.3,
    )


@pytest.fixture
def camera():
    fake = FakeCamera(fps=30)
    yield fake
    fake.stop()


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path / "media")


@pytest.fixture
async def make_booth(camera, store, fast_timings):
    booths: list[Booth] = []

    def make(booth_camera=None, timings: Timings | None = None, settings: LiveSettings | None = None) -> Booth:
        settings = settings or LiveSettings(timings or fast_timings, "https://photo.example.invalid/p")
        booth = Booth(booth_camera or camera, store, settings)
        booths.append(booth)
        return booth

    yield make
    for booth in booths:
        await booth.close()


@pytest.fixture
async def booth(make_booth) -> Booth:
    started = make_booth()
    await started.start()
    return started


async def wait_for_state(
    booth: Booth, predicate: Callable[[BoothState], bool], timeout: float = 10.0
) -> BoothState:
    queue = booth.subscribe()
    try:
        async with asyncio.timeout(timeout):
            state = booth.state
            while not predicate(state):
                state = await queue.get()
            return state
    finally:
        booth.unsubscribe(queue)


async def wait_for_screen(booth: Booth, screen: Screen, timeout: float = 10.0) -> BoothState:
    return await wait_for_state(booth, lambda state: state.screen is screen, timeout)


@pytest.fixture
def await_state():
    return wait_for_state


@pytest.fixture
def await_screen():
    return wait_for_screen


class RemoteConfig:
    """A real HTTP server on 127.0.0.1 serving one TOML document, honouring If-None-Match."""

    def __init__(self) -> None:
        self.body = b""
        self.etag: str | None = None
        self.status = HTTPStatus.OK
        self.requests: list[Message] = []
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                remote.requests.append(self.headers)
                if remote.status is not HTTPStatus.OK:
                    self.send_error(remote.status)
                    return
                if remote.etag is not None and self.headers.get("If-None-Match") == remote.etag:
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.OK)
                if remote.etag is not None:
                    self.send_header("ETag", remote.etag)
                self.send_header("Content-Length", str(len(remote.body)))
                self.end_headers()
                self.wfile.write(remote.body)

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/photobooth.toml"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05})

    def serve(self, text: str, etag: str | None = None) -> None:
        self.body = text.encode("utf-8")
        self.etag = etag
        self.status = HTTPStatus.OK

    def fail(self, status: HTTPStatus) -> None:
        self.status = status

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Closes the listening socket, so the URL refuses connections."""
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join()
        self._server.server_close()


@pytest.fixture
def remote():
    server = RemoteConfig()
    server.start()
    yield server
    server.stop()
