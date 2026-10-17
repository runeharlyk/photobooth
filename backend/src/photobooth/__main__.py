import argparse
from pathlib import Path

import uvicorn

from photobooth.api import create_app
from photobooth.camera import make_camera
from photobooth.config import load_config
from photobooth.session import Booth
from photobooth.store import CaptureStore

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
SHUTDOWN_GRACE_S = 2


def main() -> None:
    parser = argparse.ArgumentParser(prog="photobooth", description="Run the photobooth server.")
    parser.add_argument("--config", type=Path, required=True, help="path to the TOML configuration")
    config = load_config(parser.parse_args().config)

    store = CaptureStore(config.media_dir)
    store.recover()
    camera = make_camera(config.camera)
    booth = Booth(camera, store, config.timings)
    frontend_dir = FRONTEND_DIST if FRONTEND_DIST.is_dir() else None
    app = create_app(booth, camera, store, config.share_base_url, frontend_dir)
    uvicorn.run(app, host=config.host, port=config.port, timeout_graceful_shutdown=SHUTDOWN_GRACE_S)


if __name__ == "__main__":
    main()
