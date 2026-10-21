"""Canonical review math: volatility, accuracy, ACPL, phases and Elo."""

from __future__ import annotations

import math
import statistics

import chess
import pytest

from chess_master.analysis.metrics import (
    aggregate_accuracy,
    estimated_elo,
    phase_for_position,
    player_metrics,
    trimmed_acpl,
    volatility_weights,
    volatility_window_size,
)
from chess_master.analysis.winprob import (
    clamp_review_cp,
    initial_win_probability,
    move_accuracy,
    move_accuracy_review,
    review_win_probability,
)


@pytest.mark.parametrize(
    ("moves", "expected"),
    [(0, 2), (5, 2), (19, 2), (20, 2), (39, 3), (80, 8), (200, 8)],
)
def test_volatility_window_size(moves: int, expected: int) -> None:
    assert volatility_window_size(moves) == expected


def test_volatility_weights_are_per_move_and_clamped() -> None:
    flat = volatility_weights([50.0] * 11)
    assert len(flat) == 10
    assert flat == [0.5] * 10

    jumpy = volatility_weights([0.0, 100.0] * 6)
    assert len(jumpy) == 11
    assert all(value == 12.0 for value in jumpy)

    values = [50.0, 52.0, 55.0, 53.0, 60.0, 58.0]
    weights = volatility_weights(values)
    assert len(weights) == len(values) - 1
    assert all(0.5 <= value <= 12.0 for value in weights)
    assert weights[0] == pytest.approx(statistics.pstdev([50.0, 52.0]))
    assert weights[-1] == pytest.approx(statistics.pstdev([58.0, 60.0]))


def test_aggregate_accuracy_blends_weighted_and_harmonic_means() -> None:
    assert aggregate_accuracy([]) is None
    assert aggregate_accuracy([90]) == pytest.approx(90.0)
    assert aggregate_accuracy([100, 50]) == pytest.approx(70.8333333333)
    assert aggregate_accuracy([100, 50], [3, 1]) == pytest.approx(77.0833333333)
    with pytest.raises(ValueError):
        aggregate_accuracy([90, 80], [1])


def test_trimmed_acpl_only_trims_at_eight_moves() -> None:
    assert trimmed_acpl([10, 20, 30]) == pytest.approx(20.0)
    assert trimmed_acpl([10, 20, 30, 40, 50, 60, 70, 80]) == pytest.approx(40.0)
    assert trimmed_acpl(list(range(10))) == pytest.approx(4.0)
    assert trimmed_acpl([-5, 10]) == pytest.approx(5.0)


def test_phase_for_position_prefers_endgame_material() -> None:
    assert phase_for_position(chess.Board(), 1) == "opening"
    assert phase_for_position(chess.Board(), 12, last_book_ply=14) == "opening"
    assert phase_for_position(chess.Board(), 25) == "middlegame"
    endgame = chess.Board("8/8/8/4k3/8/4K3/8/8 w - - 0 1")
    assert phase_for_position(endgame, 1) == "endgame"
    queen_endgame = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    assert phase_for_position(queen_endgame, 1) == "endgame"
    queens = chess.Board("3qk3/8/8/8/8/8/8/3QK3 w - - 0 1")
    assert phase_for_position(queens, 1) == "opening"


def test_review_math_keeps_the_phase_one_curve() -> None:
    assert clamp_review_cp(5000) == 1000
    assert clamp_review_cp(-5000) == -1000
    assert review_win_probability(5000) == pytest.approx(review_win_probability(1000))
    assert initial_win_probability() == pytest.approx(review_win_probability(15))
    assert initial_win_probability(standard_start=False) == 50.0
    assert move_accuracy_review(0) == 100.0
    assert move_accuracy_review(10) > move_accuracy(10)
    assert move_accuracy_review(10, uncertainty_bonus=False) < move_accuracy_review(10)


def test_estimated_elo_is_rounded_bounded_and_anchored() -> None:
    missing = estimated_elo(None, 0, 0, graded_moves=0)
    assert missing["elo"] is None
    assert missing["method"] == "insufficient_data"

    strong = estimated_elo(92, 18, 15, graded_moves=30)
    assert strong["method"] == "unanchored"
    assert strong["elo"] is not None
    assert strong["elo"] % 50 == 0
    assert 400 <= strong["elo"] <= 3000

    weak = estimated_elo(25, 320, 300, blunders=4, mistakes=4, graded_moves=10)
    assert weak["elo"] is not None
    assert weak["elo"] < strong["elo"]

    anchored = estimated_elo(80, 30, 25, graded_moves=20, baseline_elo=1500)
    assert anchored["method"] == "anchored"
    assert anchored["baseline"] == 1500
    assert anchored["anchored"] is not None
    assert anchored["elo"] == anchored["anchored"]
    assert anchored["elo"] % 50 == 0

    clamped = estimated_elo(100, 0, 0, graded_moves=10, baseline_elo=3000)
    assert 400 <= clamped["elo"] <= 3000


def test_player_metrics_exclude_book_and_forced_moves() -> None:
    moves = [
        {
            "ply": 1,
            "color": "white",
            "is_book": True,
            "classification": "book",
            "accuracy": 100.0,
            "cp_loss": 0.0,
            "phase": "opening",
        },
        {
            "ply": 2,
            "color": "black",
            "is_book": True,
            "classification": "book",
            "accuracy": 100.0,
            "cp_loss": 0.0,
            "phase": "opening",
        },
        {
            "ply": 3,
            "color": "white",
            "is_forced": True,
            "classification": "best",
            "accuracy": 0.0,
            "cp_loss": 500.0,
            "phase": "opening",
        },
        {
            "ply": 4,
            "color": "black",
            "classification": "best",
            "accuracy": 100.0,
            "cp_loss": 0.0,
            "phase": "opening",
        },
        {
            "ply": 5,
            "color": "white",
            "classification": "good",
            "accuracy": 80.0,
            "cp_loss": 20.0,
            "phase": "middlegame",
        },
        {
            "ply": 6,
            "color": "black",
            "classification": "blunder",
            "accuracy": 60.0,
            "cp_loss": 100.0,
            "phase": "middlegame",
        },
    ]
    win_probs = [50.0, 55.0, 54.0, 57.0, 53.0, 56.0, 50.0]

    white = player_metrics("white", moves, win_probs)
    assert white["moves"] == 3
    assert white["graded_moves"] == 2
    assert white["book_moves"] == 1
    assert white["forced_moves"] == 1
    assert white["counted_moves"] == 1
    assert white["scored_moves"] == 1
    assert white["acpl"] == pytest.approx(20.0)
    assert white["accuracy"] == pytest.approx(80.0)
    assert white["counts"]["book"] == 1
    assert white["counts"]["best"] == 1
    assert white["counts"]["good"] == 1
    assert white["phases"]["middlegame"]["moves"] == 1
    assert white["phases"]["opening"]["moves"] == 0
    assert white["weighted_accuracy"] == pytest.approx(80.0)
    assert white["harmonic_accuracy"] == pytest.approx(80.0)

    black = player_metrics("black", moves, win_probs)
    assert black["counted_moves"] == 2
    assert black["acpl"] == pytest.approx(50.0)
    assert black["estimated_elo"]["elo"] is not None
    assert black["blunder_rate"] == pytest.approx(0.5)
    assert black["accuracy"] is not None
    assert 60.0 < black["accuracy"] < 100.0
