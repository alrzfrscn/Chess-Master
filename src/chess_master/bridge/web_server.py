"""Local static-asset server and an optional development JSON endpoint.

PyWebView loads the UI from ``http://127.0.0.1:<port>/`` rather than ``file://``
so ES modules, fetch and fonts behave exactly as they do in a browser. In normal
desktop use the JS API goes through PyWebView; the HTTP ``/api`` route is only
enabled for development and headless verification, never in the shipped window.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from chess_master.bridge.api import ChessMasterApi

__all__ = ["WebAppServer"]

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 2 * 1024 * 1024

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/x-chess-pgn", ".pgn")


class WebAppServer:
    """A loopback-only HTTP server for the packaged web UI."""

    def __init__(
        self,
        web_root: Path,
        api: ChessMasterApi,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        expose_api: bool = False,
    ) -> None:
        self.web_root = web_root.resolve()
        self.api = api
        self.host = host
        self._requested_port = port
        self.expose_api = expose_api
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}/"

    def _bind(self) -> ThreadingHTTPServer:
        if self._httpd is None:
            handler = self._make_handler()
            self._httpd = ThreadingHTTPServer((self.host, self._requested_port), handler)
            self._httpd.daemon_threads = True
            self._port = self._httpd.server_address[1]
            logger.info("Serving Chess Master UI at %s (HTTP API: %s)", self.url, self.expose_api)
        return self._httpd

    def start(self) -> str:
        if self._thread is not None:
            return self.url
        httpd = self._bind()
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="chess-master-web",
            daemon=True,
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None

    def serve_forever(self) -> None:
        """Run on the calling thread (used by ``--serve``)."""

        httpd = self._bind()
        try:
            httpd.serve_forever(poll_interval=0.25)
        finally:
            httpd.server_close()

    # -- handler ---------------------------------------------------------
    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        root = self.web_root
        api = self.api
        expose_api = self.expose_api
        allowed_methods = {
            name
            for name in dir(api)
            if not name.startswith("_") and callable(getattr(api, name))
        }

        class Handler(BaseHTTPRequestHandler):
            server_version = "ChessMaster/0.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                logger.debug("HTTP %s - %s", self.address_string(), format % args)

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path == "/api/health":
                    self._send_json(HTTPStatus.OK, api.get_api_info())
                    return
                self._serve_static(path)

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if not path.startswith("/api/"):
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"ok": False, "data": None, "error": {"code": "not_found", "message": "Unknown route."}},
                    )
                    return
                if not expose_api:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "ok": False,
                            "data": None,
                            "error": {
                                "code": "api_disabled",
                                "message": "The HTTP API is only available in development mode.",
                            },
                        },
                    )
                    return

                method_name = path.removeprefix("/api/").strip("/")
                if method_name not in allowed_methods:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "unknown_method", "message": f"Unknown API method: {method_name}"},
                        },
                    )
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > _MAX_BODY_BYTES:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "invalid_request", "message": "A JSON body is required."},
                        },
                    )
                    return

                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "invalid_json", "message": "The request body is not valid JSON."},
                        },
                    )
                    return

                args = payload.get("args", []) if isinstance(payload, dict) else []
                kwargs = payload.get("kwargs", {}) if isinstance(payload, dict) else {}
                if not isinstance(args, list) or not isinstance(kwargs, dict):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "invalid_request", "message": "Use {args: [], kwargs: {}}."},
                        },
                    )
                    return

                try:
                    result = getattr(api, method_name)(*args, **kwargs)
                except TypeError as exc:
                    result = {
                        "ok": False,
                        "data": None,
                        "error": {"code": "invalid_request", "message": str(exc)},
                    }
                self._send_json(HTTPStatus.OK, result)

            # -- static files -------------------------------------------
            def _serve_static(self, path: str) -> None:
                relative = unquote(path).lstrip("/")
                if not relative:
                    relative = "index.html"
                candidate = (root / relative).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                if candidate.is_dir():
                    candidate = candidate / "index.html"
                if not candidate.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                data = candidate.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def _send_json(self, status: HTTPStatus, payload: object) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

        return Handler
