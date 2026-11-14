"""Structured PyWebView JS API.

Every public method returns the same envelope so JavaScript never has to parse
Python exceptions::

    {"ok": true,  "data": {...}, "error": null}
    {"ok": false, "data": null, "error": {"code": "...", "message": "..."}}

The API owns a re-entrant lock because PyWebView can dispatch JS calls and
window lifecycle callbacks on different threads.  Phase 2 adds a single
Stockfish worker behind :class:`EngineCoordinator`; no method waits for a
search, and every result is checked against ``(game_id, revision, ply, fen)``
before it reaches the UI.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chess_master import __version__
from chess_master.analysis.openings import get_default_book
from chess_master.analysis.review import CONFIRM_DEPTH_CAP, REVIEW_PRESETS
from chess_master.analysis.winprob import model_info
from chess_master.bridge.rooms import Room, RoomManager
from chess_master.engine.clock import TIME_CONTROL_PRESETS
from chess_master.engine.coordinator import EngineCoordinator
from chess_master.engine.errors import ChessMasterError, InvalidInputError, InvalidModeError
from chess_master.engine.game import (
    DEFAULT_BOT_ELO,
    MAX_BOT_ELO,
    MIN_BOT_ELO,
    VALID_MODES,
    GameSession,
)
from chess_master.engine.stockfish import (
    DEFAULT_DEPTH,
    DEFAULT_MULTIPV,
    ELO_PRESETS,
    StockfishManager,
)
from chess_master.models.drills import drills_payload, get_drill
from chess_master.models.samples import samples_payload
from chess_master.storage.db import GameStore

__all__ = ["ChessMasterApi"]

logger = logging.getLogger(__name__)

_ANALYSIS_DEFAULTS = {
    "enabled": True,
    "arrows": False,
    "depth": DEFAULT_DEPTH,
    "multipv": DEFAULT_MULTIPV,
}


class ChessMasterApi:
    """The object exposed to JavaScript as ``window.pywebview.api``."""

    PHASE = 4

    def __init__(
        self,
        *,
        engine_path: str | None = None,
        worker: StockfishManager | None = None,
        db_path: str | Path | None = None,
        store: GameStore | None = None,
        client_id: str = "local",
        session_id: str | None = None,
        rooms: RoomManager | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._session = GameSession.new_game("pass_play")
        self._analysis_options = dict(_ANALYSIS_DEFAULTS)
        self._polling_started = False
        self._engine = EngineCoordinator(path=engine_path, worker=worker)
        self._engine.bind(self._session)
        # A pooled web session shares the process-wide archive with every other session.
        self._owns_store = store is None
        self._store = store if store is not None else GameStore(db_path)
        self._client_id = str(client_id or "local")
        self._session_id = str(session_id) if session_id else None
        self._rooms = rooms if rooms is not None else RoomManager()
        self._room_id: str | None = None
        self._saved_reviews: set[str] = set()
        self._saved_terminals: set[tuple[str, int, str]] = set()

    @property
    def store(self) -> GameStore:
        return self._store

    @property
    def worker(self) -> StockfishManager:
        return self._engine.worker

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _bind_client(self, client_id: str) -> None:
        """Adopt the caller's browser identity when a pooled session is reused."""

        self._client_id = str(client_id or "local")

    # -- envelope helpers ------------------------------------------------
    @staticmethod
    def _ok(data: Any) -> dict[str, Any]:
        return {"ok": True, "data": data, "error": None}

    @staticmethod
    def _error(exc: ChessMasterError) -> dict[str, Any]:
        return {"ok": False, "data": None, "error": exc.to_dict()}

    def _execute(self, operation: Callable[[], Any]) -> dict[str, Any]:
        try:
            with self._lock:
                room = self._room()
                if room is None:
                    return self._ok(operation())
                # Two tabs share one room session: serialise every room call.
                with self._rooms.lock:
                    return self._ok(operation())
        except ChessMasterError as exc:
            logger.info("API rejected request: %s (%s)", exc.message, exc.code)
            return self._error(exc)
        except Exception:  # pragma: no cover - defensive boundary
            logger.exception("Unexpected API failure")
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": "internal_error",
                    "message": "The chess backend hit an unexpected error.",
                },
            }

    # -- rooms and fair play --------------------------------------------
    def _room(self) -> Room | None:
        return self._rooms.get_room(self._room_id)

    def _require_room(self) -> Room:
        room = self._room()
        if room is None:
            raise InvalidInputError("Create or join an online room first.")
        return room

    def _seat(self, room: Room | None = None) -> str | None:
        room = room if room is not None else self._room()
        return room.seat_for(self._client_id, self._session_id) if room is not None else None

    def _fair_play(self) -> bool:
        room = self._room()
        return bool(room is not None and room.status != "finished")

    def _leave_room(self) -> None:
        self._room_id = None

    # -- engine orchestration -------------------------------------------
    def _sync_engine(self) -> None:
        fair_play = self._fair_play()
        wants_analysis = bool(
            self._polling_started
            and (self._analysis_options["enabled"] or self._analysis_options["arrows"])
            and not fair_play
        )
        self._engine.sync(
            self._session,
            analysis_enabled=wants_analysis,
            arrows=bool(self._analysis_options["arrows"]) and not fair_play,
            depth=int(self._analysis_options["depth"]),
            multipv=int(self._analysis_options["multipv"]),
        )

    def _compose_state(self) -> dict[str, Any]:
        fair_play = self._fair_play()
        state = self._session.to_state()
        state["engine"] = self._engine.engine_payload(self._session)
        state["engine"]["analysis"] = dict(self._analysis_options)
        state["engine"]["analysis"]["enabled"] = bool(
            self._polling_started
            and (self._analysis_options["enabled"] or self._analysis_options["arrows"])
            and not fair_play
        )
        state["evaluation"] = self._engine.evaluation(self._session)
        state["evaluation"]["bars"] = bool(self._analysis_options["enabled"]) and not fair_play
        state["evaluation"]["arrows"] = bool(self._analysis_options["arrows"]) and not fair_play
        if fair_play:
            state["evaluation"]["enabled"] = False
            state["evaluation"]["arrows"] = False
        state["review"] = self._engine.game_review()
        room = self._room()
        state["room"] = (
            room.to_state(self._client_id, self._session_id) if room is not None else None
        )
        seat = room.seat_for(self._client_id, self._session_id) if room is not None else None
        if seat:
            state["orientation"] = seat
        return state

    def _bind_session(self, session: GameSession) -> None:
        self._session = session
        self._engine.bind(session)
        if session.mode == "stockfish" and self._engine.worker.available:
            self._engine.worker.ensure_started()
        self._sync_engine()

    # -- local archive ---------------------------------------------------
    def _archive_current(self, *, force: bool = False) -> dict[str, Any] | None:
        session = self._session
        if not force and session.total_plies < 2:
            return None
        return self._store.upsert_game(
            {**session.to_archive(), "client_id": self._client_id}
        )

    def _archive_before_replacing(self) -> None:
        if self._session.total_plies >= 2 or self._session.is_over:
            self._archive_current(force=True)

    def _archive_terminal(self, state: Mapping[str, Any]) -> None:
        status = state.get("status") or {}
        if status.get("state") in (None, "in_progress"):
            return
        key = (
            str(state.get("game_id")),
            int(state.get("total_plies") or 0),
            str(status.get("state")),
        )
        if key in self._saved_terminals:
            return
        self._archive_current(force=True)
        self._saved_terminals.add(key)

    def _archive_review(self) -> None:
        payload = self._engine.game_review(full=True)
        report = payload.get("report")
        signature = payload.get("signature") or (report or {}).get("signature")
        if not report or not signature or signature in self._saved_reviews:
            return
        record = self._archive_current(force=True)
        if record is None:
            return
        summary = report.get("summary") or {}
        totals = report.get("totals") or {}
        counts = totals.get("counts") or summary.get("counts") or {}
        opening = report.get("opening") or {}
        accuracy = summary.get("accuracy") or {}
        acpl = summary.get("acpl") or {}
        estimated = summary.get("estimated_elo") or {}
        timing = report.get("timing") or {}
        self._store.save_review(
            str(record["game_id"]),
            signature=str(signature),
            preset=report.get("preset"),
            schema_version=report.get("schema_version"),
            report=report,
            completed_at=timing.get("finished_at"),
        )
        self._store.upsert_game(
            {
                **record,
                "opening_eco": opening.get("eco"),
                "opening_name": opening.get("name"),
                "white_accuracy": accuracy.get("white"),
                "black_accuracy": accuracy.get("black"),
                "white_acpl": acpl.get("white"),
                "black_acpl": acpl.get("black"),
                "white_estimated_elo": estimated.get("white"),
                "black_estimated_elo": estimated.get("black"),
                "brilliant_count": int(counts.get("brilliant") or 0),
                "blunder_count": int(counts.get("blunder") or 0),
                "review_preset": report.get("preset"),
                "review_signature": str(signature),
            }
        )
        self._saved_reviews.add(str(signature))

    # -- game lifecycle --------------------------------------------------
    def new_game(self, mode: str = "pass_play", options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Start a fresh game. ``mode`` is ``pass_play`` or ``stockfish``."""

        def operation() -> dict[str, Any]:
            if not isinstance(mode, str):
                raise InvalidInputError("Game mode must be text.")
            session = GameSession.new_game(mode, options)
            self._archive_before_replacing()
            self._leave_room()
            self._bind_session(session)
            return self._compose_state()

        return self._execute(operation)

    def reset_game(self) -> dict[str, Any]:
        """Return the current game to its root position and discard moves."""

        def operation() -> dict[str, Any]:
            if self._room() is not None:
                raise InvalidInputError(
                    "Leave the online room before starting a new game.",
                )
            self._archive_before_replacing()
            self._leave_room()
            self._session.reset()
            self._sync_engine()
            return self._compose_state()

        return self._execute(operation)

    def load_pgn(self, pgn_str: str) -> dict[str, Any]:
        """Import a PGN main line, preserving headers and comments."""

        def operation() -> dict[str, Any]:
            if not isinstance(pgn_str, str):
                raise InvalidInputError("A PGN must be provided as text.")
            session = GameSession.from_pgn(pgn_str)
            self._archive_before_replacing()
            self._leave_room()
            self._bind_session(session)
            self._archive_current(force=True)
            return self._compose_state()

        return self._execute(operation)

    def load_fen(self, fen_str: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Load a legal FEN as a playable position."""

        def operation() -> dict[str, Any]:
            if not isinstance(fen_str, str):
                raise InvalidInputError("A FEN must be provided as text.")
            session = GameSession.from_fen(fen_str, options)
            self._archive_before_replacing()
            self._leave_room()
            self._bind_session(session)
            return self._compose_state()

        return self._execute(operation)

    def start_drill(
        self, drill_id: str, options: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Start a curated drill from its FEN, player color and recommended Elo."""

        def operation() -> dict[str, Any]:
            if not isinstance(drill_id, str) or not drill_id.strip():
                raise InvalidInputError("Choose a drill to practise.")
            drill = get_drill(drill_id.strip())
            if drill is None:
                raise InvalidInputError(
                    "That drill was not found.",
                    details={"drill_id": drill_id},
                )
            if options is not None and not isinstance(options, Mapping):
                raise InvalidInputError("Drill options must be an object.")
            incoming = dict(options or {})
            mode = incoming.pop("mode", "stockfish")
            if mode not in VALID_MODES:
                raise InvalidModeError(
                    "Choose a supported game mode.",
                    details={"mode": mode, "valid_modes": list(VALID_MODES)},
                )
            incoming.setdefault("player_color", drill["player_color"])
            if mode == "stockfish":
                incoming.setdefault("bot_elo", drill["recommended_elo"])
            incoming.setdefault("event_name", f"Drill - {drill['title']}")
            incoming["start_fen"] = drill["fen"]
            session = GameSession.new_game(mode, incoming)
            self._archive_before_replacing()
            self._leave_room()
            self._bind_session(session)
            state = self._compose_state()
            state["drill"] = drill
            return state

        return self._execute(operation)

    # -- online rooms ----------------------------------------------------
    def create_room(self, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Open a shareable online room; the creator hosts the white seat."""

        def operation() -> dict[str, Any]:
            self._archive_before_replacing()
            room = self._rooms.create_room(
                self._client_id, options, session_id=self._session_id
            )
            self._room_id = room.room_id
            with self._rooms.lock:
                self._bind_session(room.session)
                return self._compose_state()

        return self._execute(operation)

    def join_room(self, room_id: str) -> dict[str, Any]:
        """Join a room by code: the second client takes black, others spectate."""

        def operation() -> dict[str, Any]:
            room = self._rooms.join_room(
                room_id, self._client_id, session_id=self._session_id
            )
            if room.room_id != self._room_id:
                self._archive_before_replacing()
            self._room_id = room.room_id
            with self._rooms.lock:
                self._bind_session(room.session)
                return self._compose_state()

        return self._execute(operation)

    def rematch_room(self) -> dict[str, Any]:
        """Start a rematch on the same table, swapping the players' colors."""

        def operation() -> dict[str, Any]:
            room = self._require_room()
            self._archive_current(force=True)
            self._rooms.rematch(room, self._client_id, self._session_id)
            self._bind_session(room.session)
            return self._compose_state()

        return self._execute(operation)

    def resign_game(self) -> dict[str, Any]:
        """Resign for your seat (or the side on move in a local game)."""

        def operation() -> dict[str, Any]:
            room = self._room()
            seat = self._seat(room)
            if room is not None:
                if room.status == "waiting":
                    raise InvalidInputError("The game has not started yet.")
                if seat is None:
                    raise InvalidInputError("Spectators cannot resign.")
            self._session.resign(seat)
            self._sync_engine()
            state = self._compose_state()
            self._archive_terminal(state)
            return state

        return self._execute(operation)

    def offer_or_accept_draw(self, action: str = "offer") -> dict[str, Any]:
        """Offer a draw, or accept/decline the pending offer."""

        def operation() -> dict[str, Any]:
            if not isinstance(action, str) or action.strip().lower() not in (
                "offer",
                "accept",
                "decline",
            ):
                raise InvalidInputError(
                    "Draw action must be offer, accept or decline.",
                    details={"action": action},
                )
            choice = action.strip().lower()
            seat = self._seat()
            if self._room() is not None and seat is None:
                raise InvalidInputError("Spectators cannot offer or answer a draw.")
            if choice == "offer":
                self._session.offer_draw(seat)
            else:
                self._session.respond_draw(choice == "accept", seat)
            self._sync_engine()
            state = self._compose_state()
            self._archive_terminal(state)
            return state

        return self._execute(operation)

    def make_move(self, move: str) -> dict[str, Any]:
        """Play a move in UCI (``e2e4``) or SAN (``Nf3``) notation."""

        def operation() -> dict[str, Any]:
            room = self._room()
            if room is not None:
                if room.status == "waiting":
                    raise InvalidInputError("Wait for an opponent to join the room.")
                seat = self._seat(room)
                if seat is None:
                    raise InvalidInputError("Spectators cannot move pieces.")
                if seat != self._session.turn:
                    raise InvalidInputError("It is not your turn.")
            self._session.make_move(move)
            self._sync_engine()
            if room is not None:
                self._rooms.touch(room)
            state = self._compose_state()
            self._archive_terminal(state)
            return state

        return self._execute(operation)

    def navigate_ply(self, ply_index: int) -> dict[str, Any]:
        """Move the read-only view cursor; never mutates the game line."""

        def operation() -> dict[str, Any]:
            if self._fair_play():
                raise InvalidInputError(
                    "Step through the game after it finishes; the live room is fair play only.",
                )
            self._session.navigate_ply(ply_index)
            self._sync_engine()
            return self._compose_state()

        return self._execute(operation)

    def get_game_state(self) -> dict[str, Any]:
        """Return the full serialized game state."""

        return self._execute(self._compose_state)

    def export_pgn(self) -> dict[str, Any]:
        """Serialize the current main line to PGN."""

        return self._execute(self._session.export_pgn)

    # -- engine surface --------------------------------------------------
    def engine_poll(self) -> dict[str, Any]:
        """Poll for evaluations and a ready bot move.

        This one call powers both PyWebView and the loopback HTTP transport.
        It never waits for a search: it promotes a completed bot move (when its
        token still matches the live revision), queues any missing work, and
        returns the current composed state.
        """

        def operation() -> dict[str, Any]:
            self._polling_started = True
            moved: str | None = None
            if self._session.mode == "stockfish":
                pending = self._engine.take_pending_bot(self._session)
                if pending:
                    try:
                        self._session.make_move(pending)
                        moved = pending
                    except ChessMasterError:
                        logger.info("Discarded a bot move that is no longer legal", exc_info=True)
                self._sync_engine()
            else:
                self._sync_engine()
            state = self._compose_state()
            if state["status"]["state"] == "timeout":
                self._engine.clear_pending_bot()
            self._archive_terminal(state)
            self._archive_review()
            return {
                "state": state,
                "moved": moved,
                "thinking": bool(state["engine"]["thinking"]),
                "evaluation": state["evaluation"],
                "engine": state["engine"],
                "review": state["review"],
            }

        return self._execute(operation)

    def set_analysis_options(self, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Persist eval bar / best-move hint preferences and reschedule."""

        def operation() -> dict[str, Any]:
            if options is not None and not isinstance(options, Mapping):
                raise InvalidInputError("Analysis options must be an object.")
            incoming = options or {}
            if "enabled" in incoming:
                if not isinstance(incoming["enabled"], bool):
                    raise InvalidInputError("Analysis enabled must be true or false.")
                self._analysis_options["enabled"] = incoming["enabled"]
            if "arrows" in incoming:
                if not isinstance(incoming["arrows"], bool):
                    raise InvalidInputError("Arrow enabled must be true or false.")
                self._analysis_options["arrows"] = incoming["arrows"]
            if "depth" in incoming:
                try:
                    depth = int(incoming["depth"])
                except (TypeError, ValueError) as exc:
                    raise InvalidInputError("Analysis depth must be a number from 8 to 30.") from exc
                if depth < 8 or depth > 30:
                    raise InvalidInputError(
                        "Analysis depth must be from 8 to 30.",
                        details={"depth": depth},
                    )
                self._analysis_options["depth"] = depth
            if "multipv" in incoming:
                try:
                    multipv = int(incoming["multipv"])
                except (TypeError, ValueError) as exc:
                    raise InvalidInputError("MultiPV must be from 1 to 5.") from exc
                if multipv < 1 or multipv > 5:
                    raise InvalidInputError("MultiPV must be from 1 to 5.", details={"multipv": multipv})
                self._analysis_options["multipv"] = multipv

            self._polling_started = True
            self._sync_engine()
            state = self._compose_state()
            return {
                "options": dict(self._analysis_options),
                "evaluation": state["evaluation"],
                "engine": state["engine"],
            }

        return self._execute(operation)

    def configure_engine_path(self, path: str | None = None) -> dict[str, Any]:
        """Use a custom Stockfish binary (``None`` or ``""`` auto-detects)."""

        def operation() -> dict[str, Any]:
            if path is not None and not isinstance(path, str):
                raise InvalidInputError("The engine path must be text.")
            text = (path or "").strip().strip('"')
            if text and not Path(text).expanduser().exists():
                raise InvalidInputError(
                    f"No file or folder exists at {text}.",
                    details={"path": text},
                )
            self._engine.configure_path(text or None)
            self._sync_engine()
            state = self._compose_state()
            return {
                "engine": state["engine"],
                "evaluation": state["evaluation"],
                "options": dict(self._analysis_options),
            }

        return self._execute(operation)

    def get_engine_status(self) -> dict[str, Any]:
        """Diagnostics for the sidebar/Engine setup and QA automation."""

        def operation() -> dict[str, Any]:
            state = self._compose_state()
            return {
                "engine": state["engine"],
                "evaluation": state["evaluation"],
                "cache": self._engine.cache_stats(),
                "analysis": dict(self._analysis_options),
                "model": model_info(),
            }

        return self._execute(operation)

    # -- game review -----------------------------------------------------
    def start_game_review(
        self,
        preset: str = "standard",
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a two-pass Stockfish review of the current game line.

        ``preset`` is ``fast`` (depth 10), ``standard`` (14) or ``deep`` (16).
        Pass 2 confirms Brilliant/Great candidates at ``depth + 2`` (cap 18).
        """

        def operation() -> dict[str, Any]:
            if self._fair_play():
                raise InvalidInputError(
                    "Game Review unlocks when the online game is over.",
                )
            if not isinstance(preset, str) or preset.strip().lower() not in REVIEW_PRESETS:
                raise InvalidInputError(
                    "Choose a review preset: fast, standard or deep.",
                    details={"preset": preset, "valid": list(REVIEW_PRESETS)},
                )
            if not isinstance(options, Mapping) and options is not None:
                raise InvalidInputError("Review options must be an object.")
            incoming = options or {}
            depth = incoming.get("depth")
            if depth is not None:
                if isinstance(depth, bool):
                    raise InvalidInputError("Review depth must be a whole number.")
                try:
                    depth = int(depth)
                except (TypeError, ValueError) as exc:
                    raise InvalidInputError("Review depth must be a whole number.") from exc
                if depth < 6 or depth > CONFIRM_DEPTH_CAP:
                    raise InvalidInputError(
                        f"Review depth must be from 6 to {CONFIRM_DEPTH_CAP}."
                    )
            force = bool(incoming.get("force", False))
            use_book = incoming.get("use_book", True)
            if not isinstance(use_book, bool):
                raise InvalidInputError("Review use_book must be true or false.")
            if self._session.total_plies <= 0:
                raise InvalidInputError(
                    "Play or import at least one move before running a review."
                )
            review = self._engine.start_game_review(
                self._session,
                preset=preset.strip().lower(),
                depth=depth,
                force=force,
                use_book=use_book,
            )
            return {"review": review, "evaluation": self._engine.evaluation(self._session)}

        return self._execute(operation)

    def cancel_game_review(self) -> dict[str, Any]:
        """Stop the running review; the game and live analysis are untouched."""

        def operation() -> dict[str, Any]:
            review = self._engine.cancel_game_review(self._session, reason="user")
            self._sync_engine()
            return {"review": review}

        return self._execute(operation)

    def get_game_review(self, full: bool = True) -> dict[str, Any]:
        """Return the compact review block and, when complete, the full report."""

        def operation() -> dict[str, Any]:
            payload = self._engine.game_review(full=True)
            self._archive_review()
            report = payload.get("report") if full else None
            compact = {
                key: value
                for key, value in payload.items()
                if key not in {"report", "moves"}
            }
            return {"review": compact, "report": report}

        return self._execute(operation)

    # -- archive ---------------------------------------------------------
    def save_current_game(self) -> dict[str, Any]:
        """Write the live session to SQLite, even mid-game."""

        def operation() -> dict[str, Any]:
            record = self._archive_current(force=True)
            return {
                "saved": record is not None,
                "game": record,
                "db_info": self._store.db_info(),
            }

        return self._execute(operation)

    def list_archived_games(
        self,
        limit: int = 50,
        offset: int = 0,
        mode: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Return archived games, aggregate stats and database details."""

        def operation() -> dict[str, Any]:
            if isinstance(limit, bool) or isinstance(offset, bool):
                raise InvalidInputError("Archive paging values must be whole numbers.")
            try:
                clean_limit = int(limit)
                clean_offset = int(offset)
            except (TypeError, ValueError) as exc:
                raise InvalidInputError(
                    "Archive paging values must be whole numbers."
                ) from exc
            if mode is not None and not isinstance(mode, str):
                raise InvalidInputError("Archive mode filter must be text.")
            if query is not None and not isinstance(query, str):
                raise InvalidInputError("Archive search must be text.")
            games, total = self._store.list_games(
                limit=clean_limit,
                offset=clean_offset,
                mode=mode or None,
                query=query or None,
                client_id=self._client_id,
            )
            return {
                "games": games,
                "total": total,
                "stats": self._store.stats(client_id=self._client_id),
                "trend": self._store.history_trend(client_id=self._client_id),
                "db_info": self._store.db_info(),
            }

        return self._execute(operation)

    def load_archived_game(self, game_id: str) -> dict[str, Any]:
        """Restore a saved game and, when present, its stored review report."""

        def operation() -> dict[str, Any]:
            if not isinstance(game_id, str) or not game_id.strip():
                raise InvalidInputError("Choose an archived game to load.")
            record = self._store.get_game(game_id.strip())
            if record is None:
                raise InvalidInputError(
                    "That archived game was not found.",
                    details={"game_id": game_id},
                )
            saved_review = self._store.get_review(game_id.strip())
            session = GameSession.from_archive(record, saved_review)
            self._archive_before_replacing()
            self._bind_session(session)
            restored = False
            if saved_review and isinstance(saved_review.get("report"), Mapping):
                self._engine.restore_review(
                    saved_review["report"],
                    signature=saved_review.get("signature"),
                    preset=saved_review.get("preset"),
                )
                restored = True
            state = self._compose_state()
            state["restored_review"] = restored
            return state

        return self._execute(operation)

    def delete_archived_game(self, game_id: str) -> dict[str, Any]:
        """Delete one archived game; its review row cascades with it."""

        def operation() -> dict[str, Any]:
            if not isinstance(game_id, str) or not game_id.strip():
                raise InvalidInputError("Choose an archived game to delete.")
            deleted = self._store.delete_game(game_id.strip())
            return {
                "deleted": deleted,
                "game_id": game_id.strip(),
                "db_info": self._store.db_info(),
            }

        return self._execute(operation)

    def clear_game_archive(self) -> dict[str, Any]:
        """Delete every archived game and review, then vacuum the database."""

        def operation() -> dict[str, Any]:
            deleted = self._store.clear(client_id=self._client_id)
            self._saved_reviews.clear()
            self._saved_terminals.clear()
            # Keep the live session from being re-archived by the next engine poll.
            payload = self._engine.game_review(full=True)
            signature = payload.get("signature") or (payload.get("report") or {}).get("signature")
            if signature:
                self._saved_reviews.add(str(signature))
            state = self._session.to_state()
            status = state.get("status") or {}
            if status.get("state") not in (None, "in_progress"):
                self._saved_terminals.add(
                    (
                        str(state.get("game_id")),
                        int(state.get("total_plies") or 0),
                        str(status.get("state")),
                    )
                )
            return {
                "deleted": deleted,
                "stats": self._store.stats(client_id=self._client_id),
                "db_info": self._store.db_info(),
            }

        return self._execute(operation)

    def export_archive_pgn(self, game_ids: Sequence[str] | None = None) -> dict[str, Any]:
        """Concatenate saved PGNs, for every game or a selected subset."""

        def operation() -> dict[str, Any]:
            if game_ids is not None:
                if isinstance(game_ids, (str, bytes)) or not isinstance(game_ids, Sequence):
                    raise InvalidInputError("Archive export expects a list of game ids.")
                rows = [self._store.get_game(str(game_id)) for game_id in game_ids]
            else:
                rows, _ = self._store.list_games(
                    limit=500, offset=0, client_id=self._client_id
                )
            blocks = [str(row["pgn"]).strip() for row in rows if row and row.get("pgn")]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            return {
                "pgn": "\n\n".join(blocks),
                "count": len(blocks),
                "filename": f"chess-master-archive-{stamp}.pgn",
            }

        return self._execute(operation)

    # -- metadata --------------------------------------------------------
    def get_api_info(self) -> dict[str, Any]:
        """Describe the bridge, its phase and the approved analysis model."""

        return self._execute(
            lambda: {
                "name": "Chess Master",
                "version": __version__,
                "phase": self.PHASE,
                "feature_phase": 6,
                "schema_version": 4,
                "modes": list(VALID_MODES),
                "methods": [
                    "new_game",
                    "reset_game",
                    "load_pgn",
                    "load_fen",
                    "make_move",
                    "navigate_ply",
                    "get_game_state",
                    "export_pgn",
                    "get_api_info",
                    "get_samples",
                    "get_drills",
                    "start_drill",
                    "engine_poll",
                    "set_analysis_options",
                    "configure_engine_path",
                    "get_engine_status",
                    "start_game_review",
                    "cancel_game_review",
                    "get_game_review",
                    "save_current_game",
                    "list_archived_games",
                    "load_archived_game",
                    "delete_archived_game",
                    "clear_game_archive",
                    "export_archive_pgn",
                    "get_opening_explorer",
                    "search_openings",
                    "create_room",
                    "join_room",
                    "rematch_room",
                    "resign_game",
                    "offer_or_accept_draw",
                ],
                "capabilities": {
                    "pass_and_play": True,
                    "stockfish": True,
                    "pgn": True,
                    "fen": True,
                    "drawable_arrows": True,
                    "engine": True,
                    "live_evaluation": True,
                    "best_move_arrows": True,
                    "multi_pv": True,
                    "engine_path_config": True,
                    "game_review": True,
                    "review_presets": list(REVIEW_PRESETS),
                    "clocks": True,
                    "time_controls": list(TIME_CONTROL_PRESETS),
                    "sound": True,
                    "archive": True,
                    "retry_mistake": True,
                    "history_dashboard": True,
                    "opening_explorer": True,
                    "board_themes": True,
                    "review_export": True,
                    "board_editor": True,
                    "drills": True,
                    "online_rooms": True,
                    "resign": True,
                    "draw_offers": True,
                    "rematch": True,
                },
                "archive": {"db_info": self._store.db_info()},
                "analysis": model_info(),
                "engine": self._engine.engine_payload(self._session),
                "analysis_options": dict(self._analysis_options),
                "elo": {
                    "min": MIN_BOT_ELO,
                    "max": MAX_BOT_ELO,
                    "default": DEFAULT_BOT_ELO,
                    "presets": list(ELO_PRESETS),
                    "limit_strength_min": 1320,
                },
            }
        )

    def get_samples(self) -> dict[str, Any]:
        """Famous games/positions for the import drawer."""

        return self._execute(lambda: {"samples": samples_payload()})

    def get_drills(self) -> dict[str, Any]:
        """Curated endgame/tactical practice positions."""

        def operation() -> dict[str, Any]:
            drills = drills_payload()
            return {"drills": drills, "count": len(drills)}

        return self._execute(operation)

    # -- opening explorer -------------------------------------------------
    def get_opening_explorer(self) -> dict[str, Any]:
        """Book continuations for the position the table is viewing."""

        def operation() -> dict[str, Any]:
            session = self._session
            moves = session.move_ucis(session.view_ply)
            return get_default_book().explore(moves, session.board.fen())

        return self._execute(operation)

    def search_openings(
        self, query: str = "", eco_prefix: str = "", limit: int = 40
    ) -> dict[str, Any]:
        """Search the bundled ECO dictionary by name fragment or ECO code."""

        def operation() -> dict[str, Any]:
            if query is not None and not isinstance(query, str):
                raise InvalidInputError("Opening search text must be text.")
            if eco_prefix is not None and not isinstance(eco_prefix, str):
                raise InvalidInputError("ECO code must be text.")
            if isinstance(limit, bool):
                raise InvalidInputError("Opening search limit must be a whole number.")
            try:
                clean_limit = int(limit)
            except (TypeError, ValueError) as exc:
                raise InvalidInputError("Opening search limit must be a whole number.") from exc
            results = get_default_book().search_openings(
                query=query or "",
                eco_prefix=eco_prefix or "",
                limit=max(1, min(clean_limit, 200)),
            )
            return {"openings": results, "count": len(results)}

        return self._execute(operation)

    def _shutdown(self) -> None:
        """Stop the engine worker.  Private so it is not exposed over HTTP."""

        self._engine.shutdown()
        if self._owns_store:
            self._store.close()

    def _detach(self) -> None:
        """Release a pooled web session; the shared worker and archive stay alive."""

        self._engine.detach()
