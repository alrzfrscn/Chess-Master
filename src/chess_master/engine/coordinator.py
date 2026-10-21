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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import chess

from chess_master.analysis.review import (
    REVIEW_PRESETS,
    ReviewRequest,
    line_signature,
)
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
            on_review_progress=self._on_review_progress,
            on_review_complete=self._on_review_complete,
            on_error=self._on_error,
        )
        self._token: EngineToken | None = None
        self._analysis: dict[str, Any] | None = None
        self._pending_bot: dict[str, Any] | None = None
        self._bot_scheduled: EngineToken | None = None
        self._analysis_scheduled: EngineToken | None = None
        self._thinking = False
        self._last_error: str | None = None
        self._review: dict[str, Any] | None = None
        self._review_report: dict[str, Any] | None = None
        self._review_signature: str | None = None
        self._review_run_id: int | None = None
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
            self._reset_review()
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
        self._invalidate_review_if_needed(session)
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
        if self._review_is_running():
            if self._is_bot_turn(session):
                # The bot must keep playing; a review is a background task.
                self.cancel_game_review(session, reason="bot_turn")
            else:
                # Live analysis would queue behind a multi-minute review.
                return
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

    # -- game review -----------------------------------------------------
    def start_game_review(
        self,
        session: "GameSession",
        *,
        preset: str = "standard",
        depth: int | None = None,
        force: bool = False,
        use_book: bool = True,
    ) -> dict[str, Any]:
        """Start (or resume) a two-pass review for the current game line."""

        signature = line_signature(session.game_id, session.start_fen, session.move_ucis())
        requested_depth = int(depth) if depth is not None else int(REVIEW_PRESETS[preset]["depth"])
        with self._lock:
            if (
                not force
                and self._review is not None
                and self._review_signature == signature
                and self._review.get("preset") == preset
                and int(self._review.get("depth") or 0) == requested_depth
                and self._review.get("status") in {"queued", "running", "complete"}
            ):
                return self.review_payload(full=False)

        state = session.to_state()
        players = state.get("players") or {}
        white_name = str((players.get("white") or {}).get("name") or "White")
        black_name = str((players.get("black") or {}).get("name") or "Black")
        result = str((state.get("status") or {}).get("result") or "*")
        baseline_white = None
        baseline_black = None
        bot_elo = session.engine_bot_elo
        if session.mode == "stockfish" and bot_elo:
            if session.engine_bot_color == "white":
                baseline_white = int(bot_elo)
            elif session.engine_bot_color == "black":
                baseline_black = int(bot_elo)

        request = ReviewRequest(
            game_id=session.game_id,
            start_fen=session.start_fen,
            moves=tuple(session.move_ucis()),
            preset=preset,
            depth=depth,
            white_name=white_name,
            black_name=black_name,
            result=result,
            baseline_white=baseline_white,
            baseline_black=baseline_black,
            use_book=bool(use_book),
        )

        self._worker.cancel_review()
        with self._lock:
            self._reset_review()
            self._review_signature = signature
            self._review = {
                "available": True,
                "status": "queued",
                "signature": signature,
                "game_id": session.game_id,
                "preset": preset,
                "preset_label": REVIEW_PRESETS[preset]["label"],
                "depth": request.base_depth,
                "confirm_depth": request.confirm_depth,
                "started_at": _utc_now(),
                "finished_at": None,
                "progress": {
                    "done": 0,
                    "total": len(request.moves) + 1,
                    "phase": "queued",
                    "percent": 0.0,
                },
                "summary": None,
                "opening": None,
                "error": None,
                "move_count": len(request.moves),
            }

        status = self._worker.submit_review(request)
        run_id = self._worker.review_run_id
        with self._lock:
            if self._review is None:
                return self.review_payload()
            self._review_run_id = run_id
            self._review["run_id"] = run_id
            if status == "unavailable":
                self._review.update(
                    {
                        "available": False,
                        "status": "failed",
                        "finished_at": _utc_now(),
                        "error": "Stockfish is not available.",
                        "progress": {
                            "done": 0,
                            "total": self._review["progress"]["total"],
                            "phase": "failed",
                            "percent": 0.0,
                        },
                    }
                )
        return self.review_payload(full=False)

    def cancel_game_review(
        self, session: "GameSession | None" = None, *, reason: str = "cancelled"
    ) -> dict[str, Any]:
        """Cancel the active review.  Navigation alone never calls this."""

        if session is not None:
            signature = line_signature(session.game_id, session.start_fen, session.move_ucis())
        else:
            signature = self._review_signature
        self._worker.cancel_review()
        with self._lock:
            if (
                self._review is not None
                and self._review_signature == signature
                and self._review.get("status") in {"queued", "running"}
            ):
                self._review.update(
                    {
                        "status": "cancelled",
                        "finished_at": _utc_now(),
                        "error": None,
                        "progress": {
                            **self._review.get("progress", {}),
                            "phase": "cancelled",
                        },
                    }
                )
                self._review["progress"]["percent"] = float(
                    self._review["progress"].get("percent") or 0.0
                )
            return self.review_payload(full=False, reason=reason)

    def review_payload(self, *, full: bool = False, reason: str | None = None) -> dict[str, Any]:
        """Compact review block for state/poll, or the full report on demand."""

        with self._lock:
            snapshot = copy.deepcopy(self._review)
            report = copy.deepcopy(self._review_report) if full else None
            signature = self._review_signature
            run_id = self._review_run_id
        if snapshot is None:
            return {
                "available": False,
                "status": "idle",
                "signature": signature,
                "run_id": run_id,
                "progress": {"done": 0, "total": 0, "phase": "idle", "percent": 0.0},
                "summary": None,
                "opening": None,
                "error": None,
                "reason": reason,
            }
        snapshot["run_id"] = run_id
        if reason:
            snapshot["reason"] = reason
        if report is not None:
            snapshot["report_available"] = True
            if full:
                snapshot["report"] = report
                snapshot["moves"] = report.get("moves", [])
                snapshot["summary"] = report.get("summary")
                snapshot["opening"] = report.get("opening")
                snapshot["engine"] = report.get("engine")
                snapshot["model"] = report.get("model")
                snapshot["timing"] = report.get("timing")
                snapshot["progress"] = report.get("progress")
        elif full:
            snapshot["report_available"] = False
        return snapshot

    def game_review(self, *, full: bool = False) -> dict[str, Any]:
        return self.review_payload(full=full)

    def _review_is_running(self) -> bool:
        with self._lock:
            return bool(
                self._review is not None
                and self._review.get("status") in {"queued", "running"}
            )

    def _review_matches(self, payload: Mapping[str, Any]) -> bool:
        signature = payload.get("signature")
        run_id = payload.get("run_id")
        if signature != self._review_signature:
            return False
        if self._review_run_id is not None and run_id is not None:
            return int(run_id) == int(self._review_run_id)
        return True

    def _invalidate_review_if_needed(self, session: "GameSession") -> None:
        if self._review is None:
            return
        signature = line_signature(session.game_id, session.start_fen, session.move_ucis())
        if signature == self._review_signature:
            return
        self._worker.cancel_review()
        with self._lock:
            if signature != self._review_signature:
                self._reset_review()

    def _reset_review(self) -> None:
        self._review = None
        self._review_report = None
        self._review_signature = None
        self._review_run_id = None

    def _apply_review_progress(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            if self._review is None or not self._review_matches(payload):
                return
            if self._review_run_id is None and payload.get("run_id") is not None:
                self._review_run_id = int(payload["run_id"])
            self._review["status"] = "running"
            self._review["run_id"] = payload.get("run_id")
            self._review["progress"] = {
                "done": int(payload.get("done") or 0),
                "total": int(payload.get("total") or 0),
                "phase": str(payload.get("phase") or "scan"),
                "percent": float(payload.get("percent") or 0.0),
            }

    def _apply_review_complete(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            if self._review is None or not self._review_matches(payload):
                return
            status = str(payload.get("status") or "failed")
            if status == "complete":
                self._review_report = copy.deepcopy(dict(payload))
                summary = payload.get("summary") or {}
                self._review.update(
                    {
                        "available": True,
                        "status": "complete",
                        "finished_at": (payload.get("timing") or {}).get("finished_at")
                        or _utc_now(),
                        "summary": {
                            "accuracy": summary.get("accuracy"),
                            "acpl": summary.get("acpl"),
                            "acpl_trimmed": summary.get("acpl_trimmed"),
                            "estimated_elo": summary.get("estimated_elo"),
                            "counts": summary.get("counts"),
                        },
                        "opening": payload.get("opening"),
                        "engine": payload.get("engine"),
                        "timing": payload.get("timing"),
                        "progress": payload.get("progress")
                        or {
                            "done": int(payload.get("progress", {}).get("done") or 0),
                            "total": int(payload.get("progress", {}).get("total") or 0),
                            "phase": "complete",
                            "percent": 100.0,
                        },
                        "error": None,
                    }
                )
            elif status == "cancelled":
                self._review.update(
                    {
                        "status": "cancelled",
                        "finished_at": _utc_now(),
                        "progress": {
                            **self._review.get("progress", {}),
                            "phase": "cancelled",
                        },
                    }
                )
            else:
                self._review.update(
                    {
                        "status": "failed",
                        "available": bool(payload.get("available", True)),
                        "finished_at": _utc_now(),
                        "error": str(payload.get("error") or "The review failed."),
                        "progress": {
                            **self._review.get("progress", {}),
                            "phase": "failed",
                        },
                    }
                )

    # -- configuration ---------------------------------------------------
    def configure_path(self, path: str | Path | None) -> dict[str, Any]:
        with self._lock:
            self._reset_review()
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
            self._review = None
            self._review_report = None
        self._worker.cancel_review()
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
            if self._review is not None and self._review.get("status") in {"queued", "running"}:
                self._review.update(
                    {
                        "status": "failed",
                        "finished_at": _utc_now(),
                        "error": message,
                        "progress": {
                            **self._review.get("progress", {}),
                            "phase": "failed",
                        },
                    }
                )

    def _on_review_progress(self, payload: dict[str, Any]) -> None:
        self._apply_review_progress(payload)

    def _on_review_complete(self, payload: dict[str, Any]) -> None:
        self._apply_review_complete(payload)

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
