"""Volatility-weighted accuracy, phase splits and estimated Elo.

All formulas here are canonical in
``.agents/skills/chess-game-review/SKILL.md``. Keep constants in this module in
sync with that document and its tests.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

import chess

__all__ = [
    "ACPL_TRIM_MIN_MOVES",
    "ACPL_TRIM_FRACTION",
    "ELO_MAX",
    "ELO_MIN",
    "CLASSIFICATION_KEYS",
    "VOLATILITY_MAX",
    "VOLATILITY_MIN",
    "aggregate_accuracy",
    "estimated_elo",
    "phase_for_position",
    "player_metrics",
    "trimmed_acpl",
    "volatility_window_size",
    "volatility_weights",
]

ELO_MIN = 400
ELO_MAX = 3000
VOLATILITY_MIN = 0.5
VOLATILITY_MAX = 12.0
ACPL_TRIM_MIN_MOVES = 8
ACPL_TRIM_FRACTION = 0.10
ACPL_DISPLAY_CAP = 1000

CLASSIFICATION_KEYS = (
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "book",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
)

_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def volatility_window_size(move_count: int) -> int:
    """``L = clamp(floor(N / 10), 2, 8)`` for ``N`` moves."""

    return max(2, min(8, int(move_count) // 10))


def volatility_weights(win_probs_white: Sequence[float]) -> list[float]:
    """One clamped standard-deviation weight per move.

    ``win_probs_white`` is the sequential White-POV win percentage for
    positions ``0..N``. The window length is
    ``L = clamp(floor(N / 10), 2, 8)``. The boundary padding prefixes
    ``(L - 2)`` copies of the initial window ``W[0:L]``. Move ``j`` (0-based)
    then uses ``padded[j : j + L]``.

    The population standard deviation is used and the weight is clamped to
    ``[0.5, 12.0]`` percentage points.
    """

    values = [float(value) for value in win_probs_white]
    move_count = len(values) - 1
    if move_count <= 0:
        return []
    length = volatility_window_size(move_count)
    initial_window = values[:length]
    padding = initial_window * (length - 2)
    padded = padding + values
    weights: list[float] = []
    for index in range(move_count):
        window = padded[index : index + length]
        if len(window) < 2:
            deviation = 0.0
        else:
            deviation = statistics.pstdev(window)
        weights.append(_clamp(float(deviation), VOLATILITY_MIN, VOLATILITY_MAX))
    return weights


def aggregate_accuracy(
    accuracies: Sequence[float],
    weights: Sequence[float] | None = None,
) -> float | None:
    """Volatility-weighted mean blended 50/50 with the harmonic mean.

    ``A_weighted = Σ(a_i w_i) / Σw_i``
    ``A_harmonic = |M| / Σ(1 / max(a_i, 1.0))``
    ``accuracy = clamp((A_weighted + A_harmonic) / 2, 0, 100)``
    """

    values = [float(value) for value in accuracies]
    if not values:
        return None
    if weights is None:
        weight_values = [1.0 for _ in values]
    else:
        weight_values = [float(value) for value in weights]
        if len(weight_values) != len(values):
            raise ValueError("accuracy and weight sequences must be the same length")
    total_weight = sum(weight_values)
    weighted = (
        sum(value * weight for value, weight in zip(values, weight_values, strict=True)) / total_weight
        if total_weight > 0
        else sum(values) / len(values)
    )
    harmonic = len(values) / sum(1.0 / max(value, 1.0) for value in values)
    return _clamp((weighted + harmonic) / 2.0, 0.0, 100.0)


def _non_pawn_material(board: chess.Board) -> int:
    total = 0
    for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        total += len(board.pieces(piece_type, chess.WHITE)) * _PIECE_VALUES[piece_type]
        total += len(board.pieces(piece_type, chess.BLACK)) * _PIECE_VALUES[piece_type]
    return total


def phase_for_position(board: chess.Board, ply: int, last_book_ply: int = 0) -> str:
    """Deterministic material/piece-count phase for one ply.

    - ``endgame``: non-pawn material on both sides is ``<= 13`` points.
    - ``opening``: within 20 plies or still inside the opening book.
    - ``middlegame``: everything else.

    Material wins precedence so a queenless technical ending is never labelled
    "opening" merely because it arrived quickly.
    """

    if _non_pawn_material(board) <= 13:
        return "endgame"
    if int(ply) <= max(20, int(last_book_ply)):
        return "opening"
    return "middlegame"


def trimmed_acpl(cpls: Sequence[float]) -> float:
    """Mean CPL after trimming the highest 10% when at least 8 moves exist."""

    values = sorted(max(0.0, float(value)) for value in cpls)
    if not values:
        return 0.0
    if len(values) < ACPL_TRIM_MIN_MOVES:
        return sum(values) / len(values)
    trim = max(1, int(math.ceil(len(values) * ACPL_TRIM_FRACTION)))
    kept = values[: len(values) - trim]
    if not kept:
        return values[0]
    return sum(kept) / len(kept)


def _round_elo(value: float) -> int:
    rounded = int(round(value / 50.0) * 50)
    return int(_clamp(rounded, ELO_MIN, ELO_MAX))


def _expected_accuracy(baseline: float) -> float:
    return 55.0 + 45.0 / (1.0 + math.exp(-(float(baseline) - 1400.0) / 600.0))


def _expected_acpl(baseline: float) -> float:
    return _clamp(95.0 - 0.02 * float(baseline), 15.0, 95.0)


def estimated_elo(
    accuracy: float | None,
    acpl_raw: float,
    acpl_trim: float,
    *,
    blunders: int = 0,
    mistakes: int = 0,
    graded_moves: int = 0,
    baseline_elo: int | None = None,
) -> dict[str, Any]:
    """Single-game Elo estimate in ``[400, 3000]``.

    Unanchored::

        acpl_component       = 3100 * exp(-0.01 * acpl_trim)
        accuracy_component   = 400 + 2600 / (1 + exp(-0.10 * (accuracy - 75)))
        blend                = 0.55 * acpl_component + 0.45 * accuracy_component
        penalty              = 700 * blunder_rate
                             + 350 * mistake_rate
                             + 3 * max(0, acpl_raw - acpl_trim)
        unanchored           = clamp(blend - penalty, 400, 3000)

    Anchored around a known baseline::

        expected_accuracy = 55 + 45 / (1 + exp(-(R_base - 1400) / 600))
        expected_acpl     = clamp(95 - 0.02 * R_base, 15, 95)
        delta_R           = 20 * (accuracy - expected_accuracy)
                          - 4 * (acpl_trim - expected_acpl)
        anchored          = clamp(R_base + delta_R, 400, 3000)

    The displayed value is rounded to the nearest 50 Elo.
    """

    if accuracy is None or graded_moves <= 0:
        return {
            "elo": None,
            "unanchored": None,
            "anchored": None,
            "baseline": baseline_elo,
            "method": "insufficient_data",
            "components": {
                "accuracy": accuracy,
                "acpl_raw": round(float(acpl_raw), 2),
                "acpl_trim": round(float(acpl_trim), 2),
                "graded_moves": int(graded_moves),
            },
        }

    accuracy_value = _clamp(float(accuracy), 0.0, 100.0)
    raw = max(0.0, float(acpl_raw))
    trim = max(0.0, float(acpl_trim))
    denominator = max(1, int(graded_moves))
    blunder_rate = max(0, int(blunders)) / denominator
    mistake_rate = max(0, int(mistakes)) / denominator

    acpl_component = 3100.0 * math.exp(-0.01 * trim)
    accuracy_component = 400.0 + 2600.0 / (1.0 + math.exp(-0.10 * (accuracy_value - 75.0)))
    blend = 0.55 * acpl_component + 0.45 * accuracy_component
    penalty = 700.0 * blunder_rate + 350.0 * mistake_rate + 3.0 * max(0.0, raw - trim)
    unanchored = _clamp(blend - penalty, ELO_MIN, ELO_MAX)

    anchored: float | None = None
    delta_r: float | None = None
    if baseline_elo is not None:
        base = _clamp(float(baseline_elo), ELO_MIN, ELO_MAX)
        delta_r = 20.0 * (accuracy_value - _expected_accuracy(base)) - 4.0 * (
            trim - _expected_acpl(base)
        )
        anchored = _clamp(base + delta_r, ELO_MIN, ELO_MAX)

    chosen = anchored if anchored is not None else unanchored
    return {
        "elo": _round_elo(chosen),
        "unanchored": _round_elo(unanchored),
        "anchored": _round_elo(anchored) if anchored is not None else None,
        "baseline": int(baseline_elo) if baseline_elo is not None else None,
        "method": "anchored" if anchored is not None else "unanchored",
        "delta_r": round(delta_r, 2) if delta_r is not None else None,
        "components": {
            "accuracy": round(accuracy_value, 2),
            "acpl_raw": round(raw, 2),
            "acpl_trim": round(trim, 2),
            "acpl_component": round(acpl_component, 2),
            "accuracy_component": round(accuracy_component, 2),
            "behavior_penalty": round(penalty, 2),
            "blunder_rate": round(blunder_rate, 4),
            "mistake_rate": round(mistake_rate, 4),
            "graded_moves": int(graded_moves),
        },
    }


def _cpl(move: Mapping[str, Any]) -> float:
    value = move.get("cp_loss")
    if value is None:
        value = move.get("cpl")
    return float(value or 0.0)


def _percentile_weighted_accuracy(
    moves: Sequence[Mapping[str, Any]], weights: Sequence[float]
) -> tuple[float | None, float | None, float | None]:
    if not moves:
        return None, None, None
    values: list[float] = []
    move_weights: list[float] = []
    for move in moves:
        ply = int(move.get("ply", 0))
        values.append(float(move.get("accuracy") or 0.0))
        index = max(0, min(len(weights) - 1, ply - 1))
        move_weights.append(float(weights[index]) if weights else 1.0)
    combined = aggregate_accuracy(values, move_weights)
    total_weight = sum(move_weights) or 1.0
    weighted = sum(value * weight for value, weight in zip(values, move_weights, strict=True)) / total_weight
    harmonic = len(values) / sum(1.0 / max(value, 1.0) for value in values)
    return combined, weighted, harmonic


def player_metrics(
    color: str,
    moves: Sequence[Mapping[str, Any]],
    win_probs_white: Sequence[float],
    *,
    baseline_elo: int | None = None,
) -> dict[str, Any]:
    """Roll one player's moves into the public review summary."""

    color = "black" if str(color).lower() == "black" else "white"
    weights = volatility_weights(win_probs_white)
    own = [move for move in moves if move.get("color") == color]
    graded = [move for move in own if not bool(move.get("is_book"))]
    # Forced moves are not decisions: they must never pad accuracy or ACPL.
    counted = [move for move in graded if not bool(move.get("is_forced"))]
    scored = [move for move in counted if move.get("cp_loss") is not None or move.get("cpl") is not None]

    accuracy, weighted, harmonic = _percentile_weighted_accuracy(counted, weights)
    cpls = [_cpl(move) for move in scored]
    acpl_raw = sum(cpls) / len(cpls) if cpls else 0.0
    acpl_trim = trimmed_acpl(cpls)

    counts = {key: 0 for key in CLASSIFICATION_KEYS}
    for move in own:
        key = str(move.get("classification") or "")
        if key in counts:
            counts[key] += 1

    phases: dict[str, Any] = {}
    for phase in ("opening", "middlegame", "endgame"):
        phase_moves = [move for move in counted if move.get("phase") == phase]
        phase_accuracy, phase_weighted, phase_harmonic = _percentile_weighted_accuracy(
            phase_moves, weights
        )
        phase_cpls = [
            _cpl(move)
            for move in phase_moves
            if move.get("cp_loss") is not None or move.get("cpl") is not None
        ]
        phases[phase] = {
            "accuracy": round(phase_accuracy, 2) if phase_accuracy is not None else None,
            "weighted_accuracy": round(phase_weighted, 2) if phase_weighted is not None else None,
            "harmonic_accuracy": round(phase_harmonic, 2) if phase_harmonic is not None else None,
            "acpl": round(sum(phase_cpls) / len(phase_cpls), 2) if phase_cpls else None,
            "moves": len(phase_moves),
        }

    elo = estimated_elo(
        accuracy,
        acpl_raw,
        acpl_trim,
        blunders=counts["blunder"],
        mistakes=counts["mistake"],
        graded_moves=len(counted),
        baseline_elo=baseline_elo,
    )

    return {
        "color": color,
        "accuracy": round(accuracy, 2) if accuracy is not None else None,
        "weighted_accuracy": round(weighted, 2) if weighted is not None else None,
        "harmonic_accuracy": round(harmonic, 2) if harmonic is not None else None,
        "acpl": round(acpl_raw, 2),
        "acpl_trimmed": round(acpl_trim, 2),
        "moves": len(own),
        "graded_moves": len(graded),
        "counted_moves": len(counted),
        "scored_moves": len(scored),
        "book_moves": sum(1 for move in own if bool(move.get("is_book"))),
        "forced_moves": sum(1 for move in own if bool(move.get("is_forced"))),
        "counts": counts,
        "estimated_elo": elo,
        "phases": phases,
        "blunder_rate": round(counts["blunder"] / len(counted), 4) if counted else 0.0,
        "mistake_rate": round(counts["mistake"] / len(counted), 4) if counted else 0.0,
    }
