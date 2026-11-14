"""Position status helpers shared by the game session and the UI payload."""

from __future__ import annotations

from typing import Any

import chess

__all__ = ["position_status", "result_from_status"]

_RESULT_BY_WINNER = {"white": "1-0", "black": "0-1"}


def _terminal(
    state: str,
    reason: str,
    winner: str | None,
    *,
    check: bool = False,
    check_square: str | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "winner": winner,
        "result": _RESULT_BY_WINNER.get(winner or "", "1/2-1/2"),
        "check": check,
        "check_square": check_square,
        "claimable_draw": None,
    }


def position_status(
    board: chess.Board,
    *,
    resigned_by: str | None = None,
    draw_agreed: bool = False,
) -> dict[str, Any]:
    """Describe check, checkmate, stalemate and explicit player endings.

    ``resigned_by`` and ``draw_agreed`` come from a live table: they take
    precedence over the board because the game ended by player action, not by
    the position on the board.
    """

    check = board.is_check()
    check_square = None
    if check:
        king_square = board.king(board.turn)
        if king_square is not None:
            check_square = chess.square_name(king_square)

    if resigned_by in ("white", "black"):
        winner = "black" if resigned_by == "white" else "white"
        return _terminal(
            "resigned", "resignation", winner, check=check, check_square=check_square
        )
    if draw_agreed:
        return _terminal("draw", "agreement", None)

    claimable_draw = None
    if board.is_fifty_moves():
        claimable_draw = "fifty_moves"
    elif board.is_repetition(3):
        claimable_draw = "threefold_repetition"

    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return {
            "state": "in_progress",
            "reason": None,
            "winner": None,
            "result": "*",
            "check": check,
            "check_square": check_square,
            "claimable_draw": claimable_draw,
        }

    termination = outcome.termination
    if termination == chess.Termination.CHECKMATE:
        winner = "white" if outcome.winner == chess.WHITE else "black"
        return _terminal(
            "checkmate", "checkmate", winner, check=True, check_square=check_square
        )

    if termination == chess.Termination.STALEMATE:
        reason = "stalemate"
    elif termination == chess.Termination.INSUFFICIENT_MATERIAL:
        reason = "insufficient_material"
    elif termination == chess.Termination.SEVENTYFIVE_MOVES:
        reason = "seventyfive_moves"
    elif termination == chess.Termination.FIVEFOLD_REPETITION:
        reason = "fivefold_repetition"
    else:
        reason = str(termination).lower()

    return _terminal("draw", reason, None)


def result_from_status(status: dict[str, Any], fallback: str = "*") -> str:
    """Prefer the board result; otherwise keep the imported PGN result."""

    if status["state"] != "in_progress":
        return status["result"]
    return fallback if fallback in {"1-0", "0-1", "1/2-1/2", "*"} else "*"
