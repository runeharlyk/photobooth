import asyncio
import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
import uvicorn

from photobooth.api import create_app
from photobooth.session import Booth
from photobooth.sharing import qr_svg, share_url

SHARE_BASE = "https://photo.example.invalid/p"
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"


@pytest.fixture
async def serve(make_booth, camera, store):
    """Runs the app, lifespan included, on a real socket: streaming responses cannot be read through ASGITransport."""
    running: list[tuple[uvicorn.Server, asyncio.Task, httpx.AsyncClient]] = []

    async def start(frontend_dir: Path | None = None, booth: Booth | None = None) -> httpx.AsyncClient:
        app = create_app(booth or make_booth(), camera, store, SHARE_BASE, frontend_dir)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        config = uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1)
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve([sock]))
        while not server.started:
            await asyncio.sleep(0.01)
        client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}")
        running.append((server, task, client))
        return client

    yield start
    for server, task, client in running:
        await client.aclose()
        server.should_exit = True
        await task


@pytest.fixture
async def client(serve) -> httpx.AsyncClient:
    return await serve()


async def await_event(client: httpx.AsyncClient, predicate, timeout: float = 10.0) -> dict:
    async with asyncio.timeout(timeout):
        async with client.stream("GET", "/api/events") as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event = json.loads(line.removeprefix("data: "))
                    if predicate(event):
                        return event
    raise AssertionError("the event stream ended")


async def take_photo(client: httpx.AsyncClient) -> dict:
    assert (await client.post("/api/tap")).status_code == 204
    return await await_event(client, lambda event: event["screen"] == "result")


def test_share_url_joins_cleanly():
    assert share_url("https://x/p/", "abc") == "https://x/p/abc"
    assert share_url("https://x/p", "abc") == "https://x/p/abc"


def test_qr_svg_is_a_standalone_svg_document():
    root = ET.fromstring(qr_svg("https://x/p/abc"))
    assert root.tag == f"{SVG_NAMESPACE}svg"


async def test_events_start_with_current_state(client):
    event = await await_event(client, lambda _: True)
    assert event["screen"] == "idle"
    assert event["share_url"] is None


async def test_tap_to_result_over_http(client):
    event = await take_photo(client)
    capture_id = event["capture_id"]
    assert event["share_url"] == f"{SHARE_BASE}/{capture_id}"
    assert event["delete_available"] is True

    qr = await client.get(f"/api/captures/{capture_id}/qr.svg")
    assert qr.status_code == 200
    assert qr.headers["content-type"] == "image/svg+xml"
    assert ET.fromstring(qr.text).tag == f"{SVG_NAMESPACE}svg"

    photo = await client.get(f"/media/{capture_id}/photo.jpg")
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.content.startswith(b"\xff\xd8")


async def test_delete_within_window_is_204_and_removes_the_capture(client):
    capture_id = (await take_photo(client))["capture_id"]
    assert (await client.post("/api/delete")).status_code == 204
    assert (await client.get(f"/media/{capture_id}/photo.jpg")).status_code == 404
    assert (await client.get(f"/api/captures/{capture_id}/qr.svg")).status_code == 404
    assert (await await_event(client, lambda _: True))["screen"] == "idle"


async def test_delete_after_window_is_409(client):
    await take_photo(client)
    await await_event(client, lambda event: event["screen"] == "result" and not event["delete_available"])
    assert (await client.post("/api/delete")).status_code == 409


async def test_new_photo_starts_another_countdown(client):
    first = (await take_photo(client))["capture_id"]
    assert (await client.post("/api/new-photo")).status_code == 204
    event = await await_event(client, lambda event: event["screen"] in ("countdown", "capturing"))
    assert event["capture_id"] is None
    assert (await client.get(f"/media/{first}/photo.jpg")).status_code == 200


async def test_media_rejects_unlisted_names(client):
    capture_id = (await take_photo(client))["capture_id"]
    assert (await client.get(f"/media/{capture_id}/capture.json")).status_code == 404
    assert (await client.get(f"/media/{capture_id}/clip.mjpeg")).status_code == 404
    assert (await client.get("/media/..%2F/photo.jpg")).status_code == 404
    assert (await client.get(f"/media/{'a' * 22}/photo.jpg")).status_code == 404


async def test_qr_for_an_unknown_capture_is_404(client):
    assert (await client.get(f"/api/captures/{'a' * 22}/qr.svg")).status_code == 404
    assert (await client.get("/api/captures/not-an-id/qr.svg")).status_code == 404


async def test_preview_is_404_at_idle(client):
    assert (await client.get("/api/preview.mjpg")).status_code == 404


async def test_preview_right_after_a_tap_delivers_jpeg_parts_and_ends_with_the_countdown(client):
    assert (await client.post("/api/tap")).status_code == 204
    async with asyncio.timeout(10):
        async with client.stream("GET", "/api/preview.mjpg") as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
            body = b"".join([chunk async for chunk in response.aiter_bytes()])

    parts = body.split(b"--frame\r\n")[1:]
    assert parts
    for part in parts:
        headers, _, rest = part.partition(b"\r\n\r\n")
        assert b"Content-Type: image/jpeg" in headers
        jpeg = rest.removesuffix(b"\r\n")
        assert jpeg.startswith(b"\xff\xd8")
        assert f"Content-Length: {len(jpeg)}".encode() in headers


async def test_preview_is_404_on_the_result_screen(client):
    await take_photo(client)
    assert (await client.get("/api/preview.mjpg")).status_code == 404


async def test_a_disconnected_event_client_is_unsubscribed(serve, make_booth):
    booth = make_booth()
    client = await serve(booth=booth)
    await await_event(client, lambda _: True)
    async with asyncio.timeout(5):
        while booth._subscribers:
            await asyncio.sleep(0.01)


async def test_frontend_is_served_from_its_directory(serve, tmp_path):
    (tmp_path / "index.html").write_text("<title>booth</title>", encoding="utf-8")
    client = await serve(frontend_dir=tmp_path)
    page = await client.get("/")
    assert page.status_code == 200
    assert "<title>booth</title>" in page.text
    assert (await client.get("/api/preview.mjpg")).status_code == 404
