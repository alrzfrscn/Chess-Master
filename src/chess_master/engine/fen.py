"""FEN validation with player-friendly diagnostics."""

from __future__ import annotations

import chess

from chess_master.engine.errors import InvalidFenError

__all__ = ["normalize_fen", "validate_fen"]

_STATUS_MESSAGES: tuple[tuple[chess.Status, str], ...] = (
    (chess.Status.EMPTY, "The board is empty."),
    (
        chess.Status.NO_WHITE_KING | chess.Status.NO_BLACK_KING | chess.Status.TOO_MANY_KINGS,
        "A legal position needs exactly one king for each side.",
    ),
    (
        chess.Status.TOO_MANY_WHITE_PAWNS | chess.Status.TOO_MANY_BLACK_PAWNS,
        "A side cannot have more than eight pawns.",
    ),
    (chess.Status.PAWNS_ON_BACKRANK, "Pawns cannot stand on the first or eighth rank."),
    (
        chess.Status.TOO_MANY_WHITE_PIECES | chess.Status.TOO_MANY_BLACK_PIECES,
        "A side cannot have more than sixteen pieces.",
    ),
    (
        chess.Status.BAD_CASTLING_RIGHTS,
        "Castling rights do not match the king and rook placement.",
    ),
    (
        chess.Status.INVALID_EP_SQUARE,
        "The en-passant square is not consistent with the position.",
    ),
    (
        chess.Status.OPPOSITE_CHECK,
        "The side that is not to move is in check.",
    ),
    (
        chess.Status.TOO_MANY_CHECKERS | chess.Status.IMPOSSIBLE_CHECK,
        "The position contains an impossible check configuration.",
    ),
    (
        chess.Status.RACE_CHECK | chess.Status.RACE_OVER | chess.Status.RACE_MATERIAL,
        "Racing-kings positions are not supported.",
    ),
)


def normalize_fen(fen: str) -> str:
    """Trim and collapse a FEN while preserving its six fields."""

    if not isinstance(fen, str):
        raise InvalidFenError("A FEN must be provided as text.")
    text = " ".join(fen.strip().split())
    if not text:
        raise InvalidFenError("Paste a FEN string to load a position.")
    fields = text.split(" ")
    if len(fields) != 6:
        raise InvalidFenError(
            "A FEN needs six fields: board, turn, castling, en passant, halfmove and fullmove.",
            details={"fields_found": len(fields)},
        )
    return text


def validate_fen(fen: str) -> chess.Board:
    """Parse and validate a FEN, raising :class:`InvalidFenError` on failure.

    The returned board is a fresh, legal :class:`chess.Board` that preserves
    castling rights and the en-passant square.
    """

    text = normalize_fen(fen)
    try:
        board = chess.Board(text)
    except ValueError as exc:
        raise InvalidFenError(
            "That FEN could not be parsed. Check the piece placement and fields.",
            details={"reason": str(exc)},
        ) from exc

    status = board.status()
    if status != 0:
        messages = [message for flag, message in _STATUS_MESSAGES if status & flag]
        message = messages[0] if messages else "This position is not legal."
        raise InvalidFenError(
            message,
            details={"status": str(status), "fen": text},
        )
    return board
