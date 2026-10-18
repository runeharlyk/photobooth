import asyncio
import io
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http import HTTPStatus
from typing import BinaryIO

from photobooth.config import UploadConfig
from photobooth.redact import describe_failure, scheme_and_host
from photobooth.store import BOOMERANG, PHOTO, SHARED_META, VIDEO, VIDEOS, CaptureMeta, CaptureState, CaptureStore

POLL_S = 30.0
RETRY_FIRST_S = 5.0
RETRY_CAP_S = 300.0
REQUEST_TIMEOUT_S = 30
STALL_S = 15 * 60.0
TOKEN_RETRY_S = 30 * 60.0
USER_AGENT = "photobooth-uploader"
CONTENT_TYPES = {PHOTO: "image/jpeg", SHARED_META: "application/json", BOOMERANG: "video/mp4", VIDEO: "video/mp4"}

logger = logging.getLogger(__name__)


class _Unavailable(Exception):
    """The Worker could not take the upload now; the message names the failure without secrets."""


class _TokenRejected(Exception):
    pass


_TOKEN_REJECTED = "the token was rejected"


class Uploader:
    """Uploads kept captures to the sharing Worker, one file at a time.

    Each pass uploads the photo and meta.json of every due capture, oldest first, before any
    boomerang or video, so a guest waiting at the booth is not queued behind a backlog of videos;
    a wake during a pass starts a new one. Progress is recorded per file in the capture's
    metadata, so a restart resumes where it stopped. A failure to reach the Worker is retried
    with exponential backoff and logged once until it changes; a rejected token is logged once
    and retried every token_retry_s, wakes aside. Logs name only the Worker's scheme and host,
    never the token.

    `on_stalled` is told whether uploads are stalled: the token is rejected, or files have been
    due for stall_s with none uploaded, counting from when they became due or from the last
    successful upload, whichever is later. A successful upload clears it at once.
    """

    def __init__(
        self,
        store: CaptureStore,
        config: UploadConfig,
        slot: asyncio.Semaphore,
        wake: asyncio.Event,
        on_stalled: Callable[[bool], None],
        poll_s: float = POLL_S,
        retry_first_s: float = RETRY_FIRST_S,
        retry_cap_s: float = RETRY_CAP_S,
        timeout_s: float = REQUEST_TIMEOUT_S,
        stall_s: float = STALL_S,
        token_retry_s: float = TOKEN_RETRY_S,
    ) -> None:
        self._store = store
        self._config = config
        self._slot = slot
        self._wake = wake
        self._on_stalled = on_stalled
        self._poll_s = poll_s
        self._retry_first_s = retry_first_s
        self._retry_cap_s = retry_cap_s
        self._timeout_s = timeout_s
        self._stall_s = stall_s
        self._token_retry_s = token_retry_s
        self._label = scheme_and_host(config.url)
        self._failure: str | None = None
        self._waiting_since: float | None = None
        self._settled: set[str] = set()
        self._unreadable: set[str] = set()

    async def run(self) -> None:
        logger.info("uploading kept captures to %s", self._label)
        retry_s = 0.0
        while True:
            self._wake.clear()
            try:
                if not await self._upload_due():
                    continue
                retry_s = 0.0
            except _TokenRejected:
                if self._failure != _TOKEN_REJECTED:
                    logger.error(
                        "%s rejected the upload token; retrying every %g s", self._label, self._token_retry_s
                    )
                self._failure = _TOKEN_REJECTED
                self._publish()
                await asyncio.sleep(self._token_retry_s)
                continue
            except _Unavailable as failure:
                self._note_failure(str(failure))
                retry_s = min(self._retry_cap_s, retry_s * 2 or self._retry_first_s)
            except Exception as error:
                self._note_failure(f"unexpected error {error!r}", exc_info=True)
                retry_s = min(self._retry_cap_s, retry_s * 2 or self._retry_first_s)
            until_stalled = self._publish()
            wait_s = retry_s or self._poll_s
            await self._sleep(wait_s if until_stalled is None else min(wait_s, until_stalled))

    async def _upload_due(self) -> bool:
        """Uploads every due file; False when woken part-way, so the caller plans again at once."""
        due = await asyncio.to_thread(self._due)
        if not due:
            self._waiting_since = None
        elif self._waiting_since is None:
            self._waiting_since = time.monotonic()
        for index, (capture_id, name) in enumerate(due):
            if index and self._wake.is_set():
                return False
            async with self._slot:
                sent = await asyncio.to_thread(self._upload, capture_id, name)
            if sent:
                self._waiting_since = time.monotonic()
                if self._failure is not None:
                    logger.info("uploads to %s resumed", self._label)
                    self._failure = None
                self._publish()
        return True

    def _publish(self) -> float | None:
        """Tells on_stalled whether uploads are stalled; returns the seconds until they would be, if ahead."""
        left = None if self._waiting_since is None else self._waiting_since + self._stall_s - time.monotonic()
        self._on_stalled(self._failure == _TOKEN_REJECTED or (left is not None and left <= 0))
        return left if left is not None and left > 0 else None

    async def _sleep(self, seconds: float) -> None:
        try:
            async with asyncio.timeout(seconds):
                await self._wake.wait()
        except TimeoutError:
            pass

    def _due(self) -> list[tuple[str, str]]:
        captures: list[tuple[str, str, list[str]]] = []
        for capture_id in self._store.capture_ids():
            if capture_id in self._settled:
                continue
            try:
                meta = self._store.meta(capture_id)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError):
                if capture_id not in self._unreadable:
                    logger.warning("could not read capture %s to upload it", capture_id, exc_info=True)
                    self._unreadable.add(capture_id)
                continue
            if meta.state is not CaptureState.KEPT:
                continue
            if meta.uploaded:
                self._settled.add(capture_id)
                continue
            captures.append((meta.captured_at, capture_id, self._store.unuploaded(meta)))
        captures.sort()
        stills = [(capture_id, name) for _, capture_id, names in captures for name in names if name not in VIDEOS]
        videos = [(capture_id, name) for _, capture_id, names in captures for name in names if name in VIDEOS]
        return stills + videos

    def _upload(self, capture_id: str, name: str) -> bool:
        """Uploads one file and records the outcome; True when the Worker stored it."""
        try:
            meta = self._store.meta(capture_id)
            if name not in self._store.unuploaded(meta):
                return False
            body = self._body(meta, name)
        except FileNotFoundError:
            if self._store.exists(capture_id):
                logger.warning("%s of capture %s is missing; it will not be uploaded", name, capture_id)
                self._record(self._store.record_upload_failure, capture_id, name)
            return False
        with body:
            status = self._put(capture_id, name, body)
        if status == HTTPStatus.UNAUTHORIZED:
            raise _TokenRejected
        if status == HTTPStatus.BAD_REQUEST:
            logger.error(
                "%s rejected %s of capture %s with HTTP 400; it will not be retried", self._label, name, capture_id
            )
            self._record(self._store.record_upload_failure, capture_id, name)
            return False
        if not 200 <= status < 300:
            raise _Unavailable(f"HTTP {status}")
        self._record(self._store.record_upload, capture_id, name)
        return True

    def _body(self, meta: CaptureMeta, name: str) -> BinaryIO:
        if name == SHARED_META:
            return io.BytesIO(json.dumps({"captured_at": meta.captured_at, "event": meta.event}).encode("utf-8"))
        return self._store.path(meta.id, name).open("rb")

    def _put(self, capture_id: str, name: str, body: BinaryIO) -> int:
        """Sends the body as a stream: http.client then writes it block by block, each block with
        its own timeout, where a bytes body is one write whose timeout bounds the whole upload."""
        try:
            request = urllib.request.Request(
                f"{self._config.url}/api/captures/{capture_id}/{name}",
                data=body,
                method="PUT",
                headers={
                    "Authorization": f"Bearer {self._config.token}",
                    "Content-Type": CONTENT_TYPES[name],
                    "Content-Length": str(_size(body)),
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code
        except Exception as error:  # the message may quote the request, its token included
            raise _Unavailable(describe_failure(error)) from None

    @staticmethod
    def _record(record: Callable[[str, str], None], capture_id: str, name: str) -> None:
        try:
            record(capture_id, name)
        except FileNotFoundError:
            pass

    def _note_failure(self, failure: str, exc_info: bool = False) -> None:
        if failure != self._failure:
            logger.warning(
                "uploads to %s are failing (%s); retrying with backoff", self._label, failure, exc_info=exc_info
            )
        self._failure = failure


def _size(body: BinaryIO) -> int:
    size = body.seek(0, io.SEEK_END)
    body.seek(0)
    return size
