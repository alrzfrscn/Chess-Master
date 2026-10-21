"""Material balance and captured-piece bookkeeping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import chess

from chess_master.models.game import MoveRecord

__all__ = [
    "PIECE_VALUES",
    "board_material",
    "captured_for_ply",
    "material_summary",
    "piece_letter",
]

PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

_LETTERS: dict[int, str] = {
    chess.PAWN: "p",
    chess.KNIGHT: "n",
    chess.BISHOP: "b",
    chess.ROOK: "r",
    chess.QUEEN: "q",
    chess.KING: "k",
}


def piece_letter(piece: chess.Piece) -> str:
    return _LETTERS[piece.piece_type]


def board_material(board: chess.Board) -> tuple[int, int]:
    """Return ``(white_total, black_total)`` in pawn units."""

    white = 0
    black = 0
    for piece in board.piece_map().values():
        value = PIECE_VALUES[piece.piece_type]
        if piece.color == chess.WHITE:
            white += value
        else:
            black += value
    return white, black


def captured_for_ply(moves: Sequence[MoveRecord], ply: int) -> dict[str, list[str]]:
    """Pieces captured by each side up to ``ply``.

    The returned lists use lowercase piece letters. They are sorted by value so
    the UI can render a tidy, Chess.com-style capture strip.
    """

    captured: dict[str, list[str]] = {"white": [], "black": []}
    for record in moves[: max(0, ply)]:
        if record.captured:
            captured[record.color].append(record.captured)

    for color in ("white", "black"):
        captured[color].sort(key=lambda letter: _letter_value(letter))
    return captured


def _letter_value(letter: str) -> int:
    return {
        "p": 1,
        "n": 3,
        "b": 3,
        "r": 5,
        "q": 9,
        "k": 0,
    }.get(letter, 0)


def material_summary(
    board: chess.Board,
    moves: Sequence[MoveRecord] = (),
    ply: int = 0,
) -> dict[str, Any]:
    """Build the material payload consumed by the player banners.

    ``advantage`` is positive when White is ahead, in pawn units. Captured
    lists are computed from the move records, so promoted pieces do not create
    phantom captures.
    """

    white_total, black_total = board_material(board)
    captured = captured_for_ply(moves, ply)
    advantage = white_total - black_total
    return {
        "white": {"total": white_total, "captured": captured["white"]},
        "black": {"total": black_total, "captured": captured["black"]},
        "advantage": advantage,
        "leader": "white" if advantage > 0 else "black" if advantage < 0 else None,
    }
