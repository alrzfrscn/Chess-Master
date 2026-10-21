"""Worker lifecycle, cancellation and cache integration with real Stockfish."""

from __future__ import annotations

import threading
import time

import chess
import pytest

from chess_master.engine.stockfish import (
    EngineToken,
    StockfishManager,
    discover_engine,
    normalize_fen,
)


pytestmark = pytest.mark.engine


class Collector:
    def __init__(self) -> None:
        self.analyses: list[tuple[EngineToken, dict]] = []
        self.bots: list[tuple[EngineToken, str, dict]] = []
        self.errors: list[str] = []
        self.analysis_event = threading.Event()
        self.bot_event = threading.Event()
        self.lock = threading.Lock()

    def on_analysis(self, token: EngineToken, payload: dict) -> None:
        with self.lock:
            self.analyses.append((token, payload))
        self.analysis_event.set()

    def on_bot(self, token: EngineToken, uci: str, payload: dict) -> None:
        with self.lock:
            self.bots.append((token, uci, payload))
        self.bot_event.set()

    def on_error(self, message: str) -> None:
        with self.lock:
            self.errors.append(message)


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture(scope="module")
def engine_path() -> str:
    discovery = discover_engine()
    if not discovery.available or discovery.path is None:
        pytest.skip("Stockfish binary is not installed")
    return str(discovery.path)


def test_worker_lifecycle_and_cache_hit(engine_path: str) -> None:
    collector = Collector()
    manager = StockfishManager(engine_path, on_analysis=collector.on_analysis, on_error=collector.on_error)
    try:
        assert manager.available is True
        assert manager.ensure_started()
        assert manager.wait_until_ready(3.0)
        token = EngineToken("g-life", 1, 0, chess.STARTING_FEN)
        manager.set_active_token(token)
        assert manager.submit_analysis(
            token,
            start_fen=chess.STARTING_FEN,
            moves=[],
            depth=8,
            multipv=3,
        ) == "queued"
        assert collector.analysis_event.wait(15.0)
        manager.wait_until_idle(5.0)

        assert len(collector.analyses) == 1
        result_token, payload = collector.analyses[0]
        assert result_token == token
        assert payload["token"] == token.to_dict()
        assert payload["cached"] is False
        assert payload["available"] is True
        assert payload["multipv"] == 3
        assert payload["depth"] >= 8
        assert len(payload["lines"]) == 3
        assert payload["best_move"]["uci"] in {move.uci() for move in chess.Board().legal_moves}
        assert 0.0 <= payload["win_prob_white"] <= 100.0

        collector.analysis_event.clear()
        assert manager.submit_analysis(
            token,
            start_fen=chess.STARTING_FEN,
            moves=[],
            depth=8,
            multipv=3,
        ) == "cache"
        assert collector.analysis_event.wait(1.0)
        assert collector.analyses[-1][1]["cached"] is True
        assert manager.status_payload()["cache"]["hits"] >= 1
    finally:
        manager.stop()


def test_new_revision_cancels_stale_search(engine_path: str) -> None:
    collector = Collector()
    manager = StockfishManager(engine_path, on_analysis=collector.on_analysis)
    try:
        assert manager.ensure_started()
        assert manager.wait_until_ready(3.0)
        token_a = EngineToken("g-stale", 1, 0, chess.STARTING_FEN)
        manager.set_active_token(token_a)
        manager.submit_analysis(
            token_a,
            start_fen=chess.STARTING_FEN,
            moves=[],
            depth=22,
            multipv=3,
            use_cache=False,
        )
        assert wait_for(
            lambda: manager.status_payload()["jobs"]["running"] == "analysis",
            timeout=5.0,
        )

        board = chess.Board()
        board.push_uci("e2e4")
        token_b = EngineToken("g-stale", 2, 1, board.fen())
        manager.set_active_token(token_b)
        manager.submit_analysis(
            token_b,
            start_fen=chess.STARTING_FEN,
            moves=["e2e4"],
            depth=8,
            multipv=2,
            use_cache=False,
        )
        assert collector.analysis_event.wait(15.0)
        manager.wait_until_idle(5.0)

        assert collector.analyses
        assert all(token == token_b for token, _ in collector.analyses)
        assert collector.analyses[-1][1]["token"]["revision"] == 2
    finally:
        manager.stop()


def test_bot_job_returns_a_legal_move(engine_path: str) -> None:
    collector = Collector()
    manager = StockfishManager(engine_path, on_bot_move=collector.on_bot)
    try:
        assert manager.ensure_started()
        assert manager.wait_until_ready(3.0)
        token = EngineToken("g-bot", 7, 0, chess.STARTING_FEN)
        manager.set_active_token(token)
        assert manager.submit_bot(
            token,
            start_fen=chess.STARTING_FEN,
            moves=[],
            elo=600,
        ) == "queued"
        assert collector.bot_event.wait(15.0)
        result_token, uci, payload = collector.bots[0]
        assert result_token == token
        assert chess.Move.from_uci(uci) in chess.Board().legal_moves
        assert payload["lines"]
        assert payload["multipv"] >= 1
    finally:
        manager.stop()


def test_missing_binary_is_reported_without_starting(tmp_path) -> None:
    manager = StockfishManager(tmp_path / "missing-stockfish.exe")
    try:
        assert manager.available is False
        assert manager.status == "missing"
        assert manager.ensure_started() is False
        token = EngineToken("g-none", 1, 0, chess.STARTING_FEN)
        assert manager.submit_analysis(token, start_fen=chess.STARTING_FEN, moves=[]) == "unavailable"
        assert manager.submit_bot(token, start_fen=chess.STARTING_FEN, moves=[], elo=1000) == "unavailable"
        payload = manager.status_payload()
        assert payload["available"] is False
        assert payload["message"]
    finally:
        manager.stop()


def test_configure_path_and_cache_reset(engine_path: str) -> None:
    manager = StockfishManager(None)
    try:
        status = manager.configure_path(engine_path)
        assert status["available"] is True
        assert status["path"]
        assert manager.ensure_started()
        assert manager.wait_until_ready(3.0)
        manager.cache.put(chess.STARTING_FEN, 8, {"multipv": 1})
        status = manager.configure_path(engine_path)
        assert status["available"] is True
        assert manager.cache.stats()["entries"] == 0
        assert normalize_fen(chess.STARTING_FEN) == chess.STARTING_FEN.rsplit(" ", 2)[0]
    finally:
        manager.stop()
