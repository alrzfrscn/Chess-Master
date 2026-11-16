"""Local static-asset server and an optional development JSON endpoint.

PyWebView loads the UI from ``http://127.0.0.1:<port>/`` rather than ``file://``
so ES modules, fetch and fonts behave exactly as they do in a browser. In normal
desktop use the JS API goes through PyWebView; the HTTP ``/api`` route is only
enabled for development and headless verification, never in the shipped window.

Browser clients send ``X-Chess-Session`` / ``X-Chess-Client``; the server then
routes the call to that session's isolated API through :class:`SessionManager`.
Requests without a session header keep using the single ``self.api`` facade, so
the desktop window and the existing HTTP tests are untouched.  ``GET
/api/events`` streams a small state fingerprint whenever the session changes so
the page can poll only when something actually happened.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.sessions import (
    SessionManager,
    is_valid_client_id,
    is_valid_session_id,
)

__all__ = ["WebAppServer"]

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 2 * 1024 * 1024
_EVENT_INTERVAL = 0.25
_EVENT_HEARTBEAT = 15.0

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/x-chess-pgn", ".pgn")


class _ThreadingHTTPServer(ThreadingHTTPServer):
    # Chromium opens a burst of parallel module requests; the stdlib backlog of 5 is too small.
    request_queue_size = 128

    def handle_error(self, request, client_address) -> None:
        # A page reload or closed tab aborts in-flight responses; that is not an error.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class WebAppServer:
    """An HTTP server for the packaged web UI (loopback or LAN when configured)."""

    def __init__(
        self,
        web_root: Path,
        api: ChessMasterApi,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        expose_api: bool = False,
        sessions: SessionManager | None = None,
    ) -> None:
        self.web_root = web_root.resolve()
        self.api = api
        self.host = host
        self._requested_port = port
        self.expose_api = expose_api
        self.sessions = sessions
        self._owns_sessions = False
        self._sessions_lock = threading.Lock()
        self._closing = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}/"

    def _bind(self) -> ThreadingHTTPServer:
        if self._httpd is None:
            handler = self._make_handler()
            self._httpd = _ThreadingHTTPServer((self.host, self._requested_port), handler)
            self._httpd.daemon_threads = True
            self._port = self._httpd.server_address[1]
            logger.info("Serving Chess Master UI at %s (HTTP API: %s)", self.url, self.expose_api)
        return self._httpd

    def start(self) -> str:
        if self._thread is not None:
            return self.url
        self._closing.clear()
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
        self._closing.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._httpd = None
            self._thread = None
        if self._owns_sessions and self.sessions is not None:
            self.sessions.close()
            self.sessions = None
            self._owns_sessions = False

    def serve_forever(self) -> None:
        """Run on the calling thread (used by ``--serve``)."""

        httpd = self._bind()
        try:
            httpd.serve_forever(poll_interval=0.25)
        finally:
            httpd.server_close()

    # -- session routing -------------------------------------------------
    def _api_for(self, session_header: str | None, client_header: str | None) -> ChessMasterApi:
        """The session's API, or the default facade when no header was sent."""

        raw = (session_header or "").strip()
        if not raw:
            return self.api
        if not is_valid_session_id(raw):
            raise ValueError("Invalid session id.")
        client = (client_header or "").strip()
        client = client if is_valid_client_id(client) else None
        if self.sessions is None:
            with self._sessions_lock:
                if self.sessions is None:
                    self.sessions = SessionManager(self.api)
                    self._owns_sessions = True
        return self.sessions.api_for(raw, client)

    # -- handler ---------------------------------------------------------
    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        root = self.web_root
        api = self.api
        expose_api = self.expose_api
        server = self
        allowed_methods = {
            name
            for name in dir(api)
            if not name.startswith("_") and callable(getattr(api, name))
        }

        class Handler(BaseHTTPRequestHandler):
            server_version = "ChessMaster/0.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                logger.debug("HTTP %s - %s", self.address_string(), format % args)

            def _deny(self, status: HTTPStatus, code: str, message: str) -> None:
                self._send_json(
                    status,
                    {
                        "ok": False,
                        "data": None,
                        "error": {"code": code, "message": message},
                    },
                )

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/health":
                    self._send_json(HTTPStatus.OK, api.get_api_info())
                    return
                if parsed.path == "/api/events":
                    self._serve_events(parsed.query)
                    return
                self._serve_static(parsed.path)

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if not path.startswith("/api/"):
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"ok": False, "data": None, "error": {"code": "not_found", "message": "Unknown route."}},
                    )
                    return
                # A cross-site form can POST a simple content type without a CORS
                # preflight.  Requiring JSON plus checking the browser's origin
                # headers is the CSRF gate for the public web build.
                content_type = (
                    self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                )
                if content_type != "application/json":
                    self._deny(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_request",
                        "Content-Type must be application/json.",
                    )
                    return
                if self.headers.get("Sec-Fetch-Site", "").strip().lower() == "cross-site":
                    self._deny(
                        HTTPStatus.FORBIDDEN,
                        "forbidden_origin",
                        "Cross-origin API requests are not allowed.",
                    )
                    return
                origin = (self.headers.get("Origin") or "").strip()
                host = self.headers.get("Host", "").strip().lower()
                if origin and origin.lower() != "null" and host:
                    if urlparse(origin).netloc.lower() != host:
                        self._deny(
                            HTTPStatus.FORBIDDEN,
                            "forbidden_origin",
                            "Cross-origin API requests are not allowed.",
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
                    target = server._api_for(
                        self.headers.get("X-Chess-Session"),
                        self.headers.get("X-Chess-Client"),
                    )
                except ValueError:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "data": None,
                            "error": {
                                "code": "invalid_session",
                                "message": "Use a 1-64 character session token.",
                            },
                        },
                    )
                    return

                try:
                    result = getattr(target, method_name)(*args, **kwargs)
                except TypeError as exc:
                    result = {
                        "ok": False,
                        "data": None,
                        "error": {"code": "invalid_request", "message": str(exc)},
                    }
                self._send_json(HTTPStatus.OK, result)

            # -- real-time stream --------------------------------------
            def _serve_events(self, query: str) -> None:
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
                requested = (parse_qs(query).get("session") or [""])[0]
                session_token = requested or self.headers.get("X-Chess-Session")
                client_token = self.headers.get("X-Chess-Client")
                try:
                    target = server._api_for(session_token, client_token)
                except ValueError:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "ok": False,
                            "data": None,
                            "error": {
                                "code": "invalid_session",
                                "message": "Use a 1-64 character session token.",
                            },
                        },
                    )
                    return

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                # shortcut: HTTP/1.0 close-delimited SSE on loopback only; use
                # chunked HTTP/1.1 if a buffering proxy ever sits in front.

                last: dict[str, Any] | None = None
                last_sent = time.monotonic()
                while not server._closing.is_set():
                    if session_token and getattr(target._engine, "detached", False):
                        # The registry evicted this session; wake the new one
                        # instead of streaming a detached coordinator forever.
                        try:
                            target = server._api_for(session_token, client_token)
                        except ValueError:
                            return
                    snapshot = _state_fingerprint(target)
                    if snapshot != last:
                        last = snapshot
                        last_sent = time.monotonic()
                        if not self._write_event("state", snapshot):
                            return
                    elif time.monotonic() - last_sent >= _EVENT_HEARTBEAT:
                        last_sent = time.monotonic()
                        try:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionError, OSError):
                            return
                    server._closing.wait(_EVENT_INTERVAL)

            def _write_event(self, event: str, data: dict[str, Any]) -> bool:
                body = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                try:
                    self.wfile.write(body.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionError, OSError):
                    return False
                return True

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
                # The 1.8 MB engine binary is content-addressed by version and
                # safe to keep in the browser cache; the app shell is not.
                cache = (
                    "public, max-age=604800"
                    if candidate.suffix.lower() == ".wasm"
                    else "no-cache"
                )
                self.send_header("Cache-Control", cache)
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


def _state_fingerprint(api: ChessMasterApi) -> dict[str, Any]:
    """The smallest payload that tells a browser tab something changed."""

    fast = getattr(api, "_state_fingerprint", None)
    if callable(fast):
        return fast()
    payload = api.get_game_state()
    state = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    if not payload.get("ok"):
        return {"ok": False, "error": (payload.get("error") or {}).get("code")}
    engine = state.get("engine") or {}
    evaluation = state.get("evaluation") or {}
    review = state.get("review") or {}
    progress = review.get("progress") or {}
    room = state.get("room") or {}
    status = state.get("status") or {}
    return {
        "ok": True,
        "game_id": state.get("game_id"),
        "revision": state.get("revision"),
        "ply": state.get("ply"),
        "total_plies": state.get("total_plies"),
        "status": status.get("state"),
        "turn": state.get("turn"),
        "draw_offer_by": status.get("draw_offer_by"),
        "room_id": room.get("room_id"),
        "room_status": room.get("status"),
        "seat": room.get("seat"),
        "seats": room.get("seats"),
        "fair_play": room.get("fair_play"),
        "rematch_available": room.get("rematch_available"),
        "thinking": bool(engine.get("thinking")),
        "engine_status": engine.get("status"),
        "evaluation_updated_at": evaluation.get("updated_at"),
        "depth": evaluation.get("depth"),
        "score_text": evaluation.get("score_text"),
        "review_status": review.get("status"),
        "review_signature": review.get("signature"),
        "review_done": progress.get("done"),
        "review_total": progress.get("total"),
    }
