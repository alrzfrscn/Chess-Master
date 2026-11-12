"""Curated endgame and tactical drills.

Every entry is a legal, non-terminal position validated by the same FEN rules
the board uses; ``tests/test_drills.py`` re-checks each one.  The frontend
renders these as cards and starts them through :meth:`ChessMasterApi.start_drill`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DRILLS", "drills_payload", "get_drill"]

DRILLS: list[dict[str, Any]] = [
    {
        "id": "queen-mate",
        "title": "Queen & King vs. Lone King",
        "category": "Basic Mate",
        "difficulty": "Beginner",
        "player_color": "white",
        "recommended_elo": 800,
        "fen": "8/8/8/4k3/8/8/8/4K2Q w - - 0 1",
        "goal": "Force the lone king to the edge and mate with the queen.",
        "hint": "Use the queen to shrink the box one rank or file at a time; keep your king close.",
    },
    {
        "id": "rook-mate",
        "title": "Rook & King vs. Lone King",
        "category": "Basic Mate",
        "difficulty": "Beginner",
        "player_color": "white",
        "recommended_elo": 900,
        "fen": "8/8/8/4k3/8/8/8/4K2R w - - 0 1",
        "goal": "Box the king in with the rook, then walk your king up to deliver mate.",
        "hint": "Cut the king off with the rook, then bring your king to the opposition.",
    },
    {
        "id": "lucena",
        "title": "Lucena Position",
        "category": "Rook Endgame",
        "difficulty": "Advanced",
        "player_color": "white",
        "recommended_elo": 1600,
        "fen": "1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1",
        "goal": "Build a bridge with the rook and promote the b-pawn.",
        "hint": "Check the black king away, then use the rook on the fourth rank as a shield.",
    },
    {
        "id": "philidor",
        "title": "Philidor Position",
        "category": "Rook Endgame",
        "difficulty": "Advanced",
        "player_color": "white",
        "recommended_elo": 1600,
        "fen": "8/8/8/8/4pk2/R7/7r/4K3 w - - 0 1",
        "goal": "Hold the draw with the third-rank defence and checks from behind.",
        "hint": "Keep the rook on the third rank until the pawn advances, then check from the far rank.",
    },
    {
        "id": "opposition",
        "title": "King & Pawn vs. King",
        "category": "Pawn Endgame",
        "difficulty": "Intermediate",
        "player_color": "white",
        "recommended_elo": 1200,
        "fen": "8/4k3/8/4K3/4P3/8/8/8 w - - 0 1",
        "goal": "Win the opposition and escort the pawn to the key squares.",
        "hint": "Take the opposition first; the pawn only moves when the king cannot lead.",
    },
    {
        "id": "rule-of-square",
        "title": "Rule of the Square",
        "category": "Pawn Endgame",
        "difficulty": "Intermediate",
        "player_color": "white",
        "recommended_elo": 1100,
        "fen": "7k/8/8/7p/P7/8/8/1K6 w - - 0 1",
        "goal": "Win the outside-pawn race by judging the square correctly.",
        "hint": "Draw the promotion square: if the enemy king cannot enter it, run the pawn.",
    },
    {
        "id": "queen-vs-pawn",
        "title": "Queen vs. Pawn on 7th Rank",
        "category": "Queen Endgame",
        "difficulty": "Advanced",
        "player_color": "white",
        "recommended_elo": 1400,
        "fen": "8/8/8/3Q4/8/1K6/p7/k7 w - - 0 1",
        "goal": "Stop the last pawn without stumbling into stalemate.",
        "hint": "Give the king room while you approach; never capture the pawn until it is safe.",
    },
    {
        "id": "two-bishops",
        "title": "Two Bishops Mate",
        "category": "Bishop Endgame",
        "difficulty": "Advanced",
        "player_color": "white",
        "recommended_elo": 1500,
        "fen": "3k4/8/8/8/2B2B2/8/8/6K1 w - - 0 1",
        "goal": "Drive the king to a corner and mate with the bishop pair.",
        "hint": "Place the bishops on adjacent diagonals and use your king to squeeze the corner.",
    },
]


def drills_payload() -> list[dict[str, Any]]:
    """Return defensive copies for the JS API."""

    return [dict(drill) for drill in DRILLS]


def get_drill(drill_id: str) -> dict[str, Any] | None:
    """Look up one drill by id; ``None`` when it does not exist."""

    for drill in DRILLS:
        if drill["id"] == drill_id:
            return dict(drill)
    return None
