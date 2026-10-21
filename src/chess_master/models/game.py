"""Serializable domain objects shared by the engine and the bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["MoveRecord", "PlayerInfo"]


@dataclass(frozen=True, slots=True)
class MoveRecord:
    """One ply of the authoritative main line.

    ``captured`` stores the *piece type* (``p``, ``n``, ``b``, ``r``, ``q``)
    removed by this move. It is kept on the record so a position can later be
    rendered with an accurate captured-material strip even after promotions.
    """

    ply: int
    move_number: int
    color: str
    san: str
    uci: str
    fen_before: str
    fen_after: str
    comment: str = ""
    captured: str | None = None

    @property
    def from_square(self) -> str:
        return self.uci[:2]

    @property
    def to_square(self) -> str:
        return self.uci[2:4]

    @property
    def promotion(self) -> str | None:
        return self.uci[4:5] or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ply": self.ply,
            "move_number": self.move_number,
            "color": self.color,
            "san": self.san,
            "uci": self.uci,
            "from": self.from_square,
            "to": self.to_square,
            "promotion": self.promotion,
            "fen_after": self.fen_after,
            "comment": self.comment or None,
            "captured": self.captured,
        }


@dataclass(slots=True)
class PlayerInfo:
    """A player banner model. Ratings and titles are optional PGN metadata."""

    color: str
    name: str
    title: str | None = None
    rating: int | None = None
    is_engine: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "color": self.color,
            "name": self.name,
            "title": self.title,
            "rating": self.rating,
            "is_engine": self.is_engine,
            **self.extras,
        }
