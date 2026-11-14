"""Isolated API sessions for concurrent browser clients.

One :class:`~chess_master.bridge.api.ChessMasterApi` holds one live game.  A
browser tab needs its own game while every tab shares one SQLite archive and one
Stockfish process, so this registry hands each session id a small API facade
wired to the shared :class:`~chess_master.storage.db.GameStore` and
:class:`~chess_master.engine.stockfish.StockfishManager`.

Sessions are evicted least-recently-used when the registry is full and after an
idle TTL, so memory stays bounded without a background thread.  Desktop mode
never uses this module: PyWebView keeps talking to its single ``ChessMasterApi``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.rooms import RoomManager
from chess_master.engine.stockfish import StockfishManager
from chess_master.storage.db import GameStore

__all__ = [
    "DEFAULT_IDLE_TTL",
    "DEFAULT_MAX_SESSIONS",
    "SESSION_ID_PATTERN",
    "SessionManager",
    "is_valid_session_id",
]

logger = logging.getLogger(__name__)

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_MAX_SESSIONS = 128
DEFAULT_IDLE_TTL = 2 * 60 * 60.0


def is_valid_session_id(value: Any) -> bool:
    """A session id is an opaque 1-64 character token, never a path or query."""

    return isinstance(value, str) and bool(SESSION_ID_PATTERN.fullmatch(value.strip()))


def is_valid_client_id(value: Any) -> bool:
    return is_valid_session_id(value)


class _Entry:
    __slots__ = ("api", "last_used")

    def __init__(self, api: ChessMasterApi, now: float) -> None:
        self.api = api
        self.last_used = now


class SessionManager:
    """LRU/TTL registry of per-client API facades over shared state."""

    def __init__(
        self,
        template_api: ChessMasterApi | None = None,
        *,
        worker: StockfishManager | None = None,
        store: GameStore | None = None,
        engine_path: str | None = None,
        db_path: Any = None,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        idle_ttl: float = DEFAULT_IDLE_TTL,
        clock: Callable[[], float] = time.monotonic,
        rooms: RoomManager | None = None,
    ) -> None:
        if worker is None and template_api is not None:
            worker = template_api.worker
        if store is None and template_api is not None:
            store = template_api.store
        self._owns_worker = worker is None
        self._owns_store = store is None
        self._rooms = rooms if rooms is not None else RoomManager()
        self._engine_path = engine_path
        self._worker: StockfishManager | None = worker
        self._store: GameStore = store if store is not None else GameStore(db_path)
        self._max_sessions = max(1, int(max_sessions))
        self._idle_ttl = float(idle_ttl)
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def store(self) -> GameStore:
        return self._store

    def api_for(self, session_id: str, client_id: str | None = None) -> ChessMasterApi:
        """The session's API, creating it on first use.

        Raises ``ValueError`` for an id the HTTP layer should have rejected.
        """

        if not is_valid_session_id(session_id):
            raise ValueError("Invalid session id.")
        key = str(session_id).strip()
        client = client_id if is_valid_client_id(client_id) else None
        with self._lock:
            now = self._clock()
            self._evict(now)
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry(self._create_api(client or "local", key), now)
                self._entries[key] = entry
                self._evict(now)
            elif client is not None:
                # A reload or SSE reconnect may arrive without the client header first.
                entry.api._bind_client(client)
            entry.last_used = now
            self._entries.move_to_end(key)
            return entry.api

    def close(self) -> None:
        """Detach every session, then release the shared state we own."""

        with self._lock:
            while self._entries:
                _, entry = self._entries.popitem(last=False)
                self._detach(entry)
        if self._owns_store:
            self._store.close()
        if self._owns_worker and self._worker is not None:
            self._worker.stop()

    # -- internals -------------------------------------------------------
    def _create_api(self, client_id: str, session_id: str | None = None) -> ChessMasterApi:
        if self._worker is None:
            self._worker = StockfishManager(path=self._engine_path)
        return ChessMasterApi(
            engine_path=self._engine_path,
            worker=self._worker,
            store=self._store,
            client_id=client_id,
            session_id=session_id,
            rooms=self._rooms,
        )

    def _evict(self, now: float) -> None:
        if self._idle_ttl > 0:
            for key, entry in list(self._entries.items()):
                if now - entry.last_used >= self._idle_ttl:
                    self._drop(key)
        while len(self._entries) > self._max_sessions:
            self._drop(next(iter(self._entries)))

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._detach(entry)

    @staticmethod
    def _detach(entry: _Entry) -> None:
        try:
            entry.api._detach()
        except Exception:  # pragma: no cover - eviction must never raise
            logger.debug("Could not detach a web session", exc_info=True)
