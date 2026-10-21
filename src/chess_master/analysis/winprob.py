"""Win-probability and accuracy math for Chess Master.

The Lichess logistic curve is the single source of truth for evaluation math.
Phase 3 adds the calibrated Game Review surface without changing the Phase 1
values: ordinary game-review scores use a tighter ``±1000 cp`` ceiling, while
the existing helpers keep the approved ``±1500 cp`` ceiling that Phase 1/2
tests assert.

References:
    ``.agents/skills/chess-game-review/SKILL.md``
    lichess-org/lila ``AccuracyPercent.scala``
    lichess-org/lila PR #11148
"""

from __future__ import annotations

import math

__all__ = [
    "ACCURACY_COEFF_A",
    "ACCURACY_COEFF_B",
    "ACCURACY_COEFF_C",
    "ACCURACY_MODEL",
    "ACCURACY_REVIEW_MODEL",
    "EVAL_CLAMP_CP",
    "INITIAL_CP",
    "LEGACY_ACCURACY_A",
    "LEGACY_ACCURACY_B",
    "LEGACY_ACCURACY_C",
    "MATE_CP",
    "REVIEW_CP_CEILING",
    "UNCERTAINTY_BONUS",
    "WINPROB_K",
    "WINPROB_MODEL",
    "WINPROB_VERSION",
    "clamp_review_cp",
    "initial_win_probability",
    "model_info",
    "move_accuracy",
    "move_accuracy_review",
    "review_win_probability",
    "white_cp_from_mate",
    "win_probability",
]

WINPROB_MODEL = "lichess-logistic"
WINPROB_VERSION = 2
WINPROB_K = 0.00368208
EVAL_CLAMP_CP = 1500
REVIEW_CP_CEILING = 1000
INITIAL_CP = 15
MATE_CP = 10_000

# Exact coefficients from the Chess.com-compatible review curve.
ACCURACY_COEFF_A = 103.1668100711649
ACCURACY_COEFF_B = 0.04354415386753951
ACCURACY_COEFF_C = 3.166924740191411
UNCERTAINTY_BONUS = 1.0

# The Phase 1/2 curve is the rounded community form. It is frozen because
# stored reviews and the original tests assert its exact values.
LEGACY_ACCURACY_A = 103.1668
LEGACY_ACCURACY_B = 0.04354
LEGACY_ACCURACY_C = 3.1669

ACCURACY_MODEL = "cc-103.1668"
ACCURACY_REVIEW_MODEL = "cc-103.1668100711649+1"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_review_cp(cp: float) -> float:
    """Clamp an ordinary White-POV score to the Game Review ceiling."""

    return _clamp(float(cp), -REVIEW_CP_CEILING, REVIEW_CP_CEILING)


def win_probability(
    cp: float,
    *,
    mate_for_white: bool | None = None,
    clamp_cp: float = EVAL_CLAMP_CP,
) -> float:
    """White's win probability in percent for a White-POV centipawn score.

    Mate is not a centipawn value: pass ``mate_for_white`` to get the exact
    0/100 endpoint. Ordinary scores are clamped before the exponential. The
    default ceiling remains ``±1500 cp`` for the Phase 1/2 contract; Game
    Review passes ``clamp_cp=REVIEW_CP_CEILING`` (``±1000 cp``).
    """

    if mate_for_white is True:
        return 100.0
    if mate_for_white is False:
        return 0.0
    score = _clamp(float(cp), -float(clamp_cp), float(clamp_cp))
    value = 100.0 / (1.0 + math.exp(-WINPROB_K * score))
    return _clamp(value, 0.0, 100.0)


def review_win_probability(cp: float, *, mate_for_white: bool | None = None) -> float:
    """Game Review win probability: same logistic with the exact ±1000 ceiling."""

    return win_probability(cp, mate_for_white=mate_for_white, clamp_cp=REVIEW_CP_CEILING)


def initial_win_probability(*, standard_start: bool = True) -> float:
    """The Ply 0 prior: +15 cp for White (≈51.38%) on the standard start."""

    if not standard_start:
        return 50.0
    return review_win_probability(INITIAL_CP)


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
    """Phase 1/2 raw Chess.com-style per-move accuracy from win-percentage loss.

    This deliberately keeps the original coefficients and *no* uncertainty
    bonus so existing tests and stored payloads remain stable.
    """

    loss = max(0.0, float(win_loss))
    accuracy = LEGACY_ACCURACY_A * math.exp(-LEGACY_ACCURACY_B * loss) - LEGACY_ACCURACY_C
    return _clamp(accuracy, 0.0, 100.0)


def move_accuracy_review(win_loss: float, *, uncertainty_bonus: bool = True) -> float:
    """Game Review move accuracy with the canonical ``+1.0`` uncertainty bonus.

    ``ΔW = 0`` returns exactly ``100.0`` because there is no loss to grade.
    For positive loss the calibrated formula is::

        103.1668100711649 * exp(-0.04354415386753951 * ΔW)
        - 3.166924740191411
        + 1.0

    clamped to ``[0, 100]``. Pass ``uncertainty_bonus=False`` when a caller
    needs the un-bonused raw curve.
    """

    loss = max(0.0, float(win_loss))
    if loss <= 0.0:
        return 100.0
    bonus = UNCERTAINTY_BONUS if uncertainty_bonus else 0.0
    accuracy = ACCURACY_COEFF_A * math.exp(-ACCURACY_COEFF_B * loss) - ACCURACY_COEFF_C + bonus
    return _clamp(accuracy, 0.0, 100.0)


def model_info() -> dict[str, object]:
    """Stable model metadata for payloads and the review UI."""

    return {
        "winprob": WINPROB_MODEL,
        "winprob_version": WINPROB_VERSION,
        "accuracy": ACCURACY_MODEL,
        "accuracy_review": ACCURACY_REVIEW_MODEL,
        "eval_clamp_cp": EVAL_CLAMP_CP,
        "review_clamp_cp": REVIEW_CP_CEILING,
        "initial_cp": INITIAL_CP,
        "initial_win_prob_white": round(initial_win_probability(), 4),
        "mate_cp": MATE_CP,
        "uncertainty_bonus": UNCERTAINTY_BONUS,
    }
