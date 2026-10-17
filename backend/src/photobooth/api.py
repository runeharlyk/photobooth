import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from photobooth.camera import Camera, CameraError
from photobooth.session import Booth, BoothState, Screen
from photobooth.sharing import qr_svg, share_url
from photobooth.store import PUBLIC_FILES, CaptureStore

PREVIEW_SCREENS = (Screen.COUNTDOWN, Screen.CAPTURING)
PREVIEW_BOUNDARY = "frame"
FRAME_WAIT_S = 0.1
CAMERA_POLL_S = 0.02


def create_app(
    booth: Booth,
    camera: Camera,
    store: CaptureStore,
    share_base_url: str,
    frontend_dir: Path | None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await booth.start()
        try:
            yield
        finally:
            await booth.close()
            await asyncio.to_thread(camera.stop)

    app = FastAPI(lifespan=lifespan)

    def event_data(state: BoothState) -> str:
        payload = asdict(state)
        payload["share_url"] = share_url(share_base_url, state.capture_id) if state.capture_id else None
        return f"data: {json.dumps(payload)}\n\n"

    async def events() -> AsyncIterator[str]:
        queue = booth.subscribe()
        try:
            yield event_data(booth.state)
            while True:
                yield event_data(await queue.get())
        finally:
            booth.unsubscribe(queue)

    async def preview_parts() -> AsyncIterator[bytes]:
        after_seq = 0
        while booth.state.screen in PREVIEW_SCREENS:
            if not camera.running:
                await asyncio.sleep(CAMERA_POLL_S)
                continue
            try:
                frame = await asyncio.to_thread(camera.next_frame, after_seq, FRAME_WAIT_S)
            except TimeoutError:
                continue
            except CameraError:
                return
            after_seq = frame.seq
            head = f"--{PREVIEW_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame.jpeg)}\r\n\r\n"
            yield head.encode("ascii") + frame.jpeg + b"\r\n"

    @app.get("/api/events")
    async def stream_events() -> StreamingResponse:
        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/tap", status_code=204)
    async def tap() -> None:
        await booth.tap()

    @app.post("/api/new-photo", status_code=204)
    async def new_photo() -> None:
        await booth.new_photo()

    @app.post("/api/delete", status_code=204)
    async def delete() -> None:
        if not await booth.delete():
            raise HTTPException(status_code=409)

    @app.get("/api/preview.mjpg")
    async def preview() -> StreamingResponse:
        if booth.state.screen not in PREVIEW_SCREENS:
            raise HTTPException(status_code=404)
        return StreamingResponse(
            preview_parts(), media_type=f"multipart/x-mixed-replace; boundary={PREVIEW_BOUNDARY}"
        )

    @app.get("/api/captures/{capture_id}/qr.svg")
    async def qr(capture_id: str) -> Response:
        try:
            known = store.exists(capture_id)
        except ValueError:
            known = False
        if not known:
            raise HTTPException(status_code=404)
        return Response(qr_svg(share_url(share_base_url, capture_id)), media_type="image/svg+xml")

    @app.get("/media/{capture_id}/{name}")
    async def media(capture_id: str, name: str) -> FileResponse:
        if name not in PUBLIC_FILES:
            raise HTTPException(status_code=404)
        try:
            path = store.path(capture_id, name)
        except ValueError:
            raise HTTPException(status_code=404) from None
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    if frontend_dir is not None:
        app.mount("/", StaticFiles(directory=frontend_dir, html=True))

    return app
