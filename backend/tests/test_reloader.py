import asyncio
import logging
from http import HTTPStatus
from pathlib import Path

from photobooth import reloader as reloader_module
from photobooth.api import create_app
from photobooth.reloader import CACHE_NAME, ConfigReloader
from photobooth.session import BoothState, Screen
from photobooth.store import CaptureStore

LOCAL_BASE = "https://local.example.invalid/p"
REMOTE_BASE = "https://remote.example.invalid/s"


def local_toml(top: str = "", countdown_s: float = 0.6, record_start_s: float = 0.2) -> str:
    return f"""share_base_url = "{LOCAL_BASE}"
media_dir = "media"
{top}

[camera]
kind = "fake"

[timings]
countdown_s = {countdown_s}
record_start_s = {record_start_s}
flash_s = 0.3
boomerang_s = 0.2
delete_window_s = 0.5
result_timeout_s = 30.0
message_s = 0.3
"""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def drain(queue: asyncio.Queue) -> list[BoothState]:
    states = []
    while not queue.empty():
        states.append(queue.get_nowait())
    return states


def countdown_numbers(states: list[BoothState]) -> list[int]:
    return [state.remaining for state in states if state.screen is Screen.COUNTDOWN]


def warnings_logged(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


async def test_a_local_countdown_edit_applies_to_the_next_countdown_not_the_running_one(
    tmp_path, make_booth, await_screen
):
    path = write(tmp_path / "config.toml", local_toml())
    reloader = ConfigReloader(path)
    booth = make_booth(settings=reloader.settings)
    await booth.start()
    events = booth.subscribe()

    await booth.tap()
    write(path, local_toml(countdown_s=1.2, record_start_s=0.4))
    await reloader.poll()
    await await_screen(booth, Screen.RESULT)
    running = countdown_numbers(drain(events))

    await booth.new_photo()
    await await_screen(booth, Screen.CAPTURING)
    following = countdown_numbers(drain(events))

    assert running == [1]
    assert following == [2, 1]


async def test_a_second_poll_sends_the_etag_and_a_304_changes_nothing(tmp_path, remote):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n', etag='"v1"')
    reloader = ConfigReloader(write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"')))
    remote.serve('share_base_url = "https://changed.example.invalid/x"\n', etag='"v1"')

    await reloader.poll()

    assert remote.requests[0].get("If-None-Match") is None
    assert remote.requests[1].get("If-None-Match") == '"v1"'
    assert reloader.settings.share_base_url == REMOTE_BASE


async def test_an_invalid_remote_document_keeps_the_settings_and_warns_once(tmp_path, remote, caplog):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n[timings]\ncountdown_s = 0.9\n')
    reloader = ConfigReloader(write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"')))
    remote.serve('share_base_url = "https://changed.example.invalid/x"\n[timings]\ncountdwn_s = 2\n')

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await reloader.poll()

    assert len(remote.requests) == 4
    assert (reloader.settings.share_base_url, reloader.settings.timings.countdown_s) == (REMOTE_BASE, 0.9)
    [warning] = warnings_logged(caplog)
    assert "countdwn_s" in warning


async def test_a_failing_remote_keeps_the_settings_and_warns_once(tmp_path, remote, caplog):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n')
    reloader = ConfigReloader(write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"')))
    remote.fail(HTTPStatus.SERVICE_UNAVAILABLE)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await reloader.poll()

    assert reloader.settings.share_base_url == REMOTE_BASE
    [warning] = warnings_logged(caplog)
    assert "503" in warning


async def test_an_unreachable_remote_at_startup_uses_the_cached_copy(tmp_path, remote, caplog):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n[timings]\ncountdown_s = 0.9\n')
    path = write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"'))
    ConfigReloader(path)
    remote.stop()

    with caplog.at_level(logging.WARNING):
        rebooted = ConfigReloader(path)

    assert (rebooted.settings.share_base_url, rebooted.settings.timings.countdown_s) == (REMOTE_BASE, 0.9)
    [warning] = warnings_logged(caplog)
    assert "http://127.0.0.1" in warning
    assert "photobooth.toml" not in warning


async def test_an_invalid_remote_at_startup_uses_the_cached_copy(tmp_path, remote, caplog):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n[timings]\ncountdown_s = 0.9\n')
    path = write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"'))
    ConfigReloader(path)
    remote.serve('share_base_url = "https://changed.example.invalid/x"\n[timings]\ncountdwn_s = 2\n')

    with caplog.at_level(logging.WARNING):
        rebooted = ConfigReloader(path)

    assert (rebooted.settings.share_base_url, rebooted.settings.timings.countdown_s) == (REMOTE_BASE, 0.9)
    [warning] = warnings_logged(caplog)
    assert "countdwn_s" in warning


async def test_a_valid_local_edit_applies_while_the_remote_is_invalid(tmp_path, remote, caplog):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n')
    path = write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"'))
    reloader = ConfigReloader(path)
    remote.serve("[timings]\ncountdwn_s = 2\n")

    with caplog.at_level(logging.WARNING):
        write(path, local_toml(f'remote_config_url = "{remote.url}"', countdown_s=1.2, record_start_s=0.4))
        await reloader.poll()
        await reloader.poll()

    assert (reloader.settings.share_base_url, reloader.settings.timings.countdown_s) == (REMOTE_BASE, 1.2)
    [warning] = warnings_logged(caplog)
    assert "countdwn_s" in warning


async def test_the_cache_is_kept_in_the_media_state_directory_through_store_recovery(tmp_path, remote):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n')
    ConfigReloader(write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"')))
    cache = tmp_path / "media" / ".state" / CACHE_NAME
    assert cache.read_text(encoding="utf-8") == f'share_base_url = "{REMOTE_BASE}"\n'

    store = CaptureStore(tmp_path / "media")
    store.recover()
    store.prune_clips(min_free_bytes=2**62)

    assert cache.read_text(encoding="utf-8") == f'share_base_url = "{REMOTE_BASE}"\n'
    assert not (tmp_path / CACHE_NAME).exists()


async def test_an_unexpected_reload_error_is_logged_once_and_polling_continues(tmp_path, monkeypatch, caplog):
    reloader = ConfigReloader(write(tmp_path / "config.toml", local_toml("config_poll_s = 0.02")))
    calls = 0

    def broken_load(_path):
        nonlocal calls
        calls += 1
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(reloader_module, "load_config", broken_load)
    with caplog.at_level(logging.WARNING):
        polling = asyncio.create_task(reloader.run())
        async with asyncio.timeout(5):
            while calls < 4:
                await asyncio.sleep(0.01)
        polling.cancel()
        await asyncio.wait({polling})

    [warning] = warnings_logged(caplog)
    assert "disk on fire" in warning


async def test_a_port_change_needs_a_restart_while_a_timing_change_applies(tmp_path, caplog):
    path = write(tmp_path / "config.toml", local_toml())
    reloader = ConfigReloader(path)
    with caplog.at_level(logging.WARNING):
        write(path, local_toml("port = 9001", countdown_s=0.9))
        await reloader.poll()
        write(path, local_toml("port = 9001", countdown_s=0.8))
        await reloader.poll()

    assert reloader.config.port == 8000
    assert reloader.settings.timings.countdown_s == 0.8
    [warning] = warnings_logged(caplog)
    assert "port" in warning
    assert "restart" in warning


async def test_shutdown_cancels_the_reloader_without_warnings(tmp_path, remote, make_booth, camera, store, caplog):
    remote.serve(f'share_base_url = "{REMOTE_BASE}"\n')
    path = write(tmp_path / "config.toml", local_toml(f'remote_config_url = "{remote.url}"\nconfig_poll_s = 0.02'))
    reloader = ConfigReloader(path)
    app = create_app(make_booth(settings=reloader.settings), camera, store, reloader, None)

    with caplog.at_level(logging.WARNING):
        async with asyncio.timeout(5):
            async with app.router.lifespan_context(app):
                while len(remote.requests) < 5:
                    await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        polled = len(remote.requests)
        await asyncio.sleep(0.3)

    assert len(remote.requests) == polled
    assert warnings_logged(caplog) == []
