"""Binds the single Stockfish worker to a :class:`GameSession`.

The coordinator owns the *policy*: when a bot move should be requested, when a
live evaluation is useful, which revision a result belongs to, and what the
frontend receives.  The worker only owns the engine process.

Nothing in this module writes to a session.  The bridge calls
:meth:`take_pending_bot` on the API thread and commits the move while holding
the API lock, after checking the token one final time.
"""

from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path
from typing import Any, TYPE_CHECKING

import chess

from chess_master.analysis.winprob import WINPROB_VERSION
from chess_master.engine.stockfish import (
    DEFAULT_DEPTH,
    DEFAULT_MULTIPV,
    EngineToken,
    StockfishManager,
    elo_profile,
    terminal_evaluation,
)
from chess_master.engine.status import position_status

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chess_master.engine.game import GameSession

__all__ = ["EngineCoordinator"]

logger = logging.getLogger(__name__)


class EngineCoordinator:
    """Per-table engine policy and stale-result guard."""

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        worker: StockfishManager | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._worker = worker or StockfishManager(path=path, project_root=project_root)
        self._worker.set_callbacks(
            on_analysis=self._on_analysis,
            on_bot_move=self._on_bot_move,
            on_error=self._on_error,
        )
        self._token: EngineToken | None = None
        self._analysis: dict[str, Any] | None = None
        self._pending_bot: dict[str, Any] | None = None
        self._bot_scheduled: EngineToken | None = None
        self._analysis_scheduled: EngineToken | None = None
        self._thinking = False
        self._last_error: str | None = None
        self._options = {
            "enabled": True,
            "arrows": False,
            "depth": DEFAULT_DEPTH,
            "multipv": DEFAULT_MULTIPV,
        }

    # -- public state ----------------------------------------------------
    @property
    def worker(self) -> StockfishManager:
        return self._worker

    @property
    def options(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._options)

    def set_options(self, **options: Any) -> dict[str, Any]:
        with self._lock:
            for key in ("enabled", "arrows", "depth", "multipv"):
                if key in options and options[key] is not None:
                    self._options[key] = options[key]
            self._analysis = None
            self._analysis_scheduled = None
            return dict(self._options)

    @staticmethod
    def token_for(session: "GameSession") -> EngineToken:
        return EngineToken(
            game_id=session.game_id,
            revision=session.revision,
            ply=session.view_ply,
            fen=session.board.fen(),
        )

    def bind(self, session: "GameSession") -> EngineToken:
        token = self.token_for(session)
        with self._lock:
            self._reset_for(token)
        self._worker.set_active_token(token)
        return token

    def sync(
        self,
        session: "GameSession",
        *,
        analysis_enabled: bool | None = None,
        arrows: bool | None = None,
        depth: int | None = None,
        multipv: int | None = None,
    ) -> None:
        """Re-point the engine at the session's current revision and schedule."""

        token = self.token_for(session)
        with self._lock:
            old_depth = int(self._options["depth"])
            old_multipv = int(self._options["multipv"])
            old_enabled = bool(self._options["enabled"] or self._options["arrows"])
            if analysis_enabled is not None:
                self._options["enabled"] = bool(analysis_enabled)
            if arrows is not None:
                self._options["arrows"] = bool(arrows)
            if depth is not None:
                self._options["depth"] = max(8, min(30, int(depth)))
            if multipv is not None:
                self._options["multipv"] = max(1, min(5, int(multipv)))
            new_enabled = bool(self._options["enabled"] or self._options["arrows"])
            if (
                not new_enabled
                or old_depth != int(self._options["depth"])
                or old_multipv != int(self._options["multipv"])
                or old_enabled != new_enabled
            ):
                self._analysis = None
                self._analysis_scheduled = None
            if token != self._token:
                self._reset_for(token)
        self._worker.set_active_token(token)
        self.ensure_scheduled(session)

    def ensure_scheduled(self, session: "GameSession") -> None:
        """Queue missing jobs.  Idempotent; safe to call on every poll."""

        token = self.token_for(session)
        with self._lock:
            if token != self._token:
                self._reset_for(token)
                reset = True
            else:
                reset = False
            options = dict(self._options)
            enabled = bool(options["enabled"] or options["arrows"])
            depth = int(options["depth"])
            multipv = int(options["multipv"])
        if reset:
            self._worker.set_active_token(token)

        if not self._worker.available:
            return

        terminal = self._terminal_payload(session, token)
        if terminal is not None:
            if enabled:
                with self._lock:
                    if self._token == token:
                        self._analysis = terminal
                        self._analysis_scheduled = token
            return

        # A bot only moves at the live tip.  Navigating into history cancels
        # any in-flight search through the changed token.
        if self._is_bot_turn(session):
            with self._lock:
                should_submit = (
                    self._pending_bot is None
                    and self._bot_scheduled != token
                    and self._token == token
                )
            if should_submit:
                status = self._worker.submit_bot(
                    token,
                    start_fen=session.start_fen,
                    moves=session.move_ucis(session.view_ply),
                    elo=session.engine_bot_elo or 1400,
                )
                if status in {"queued", "cache"}:
                    with self._lock:
                        if self._token == token:
                            self._bot_scheduled = token
                            self._thinking = True

        if enabled:
            with self._lock:
                need_analysis = (
                    self._analysis_token(token) is False
                    and self._analysis_scheduled != token
                    and self._token == token
                )
            if need_analysis:
                status = self._worker.submit_analysis(
                    token,
                    start_fen=session.start_fen,
                    moves=session.move_ucis(session.view_ply),
                    depth=depth,
                    multipv=multipv,
                    use_cache=True,
                )
                if status in {"queued", "cache"}:
                    with self._lock:
                        if self._token == token:
                            self._analysis_scheduled = token

    # -- bot commit ------------------------------------------------------
    def take_pending_bot(self, session: "GameSession") -> str | None:
        """Consume a ready bot move if it still matches the live revision."""

        token = self.token_for(session)
        with self._lock:
            pending = self._pending_bot
            if pending is None:
                return None
            if pending["token"] != token:
                self._pending_bot = None
                return None
            self._pending_bot = None
            self._thinking = False
            self._bot_scheduled = None
            if pending["uci"] == "0000":
                return None
            if not self._is_bot_turn(session):
                return None
            return str(pending["uci"])

    # -- payloads --------------------------------------------------------
    def engine_payload(self, session: "GameSession") -> dict[str, Any]:
        status = self._worker.status_payload()
        with self._lock:
            options = dict(self._options)
            thinking = bool(self._thinking and self._is_bot_turn(session))
            last_error = self._last_error

        enabled = session.mode == "stockfish"
        bot_elo = session.engine_bot_elo
        bot_profile = elo_profile(bot_elo).to_dict() if enabled and bot_elo else None
        available = bool(status["available"])
        status_name = status["status"]
        if not available:
            note = (
                status.get("message")
                or "Stockfish was not found. Add a binary to bin/, set STOCKFISH_PATH, "
                "or choose a custom path."
            )
        elif status_name == "error":
            note = status.get("message") or last_error or "Stockfish stopped unexpectedly."
        elif status_name == "starting":
            note = "Starting the Stockfish worker."
        else:
            note = "One engine process, one owner thread."

        return {
            **status,
            "enabled": enabled,
            "available": available,
            "thinking": thinking,
            "analysis": options,
            "player_color": session.engine_player_color,
            "bot_color": session.engine_bot_color,
            "bot_elo": bot_elo,
            "bot": bot_profile,
            "orientation": session.engine_player_color or "white",
            "configurable": True,
            "note": note,
        }

    def evaluation(self, session: "GameSession") -> dict[str, Any]:
        token = self.token_for(session)
        with self._lock:
            options = dict(self._options)
            enabled = bool(options["enabled"] or options["arrows"])
            analysis = copy.deepcopy(self._analysis) if self._analysis is not None else None
            analysis_token = analysis.get("token") if analysis else None
            scheduled = self._analysis_scheduled == token
        terminal = self._terminal_payload(session, token)
        if terminal is not None:
            terminal.update(
                {
                    "enabled": bool(options["enabled"]),
                    "arrows": bool(options["arrows"]),
                    "available": True,
                    "thinking": False,
                }
            )
            return terminal

        if analysis is not None and analysis_token == token.to_dict():
            analysis.update(
                {
                    "enabled": bool(options["enabled"]),
                    "arrows": bool(options["arrows"]),
                    "available": bool(self._worker.available),
                    "thinking": False,
                }
            )
            return analysis

        worker_jobs = self._worker.status_payload().get("jobs", {})
        return {
            "available": bool(self._worker.available),
            "enabled": bool(options["enabled"]),
            "arrows": bool(options["arrows"]),
            "thinking": bool(enabled and (scheduled or worker_jobs.get("running") == "analysis")),
            "depth": 0,
            "multipv": int(options["multipv"]),
            "score_cp": None,
            "mate": None,
            "score_text": None,
            "win_prob_white": 50.0,
            "best_move": None,
            "pv": [],
            "pv_san": [],
            "lines": [],
            "cached": False,
            "updated_at": None,
            "model": "lichess-logistic",
            "model_version": WINPROB_VERSION,
            "token": token.to_dict(),
        }

    def cache_stats(self) -> dict[str, Any]:
        return self._worker.cache.stats()

    # -- configuration ---------------------------------------------------
    def configure_path(self, path: str | Path | None) -> dict[str, Any]:
        with self._lock:
            self._analysis = None
            self._pending_bot = None
            self._bot_scheduled = None
            self._analysis_scheduled = None
            self._thinking = False
            self._last_error = None
        status = self._worker.configure_path(path)
        token = self._token
        if token is not None:
            self._worker.set_active_token(token)
        return status

    def shutdown(self) -> None:
        with self._lock:
            self._analysis = None
            self._pending_bot = None
        self._worker.stop()

    # -- worker callbacks ------------------------------------------------
    def _on_analysis(self, token: EngineToken, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._token == token:
                self._analysis = copy.deepcopy(payload)
                self._analysis_scheduled = token
            else:
                logger.debug("Discarded stale analysis for revision %s", token.revision)

    def _on_bot_move(self, token: EngineToken, uci: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._token == token and self._bot_scheduled == token:
                self._pending_bot = {
                    "token": token,
                    "uci": uci,
                    "analysis": copy.deepcopy(payload),
                }
                self._thinking = False
            else:
                logger.debug("Discarded stale bot move for revision %s", token.revision)

    def _on_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._thinking = False

    # -- internals -------------------------------------------------------
    def _reset_for(self, token: EngineToken) -> None:
        if token == self._token:
            return
        self._token = token
        self._analysis = None
        self._pending_bot = None
        self._bot_scheduled = None
        self._analysis_scheduled = None
        self._thinking = False

    def _analysis_token(self, token: EngineToken) -> bool:
        if self._analysis is None:
            return False
        return self._analysis.get("token") == token.to_dict()

    def _is_bot_turn(self, session: "GameSession") -> bool:
        if session.mode != "stockfish" or not session.is_viewing_live:
            return False
        bot_color = session.engine_bot_color
        if not bot_color:
            return False
        if position_status(session.board)["state"] != "in_progress":
            return False
        turn = "white" if session.board.turn == chess.WHITE else "black"
        return turn == bot_color

    def _terminal_payload(self, session: "GameSession", token: EngineToken) -> dict[str, Any] | None:
        board = session.board
        if not (board.is_checkmate() or board.is_stalemate() or board.is_insufficient_material() or board.is_seventyfive_moves() or board.is_fivefold_repetition()):
            return None
        return terminal_evaluation(
            board,
            token=token,
            engine_name=self._worker.name,
            engine_version=self._worker.version,
        )
