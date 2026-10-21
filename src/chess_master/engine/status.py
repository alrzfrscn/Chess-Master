"""Position status helpers shared by the game session and the UI payload."""

from __future__ import annotations

from typing import Any

import chess

__all__ = ["position_status", "result_from_status"]

_RESULT_BY_WINNER = {"white": "1-0", "black": "0-1"}


def position_status(board: chess.Board) -> dict[str, Any]:
    """Describe check, checkmate, stalemate and automatic draw states."""

    check = board.is_check()
    check_square = None
    if check:
        king_square = board.king(board.turn)
        if king_square is not None:
            check_square = chess.square_name(king_square)

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
        return {
            "state": "checkmate",
            "reason": "checkmate",
            "winner": winner,
            "result": _RESULT_BY_WINNER[winner],
            "check": True,
            "check_square": check_square,
            "claimable_draw": None,
        }

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

    return {
        "state": "draw",
        "reason": reason,
        "winner": None,
        "result": "1/2-1/2",
        "check": False,
        "check_square": None,
        "claimable_draw": None,
    }


def result_from_status(status: dict[str, Any], fallback: str = "*") -> str:
    """Prefer the board result; otherwise keep the imported PGN result."""

    if status["state"] != "in_progress":
        return status["result"]
    return fallback if fallback in {"1-0", "0-1", "1/2-1/2", "*"} else "*"
