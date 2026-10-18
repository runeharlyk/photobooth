import asyncio
import http.client
import logging
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

from photobooth.config import Config, LiveSettings, ignored_remote_keys, load_config, with_remote

CACHE_NAME = "remote-config.cache.toml"
FETCH_TIMEOUT_S = 10
RESTART_FIELDS = ("camera", "media_dir", "host", "port", "remote_config_url", "config_poll_s")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteDocument:
    data: dict
    body: bytes
    etag: str | None


class ConfigReloader:
    """Keeps LiveSettings in step with the local config file and the remote document it names.

    `config` stays as loaded at startup: the fields in RESTART_FIELDS are read from it once.
    Each failure is logged when it first occurs, not again while it persists.
    """

    def __init__(self, path: Path) -> None:
        self.config = load_config(path)
        self.settings = LiveSettings(self.config.timings, self.config.share_base_url)
        self._path = path
        self._cache = path.parent / CACHE_NAME
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
            except Exception:
                logger.exception("reloading the config failed")

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
        try:
            effective = with_remote(local, remote.data if remote else {})
        except ValueError as error:
            self._fail(f"keeping the current settings: {error}")
            return None
        if remote is not None and remote is not self._remote:
            for key in ignored_remote_keys(remote.data):
                logger.warning("ignoring %r in the remote config: only share_base_url and [timings] apply", key)
            self._write_cache(remote.body)
        self._note_restart(local)
        self._local, self._remote = local, remote
        return effective

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
            return self._fetch(url)
        except (OSError, http.client.HTTPException) as error:
            self._fail(f"could not fetch {url}: {error}")
        except ValueError as error:
            self._fail(f"could not parse {url}: {error}")
        return self._remote or self._read_cache()

    def _fetch(self, url: str) -> RemoteDocument | None:
        request = urllib.request.Request(url)
        if self._remote is not None and self._remote.etag is not None:
            request.add_header("If-None-Match", self._remote.etag)
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
                body = response.read()
                etag = response.headers.get("ETag")
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == HTTPStatus.NOT_MODIFIED:
                return self._remote
            raise
        if self._remote is not None and body == self._remote.body:
            return self._remote
        return RemoteDocument(tomllib.loads(body.decode("utf-8")), body, etag)

    def _read_cache(self) -> RemoteDocument | None:
        try:
            body = self._cache.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return RemoteDocument(tomllib.loads(body.decode("utf-8")), body, None)
        except ValueError as error:
            self._fail(f"could not parse {self._cache}: {error}")
            return None

    def _write_cache(self, body: bytes) -> None:
        partial = self._cache.with_name(self._cache.name + ".partial")
        try:
            partial.write_bytes(body)
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

    def _fail(self, message: str) -> None:
        self._failures.add(message)
        if message not in self._failures_seen:
            logger.warning(message)
