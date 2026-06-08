"""ABA installer service — FastAPI app served on localhost.

Endpoints land in submodules; this file just builds the app + boots uvicorn.
"""
from __future__ import annotations
import logging
import sys

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from aba_installer import __version__
from aba_installer.paths import installer_dir, logs_dir, port_file
from aba_installer.portpick import pick_port


_log = logging.getLogger("aba_installer")


def _configure_logging() -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
    log_path = logs_dir() / "installer.log"
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.info("aba-installer v%s starting", __version__)


def build_app() -> FastAPI:
    app = FastAPI(title="ABA Installer", version=__version__)

    @app.get("/ready")
    def ready() -> dict:
        """Liveness probe — returns 200 once the service is accepting requests.
        The setup.command script polls this to know when to `open` the browser."""
        return {"ok": True, "version": __version__}

    from aba_installer.control import router as control_router
    from aba_installer.auth import router as auth_router, callback_router as oauth_callback_router
    app.include_router(control_router)
    app.include_router(auth_router)
    app.include_router(oauth_callback_router)  # /callback for Sign in with Claude.ai

    # Static UI bundle. Serves /ui/<file> and / (returns index.html).
    ui_dir = Path(__file__).resolve().parent / "ui"
    if ui_dir.exists():
        app.mount("/ui", StaticFiles(directory=ui_dir), name="ui")

        @app.get("/", include_in_schema=False)
        def root() -> FileResponse:
            return FileResponse(ui_dir / "index.html")

    return app


def main() -> int:
    """Entry point — picks a port, starts uvicorn, returns exit code.
    The chosen port is persisted to installer_dir/port.txt so the LaunchAgent
    + bookmarks remain stable across restarts."""
    _configure_logging()
    pf = port_file()
    port = pick_port(state_file=pf)
    _log.info("listening on http://127.0.0.1:%d (port file: %s)", port, pf)

    import uvicorn
    uvicorn.run(
        build_app(),
        host="127.0.0.1",   # loopback only — avoids firewall prompt
        port=port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
