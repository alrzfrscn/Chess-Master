"""Approved Lichess logistic model and accuracy curve tests."""

from __future__ import annotations

import math

import pytest

from chess_master.analysis.winprob import (
    MATE_CP,
    move_accuracy,
    white_cp_from_mate,
    win_probability,
)


@pytest.mark.parametrize(
    ("cp", "expected"),
    [
        (0, 50.0),
        (100, 59.10258971916128),
        (400, 81.34874545019301),
        (1000, 97.54474363414323),
        (-100, 40.89741028083871),
        (-400, 18.651254549806996),
    ],
)
def test_model_b_win_probability(cp: float, expected: float) -> None:
    assert win_probability(cp) == pytest.approx(expected, abs=1e-9)


def test_win_probability_is_symmetric_and_monotonic() -> None:
    assert win_probability(0) == 50.0
    assert win_probability(100) + win_probability(-100) == pytest.approx(100.0)
    values = [win_probability(cp) for cp in range(-2000, 2001, 100)]
    assert all(left <= right for left, right in zip(values, values[1:], strict=False))


def test_win_probability_clamps_before_the_exponential() -> None:
    assert win_probability(5000) == win_probability(1500)
    assert win_probability(-5000) == win_probability(-1500)
    assert 0.0 <= win_probability(5000) <= 100.0


def test_mate_endpoints() -> None:
    assert win_probability(0, mate_for_white=True) == 100.0
    assert win_probability(0, mate_for_white=False) == 0.0


def test_mate_pseudo_cp_ordering() -> None:
    assert white_cp_from_mate(1) == MATE_CP - 100
    assert white_cp_from_mate(5) == MATE_CP - 500
    assert white_cp_from_mate(-1) == -(MATE_CP - 100)
    assert white_cp_from_mate(1) > white_cp_from_mate(5)
    assert white_cp_from_mate(0) == -MATE_CP
    assert abs(white_cp_from_mate(-8)) < MATE_CP


@pytest.mark.parametrize(
    ("loss", "expected"),
    [
        (0, 99.9999),
        (10, 63.582619307109724),
        (20, 40.020427005686074),
        (30, 24.775551619983403),
    ],
)
def test_move_accuracy_curve(loss: float, expected: float) -> None:
    assert move_accuracy(loss) == pytest.approx(expected, abs=1e-9)


def test_move_accuracy_is_bounded_and_monotonic() -> None:
    values = [move_accuracy(loss) for loss in range(0, 101)]
    assert all(0.0 <= value <= 100.0 for value in values)
    assert all(left >= right for left, right in zip(values, values[1:], strict=False))
    assert move_accuracy(-5) == move_accuracy(0)
    assert math.isclose(move_accuracy(0), 100.0, abs_tol=1e-3)
