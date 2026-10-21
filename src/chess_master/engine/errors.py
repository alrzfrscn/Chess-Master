"""Domain errors with stable codes for the JSON bridge."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ChessMasterError",
    "GameOverError",
    "IllegalMoveError",
    "InvalidFenError",
    "InvalidInputError",
    "InvalidModeError",
    "InvalidPgnError",
    "InvalidPlyError",
    "PromotionRequiredError",
]


class ChessMasterError(Exception):
    """Base class for errors that are safe to expose to the UI."""

    code = "chess_master_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class InvalidFenError(ChessMasterError):
    code = "invalid_fen"


class InvalidPgnError(ChessMasterError):
    code = "invalid_pgn"


class IllegalMoveError(ChessMasterError):
    code = "illegal_move"


class PromotionRequiredError(ChessMasterError):
    code = "promotion_required"


class InvalidPlyError(ChessMasterError):
    code = "invalid_ply"


class GameOverError(ChessMasterError):
    code = "game_over"


class InvalidModeError(ChessMasterError):
    code = "invalid_mode"


class InvalidInputError(ChessMasterError):
    code = "invalid_input"
