"""FEN validation tests."""

from __future__ import annotations

import chess
import pytest

from chess_master.engine.errors import InvalidFenError
from chess_master.engine.fen import normalize_fen, validate_fen


def test_starting_fen_is_valid() -> None:
    board = validate_fen(chess.STARTING_FEN)
    assert board.fen() == chess.STARTING_FEN
    assert board.turn == chess.WHITE


def test_fen_whitespace_is_normalized() -> None:
    messy = "  rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR   w   KQkq   -   0   1  "
    assert normalize_fen(messy) == chess.STARTING_FEN
    assert validate_fen(messy).fen() == chess.STARTING_FEN


@pytest.mark.parametrize(
    ("fen", "fragment"),
    [
        ("", "Paste a FEN"),
        ("8/8/8/8/8/8/8/8 w - - 0", "six fields"),
        ("8/8/8/8/8/8/8/8 w - - 0 1", "empty"),
        ("4k3/8/8/8/8/8/8/8 w - - 0 1", "king"),
        ("4k3/8/8/8/8/8/8/4K3 w K - 0 1", "Castling"),
        ("4k3/8/8/8/8/8/8/K3R3 w - - 0 1", "not to move is in check"),
        ("k3P3/8/8/8/8/8/8/4K3 w - - 0 1", "eighth rank"),
        ("4k3/8/8/8/8/8/8/4K2Q w - - 0 1 extra", "six fields"),
    ],
)
def test_invalid_fens_get_actionable_errors(fen: str, fragment: str) -> None:
    with pytest.raises(InvalidFenError) as error:
        validate_fen(fen)
    assert fragment.lower() in error.value.message.lower()


def test_check_is_valid_when_the_side_to_move_is_in_check() -> None:
    board = validate_fen("4k3/8/8/8/8/8/8/K3R3 b - - 0 1")
    assert board.is_check()
    assert board.turn == chess.BLACK


def test_en_passant_square_is_preserved() -> None:
    fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
    board = validate_fen(fen)
    assert board.ep_square == chess.D6
    assert board.has_legal_en_passant()


def test_castling_rights_are_preserved() -> None:
    board = validate_fen(chess.STARTING_FEN)
    assert board.has_kingside_castling_rights(chess.WHITE)
    assert board.has_queenside_castling_rights(chess.BLACK)


def test_non_text_fen_is_rejected() -> None:
    with pytest.raises(InvalidFenError):
        normalize_fen(None)  # type: ignore[arg-type]
