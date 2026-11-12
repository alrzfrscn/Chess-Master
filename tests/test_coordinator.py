"""Coordinator policy: engine errors, pacing and review-aware scheduling."""

from __future__ import annotations

import time

from chess_master.analysis.review import line_signature
from chess_master.engine.coordinator import EngineCoordinator, bot_move_delay
from chess_master.engine.game import GameSession


class FakeWorker:
    """The small slice of the worker the coordinator touches in these tests."""

    def __init__(self) -> None:
        self.callbacks: dict = {}
        self.cancelled_reviews = 0
        self.active_tokens: list = []
        self.available = True

    def set_callbacks(self, **callbacks) -> None:
        self.callbacks = callbacks

    def set_active_token(self, token) -> None:
        self.active_tokens.append(token)

    def cancel_review(self) -> bool:
        self.cancelled_reviews += 1
        return True

    def status_payload(self) -> dict:
        return {
            "available": True,
            "status": "ready",
            "name": "Stockfish",
            "version": "test",
            "jobs": {},
        }


def _running_review(session: GameSession) -> dict:
    return {
        "status": "running",
        "signature": line_signature(session.game_id, session.start_fen, session.move_ucis()),
        "progress": {"done": 1, "total": 2, "phase": "scan", "percent": 50.0},
    }


def _coordinator_with_review(session: GameSession) -> tuple[EngineCoordinator, FakeWorker]:
    worker = FakeWorker()
    coordinator = EngineCoordinator(worker=worker)
    review = _running_review(session)
    coordinator._review = review
    coordinator._review_signature = review["signature"]
    return coordinator, worker


def test_on_error_fails_and_cancels_an_active_review() -> None:
    session = GameSession.new_game("pass_play")
    coordinator, worker = _coordinator_with_review(session)

    coordinator._on_error("engine crashed")

    assert coordinator._last_error == "engine crashed"
    assert coordinator._review is not None
    assert coordinator._review["status"] == "failed"
    assert coordinator._review["error"] == "engine crashed"
    assert worker.cancelled_reviews == 1


def test_sync_pauses_live_analysis_while_a_review_runs(monkeypatch) -> None:
    session = GameSession.new_game("stockfish", {"player_color": "white", "bot_elo": 800})
    coordinator, worker = _coordinator_with_review(session)
    scheduled: list[GameSession] = []
    monkeypatch.setattr(coordinator, "ensure_scheduled", scheduled.append)

    coordinator.sync(session)

    assert scheduled == []
    assert worker.cancelled_reviews == 0


def test_sync_cancels_the_review_when_it_is_the_bots_turn(monkeypatch) -> None:
    session = GameSession.new_game("stockfish", {"player_color": "black", "bot_elo": 800})
    coordinator, worker = _coordinator_with_review(session)
    assert coordinator._is_bot_turn(session) is True
    scheduled: list[GameSession] = []
    monkeypatch.setattr(coordinator, "ensure_scheduled", scheduled.append)

    coordinator.sync(session)

    assert scheduled == [session]
    assert worker.cancelled_reviews == 1
    assert coordinator._review is not None
    assert coordinator._review["status"] == "cancelled"


def test_completed_bot_move_is_held_until_the_pace_gate() -> None:
    session = GameSession.new_game(
        "stockfish", {"player_color": "white", "bot_elo": 800, "bot_pace": "bullet"}
    )
    session.make_move("e4")
    coordinator = EngineCoordinator(worker=FakeWorker())
    token = coordinator.token_for(session)
    coordinator._token = token
    coordinator._bot_scheduled = token
    coordinator._thinking = True
    coordinator._pending_bot = {"token": token, "uci": "e7e5", "analysis": {}}
    coordinator._bot_ready_at = time.monotonic() + 0.15

    assert coordinator.take_pending_bot(session) is None
    assert coordinator.engine_payload(session)["thinking"] is True

    time.sleep(0.16)
    assert coordinator.take_pending_bot(session) == "e7e5"
    assert coordinator.engine_payload(session)["thinking"] is False


def test_low_bot_clock_shortens_the_delay() -> None:
    session = GameSession.new_game(
        "stockfish",
        {"player_color": "white", "bot_elo": 800, "bot_pace": "rapid", "time_control": "1+0"},
    )
    assert session.clock is not None

    session.clock.black_ms = 9_000
    assert all(0.18 <= bot_move_delay(session) <= 0.26 for _ in range(40))

    session.clock.black_ms = 24_000
    assert all(0.35 <= bot_move_delay(session) <= 0.55 for _ in range(40))

    session.clock.black_ms = 60_000
    assert all(1.5 <= bot_move_delay(session) <= 2.5 for _ in range(40))
