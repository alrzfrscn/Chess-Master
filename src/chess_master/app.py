"""Application entry point: serve the UI, then hand it to PyWebView."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.web_server import WebAppServer
from chess_master.paths import WEB_ROOT

__all__ = ["main", "run_app", "run_server"]

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Chess Master — Precision Play"
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
MIN_WIDTH = 1280
MIN_HEIGHT = 820
BACKGROUND = "#0B0E12"


def _storage_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    path = base / "ChessMaster" / "WebView"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChessMaster.Desktop.1")
    except Exception:  # pragma: no cover - cosmetic only
        logger.debug("Could not set the Windows app id", exc_info=True)


def run_server(port: int = 8765, *, engine_path: str | None = None) -> int:
    """Run the UI + development HTTP API headlessly (used by verification)."""

    api = ChessMasterApi(engine_path=engine_path)
    server = WebAppServer(WEB_ROOT, api, port=port, expose_api=True)
    try:
        server.start()
        print(f"Chess Master dev server: {server.url}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        # Keep the main thread alive; the HTTP server owns a daemon thread.
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Chess Master dev server.", flush=True)
        return 0
    finally:
        server.stop()
        api._shutdown()


def run_app(
    *,
    debug: bool = False,
    http_api: bool = False,
    smoke_test: bool = False,
    engine_path: str | None = None,
) -> int:
    """Launch the desktop window. Imports PyWebView lazily for headless use."""

    _set_windows_app_id()

    try:
        import webview
    except ImportError:
        logger.error(
            "PyWebView is not installed. Run `pip install -e .` inside the project virtualenv."
        )
        return 1

    api = ChessMasterApi(engine_path=engine_path)
    server = WebAppServer(WEB_ROOT, api, expose_api=http_api)
    try:
        server.start()
        window = webview.create_window(
            WINDOW_TITLE,
            f"{server.url}?desktop=1",
            js_api=api,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            background_color=BACKGROUND,
            text_select=True,
            easy_drag=False,
            zoomable=False,
            confirm_close=False,
            hidden=smoke_test,
        )

        if smoke_test:
            logger.info("Smoke-test window created (hidden); closing in 3 seconds.")

            def _close_after_delay() -> None:
                time.sleep(3.0)
                try:
                    window.destroy()
                except Exception:  # pragma: no cover - best-effort shutdown
                    logger.debug("Smoke-test destroy failed", exc_info=True)

            threading.Thread(target=_close_after_delay, daemon=True).start()

        webview.start(
            debug=debug,
            http_server=False,
            private_mode=False,
            storage_path=str(_storage_path()),
        )
        return 0
    finally:
        server.stop()
        api._shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chess-master",
        description="Chess Master — a precision chess desktop studio.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable PyWebView developer tools.")
    parser.add_argument(
        "--http-api",
        action="store_true",
        help="Expose the JSON API over loopback for browser-based UI testing (development only).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the web UI and development API without opening a desktop window.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for --serve (default 8765) or the desktop asset server (default: random).",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Open a hidden window, then close it after three seconds. Used by CI.",
    )
    parser.add_argument(
        "--engine",
        default=None,
        metavar="PATH",
        help="Path to the Stockfish executable (overrides STOCKFISH_PATH and auto-detection).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.serve:
        return run_server(args.port or 8765, engine_path=args.engine)
    return run_app(
        debug=args.debug,
        http_api=args.http_api,
        smoke_test=args.smoke_test,
        engine_path=args.engine,
    )


if __name__ == "__main__":
    raise SystemExit(main())
