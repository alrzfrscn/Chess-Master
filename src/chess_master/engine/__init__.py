"""Authoritative chess rules, notation and game sessions."""

from chess_master.engine.errors import (
    ChessMasterError,
    GameOverError,
    IllegalMoveError,
    InvalidFenError,
    InvalidInputError,
    InvalidModeError,
    InvalidPgnError,
    InvalidPlyError,
    PromotionRequiredError,
)
from chess_master.engine.coordinator import EngineCoordinator
from chess_master.engine.fen import validate_fen
from chess_master.engine.game import VALID_MODES, GameSession
from chess_master.engine.material import material_summary
from chess_master.engine.pgn_io import build_pgn, parse_pgn
from chess_master.engine.stockfish import (
    ELO_PRESETS,
    EngineToken,
    StockfishManager,
    elo_profile,
)
from chess_master.engine.status import position_status

__all__ = [
    "ChessMasterError",
    "ELO_PRESETS",
    "EngineCoordinator",
    "EngineToken",
    "GameOverError",
    "GameSession",
    "IllegalMoveError",
    "InvalidFenError",
    "InvalidInputError",
    "InvalidModeError",
    "InvalidPgnError",
    "InvalidPlyError",
    "PromotionRequiredError",
    "StockfishManager",
    "VALID_MODES",
    "build_pgn",
    "elo_profile",
    "material_summary",
    "parse_pgn",
    "position_status",
    "validate_fen",
]
