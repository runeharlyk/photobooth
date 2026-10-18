import asyncio
import logging
import os
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

from photobooth.config import Config, LiveSettings, ignored_remote_keys, load_config, with_remote

CACHE_NAME = "remote-config.cache.toml"
STATE_DIR = ".state"
FETCH_TIMEOUT_S = 10
RESTART_FIELDS = ("camera", "media_dir", "host", "port", "remote_config_url", "config_poll_s")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteDocument:
    data: dict
    body: bytes
    etag: str | None
    from_cache: bool = False


class ConfigReloader:
    """Keeps LiveSettings in step with the local config file and the remote document it names.

    `config` stays as loaded at startup: the fields in RESTART_FIELDS are read from it once.
    A remote document that fails validation is ignored in favour of the last good one, which
    is cached under the media directory for the next boot. Each failure is logged when it
    first occurs, not again while it persists. Logs name only the remote's scheme and host,
    since the URL itself is the secret.
    """

    def __init__(self, path: Path) -> None:
        self.config = load_config(path)
        self.settings = LiveSettings(self.config.timings, self.config.share_base_url)
        self._path = path
        self._cache = self.config.media_dir / STATE_DIR / CACHE_NAME
        self._remote_label = _scheme_and_host(self.config.remote_config_url or "")
        self._local = self.config
        self._remote: RemoteDocument | None = None
        self._failures: set[str] = set()
        self._failures_seen: set[str] = set()
        self._restart_noted: dict[str, object] = {}
        self._apply(self._reload())

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.config.config_poll_s)
            try:
                await self.poll()
            except Exception as error:
                self._fail(f"reloading the config failed: {error!r}", exc_info=True)

    async def poll(self) -> None:
        self._apply(await asyncio.to_thread(self._reload))

    def _apply(self, effective: Config | None) -> None:
        if effective is not None:
            self.settings.timings = effective.timings
            self.settings.share_base_url = effective.share_base_url

    def _reload(self) -> Config | None:
        self._failures_seen, self._failures = self._failures, set()
        local = self._read_local()
        remote = self._read_remote()
        if local == self._local and remote is self._remote:
            return None
        settled = self._settle(local, remote)
        if settled is None:
            return None
        effective, used = settled
        if used is not None and used is not self._remote:
            for key in ignored_remote_keys(used.data):
                logger.warning("ignoring %r in the remote config: only share_base_url and [timings] apply", key)
            if not used.from_cache:
                self._write_cache(used.body)
        self._note_restart(local)
        self._local, self._remote = local, used
        return effective

    def _settle(self, local: Config, remote: RemoteDocument | None) -> tuple[Config, RemoteDocument | None] | None:
        """The effective config and the remote document it uses, or None to keep the current settings."""
        try:
            return with_remote(local, _data(remote)), remote
        except ValueError as error:
            if remote is self._remote:
                self._fail(f"keeping the current settings: {error}")
                return None
            self._fail(f"ignoring the remote config from {self._remote_label}: {error}")
        fallback = self._remote if self._remote is not None else self._read_cache()
        if fallback == remote:
            fallback = None
        try:
            return with_remote(local, _data(fallback)), fallback
        except ValueError as error:
            self._fail(f"keeping the current settings: {error}")
            return None

    def _read_local(self) -> Config:
        try:
            return load_config(self._path)
        except (OSError, ValueError) as error:
            self._fail(f"could not read {self._path}: {error}")
            return self._local

    def _read_remote(self) -> RemoteDocument | None:
        url = self.config.remote_config_url
        if url is None:
            return None
        try:
            fetched = self._fetch(url)
        except Exception as error:  # urllib and http.client quote the secret URL in their messages
            self._fail(f"could not fetch the remote config from {self._remote_label}: {_describe(error)}")
            return self._remote or self._read_cache()
        if fetched is None:
            return self._remote
        body, etag = fetched
        if self._remote is not None and body == self._remote.body:
            return self._remote
        try:
            return RemoteDocument(tomllib.loads(body.decode("utf-8")), body, etag)
        except ValueError as error:
            self._fail(f"could not parse the remote config from {self._remote_label}: {error}")
            return self._remote or self._read_cache()

    def _fetch(self, url: str) -> tuple[bytes, str | None] | None:
        """The body and ETag of the remote document, or None when the server answers 304."""
        request = urllib.request.Request(url)
        if self._remote is not None and self._remote.etag is not None:
            request.add_header("If-None-Match", self._remote.etag)
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
                return response.read(), response.headers.get("ETag")
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == HTTPStatus.NOT_MODIFIED:
                return None
            raise

    def _read_cache(self) -> RemoteDocument | None:
        try:
            body = self._cache.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            self._fail(f"could not read {self._cache}: {error}")
            return None
        try:
            return RemoteDocument(tomllib.loads(body.decode("utf-8")), body, None, from_cache=True)
        except ValueError as error:
            self._fail(f"could not parse {self._cache}: {error}")
            return None

    def _write_cache(self, body: bytes) -> None:
        partial = self._cache.with_name(self._cache.name + ".partial")
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            with partial.open("wb") as file:
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.replace(partial, self._cache)
        except OSError as error:
            self._fail(f"could not cache the remote config in {self._cache}: {error}")

    def _note_restart(self, local: Config) -> None:
        changed = {
            name: getattr(local, name)
            for name in RESTART_FIELDS
            if getattr(local, name) != getattr(self.config, name)
        }
        for name, value in changed.items():
            if self._restart_noted.get(name) != value:
                logger.warning("%s changed in %s and needs a restart to apply", name, self._path)
        self._restart_noted = changed

    def _fail(self, message: str, exc_info: bool = False) -> None:
        self._failures.add(message)
        if message not in self._failures_seen:
            logger.warning(message, exc_info=exc_info)


def _data(remote: RemoteDocument | None) -> dict:
    return remote.data if remote is not None else {}


def _describe(error: Exception) -> str:
    """Names a fetch failure without its message, which may quote the secret URL."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        return type(error.reason).__name__
    return type(error).__name__


def _scheme_and_host(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme and parts.hostname:
        return f"{parts.scheme}://{parts.hostname}"
    return "a URL without a scheme and host"
