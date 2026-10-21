"""Replay moves from a FEN root into a fresh python-chess board."""

from __future__ import annotations

from collections.abc import Sequence

import chess

from chess_master.engine.errors import InvalidPgnError
from chess_master.models.game import MoveRecord

__all__ = ["capture_on_move", "replay_board"]


def replay_board(
    start_fen: str,
    moves: Sequence[MoveRecord],
    up_to: int | None = None,
) -> chess.Board:
    """Return a board with the move stack restored up to ``up_to`` plies.

    Keeping a real move stack (rather than loading a bare FEN) preserves
    repetition and fifty-move context for the future review engine.
    """

    board = chess.Board(start_fen)
    limit = len(moves) if up_to is None else max(0, min(up_to, len(moves)))
    for record in moves[:limit]:
        try:
            move = chess.Move.from_uci(record.uci)
        except ValueError as exc:
            raise InvalidPgnError(
                "The game contains an unreadable move.",
                details={"ply": record.ply, "uci": record.uci},
            ) from exc
        if move not in board.legal_moves:
            raise InvalidPgnError(
                "The game contains a move that is not legal in sequence.",
                details={"ply": record.ply, "uci": record.uci, "fen": board.fen()},
            )
        board.push(move)
    return board


def capture_on_move(board: chess.Board, move: chess.Move) -> str | None:
    """Return the captured piece letter before ``move`` is pushed.

    En-passant captures remove a pawn that is not on the destination square.
    """

    if not board.is_capture(move):
        return None

    if board.is_en_passant(move):
        captured_square = chess.square(
            chess.square_file(move.to_square),
            chess.square_rank(move.from_square),
        )
    else:
        captured_square = move.to_square

    piece = board.piece_at(captured_square)
    return piece.symbol().lower() if piece else None
