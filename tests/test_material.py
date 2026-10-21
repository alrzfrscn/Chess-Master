"""Material balance and capture tests."""

from __future__ import annotations

import chess
import pytest

from chess_master.engine.game import GameSession
from chess_master.engine.material import board_material, material_summary
from chess_master.engine.replay import replay_board


def test_initial_material_is_balanced() -> None:
    board = chess.Board()
    assert board_material(board) == (39, 39)
    summary = material_summary(board)
    assert summary["advantage"] == 0
    assert summary["leader"] is None
    assert summary["white"]["captured"] == []
    assert summary["black"]["captured"] == []


def test_capture_updates_advantage_and_strip() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("d5")
    state = session.make_move("exd5")

    assert state["material"]["white"]["captured"] == ["p"]
    assert state["material"]["black"]["captured"] == []
    assert state["material"]["white"]["total"] == 39
    assert state["material"]["black"]["total"] == 38
    assert state["material"]["advantage"] == 1
    assert state["material"]["leader"] == "white"


def test_material_follows_navigation_ply() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("d5")
    session.make_move("exd5")

    at_start = session.navigate_ply(0)
    assert at_start["material"]["white"]["captured"] == []
    assert at_start["material"]["advantage"] == 0

    at_capture = session.navigate_ply(3)
    assert at_capture["material"]["white"]["captured"] == ["p"]
    assert at_capture["material"]["advantage"] == 1


def test_en_passant_capture_is_counted() -> None:
    session = GameSession.from_fen("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    state = session.make_move("exd6")

    assert state["material"]["white"]["captured"] == ["p"]
    assert state["material"]["advantage"] == 1
    assert state["moves"][0]["captured"] == "p"


def test_custom_fen_imbalance_is_reported() -> None:
    board = chess.Board("rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    summary = material_summary(board)
    assert summary["advantage"] == 9
    assert summary["leader"] == "white"


def test_promotion_increases_board_material_without_phantom_capture() -> None:
    session = GameSession.from_fen("k7/4P3/8/8/8/8/8/4K3 w - - 0 1")
    state = session.make_move("e8=Q")

    assert state["material"]["white"]["captured"] == []
    assert state["material"]["white"]["total"] == 9
    assert state["material"]["advantage"] == 9
    assert state["moves"][0]["promotion"] == "q"


def test_captured_promoted_piece_is_recorded_when_taken() -> None:
    # Black promotes on a1, then White captures the new queen.
    session = GameSession.from_fen("4k3/8/8/8/8/8/6p1/4K2R b - - 0 1")
    session.make_move("g1=Q+")
    state = session.make_move("Rxg1")

    assert state["material"]["white"]["captured"] == ["q"]
    assert state["material"]["black"]["captured"] == []


def test_replay_preserves_move_stack() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")
    state = session.navigate_ply(1)
    board = replay_board(state["start_fen"], session._moves, 1)  # noqa: SLF001 - white-box check
    assert board.move_stack[-1].uci() == "e2e4"
    assert len(board.move_stack) == 1
    assert board.turn == chess.BLACK
