"""Win-probability and accuracy math approved for Chess Master.

Tech-lead decision: **Model B, the Lichess logistic curve**, is the single
source of truth for evaluation math in this project. Phase 1 ships and tests
the model so Phase 2's review engine cannot drift from the approved formula.

Reference: ``.agents/skills/chess-game-review``.
"""

from __future__ import annotations

import math

__all__ = [
    "ACCURACY_MODEL",
    "MATE_CP",
    "WINPROB_MODEL",
    "WINPROB_VERSION",
    "move_accuracy",
    "model_info",
    "white_cp_from_mate",
    "win_probability",
]

WINPROB_MODEL = "lichess-logistic"
WINPROB_VERSION = 1
WINPROB_K = 0.00368208
EVAL_CLAMP_CP = 1500
MATE_CP = 10_000
ACCURACY_MODEL = "cc-103.1668"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def win_probability(cp: float, *, mate_for_white: bool | None = None) -> float:
    """White's win probability in percent for a White-POV centipawn score.

    Mate is not a centipawn value: pass ``mate_for_white`` to get the exact
    0/100 endpoint. Ordinary scores are clamped to ±1500 cp before the
    exponential, as required by the approved model.
    """

    if mate_for_white is True:
        return 100.0
    if mate_for_white is False:
        return 0.0
    score = _clamp(float(cp), -EVAL_CLAMP_CP, EVAL_CLAMP_CP)
    value = 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-WINPROB_K * score)) - 1.0)
    return _clamp(value, 0.0, 100.0)


def white_cp_from_mate(mate: int) -> int:
    """Encode mate distance as a bounded White-POV pseudo-centipawn score.

    ``mate > 0`` means White mates; ``mate < 0`` means Black mates. Mate in 1
    is stronger than mate in 8 because the magnitude decreases with distance.
    """

    if mate == 0:
        return -MATE_CP
    sign = 1 if mate > 0 else -1
    return sign * (MATE_CP - abs(mate) * 100)


def move_accuracy(win_loss: float) -> float:
    """Chess.com-style per-move accuracy from a win-percentage loss (0–100)."""

    loss = max(0.0, float(win_loss))
    accuracy = 103.1668 * math.exp(-0.04354 * loss) - 3.1669
    return _clamp(accuracy, 0.0, 100.0)


def model_info() -> dict[str, object]:
    """Stable model metadata for payloads and the review UI."""

    return {
        "winprob": WINPROB_MODEL,
        "winprob_version": WINPROB_VERSION,
        "accuracy": ACCURACY_MODEL,
        "eval_clamp_cp": EVAL_CLAMP_CP,
        "mate_cp": MATE_CP,
    }
