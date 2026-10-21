"""Famous games and positions used by the import panel.

Keeping the samples in Python means they are covered by the test suite: every
PGN sample is parsed through the same importer the UI uses, and every FEN
sample is passed through the same validator.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SAMPLES"]

SAMPLES: list[dict[str, str]] = [
    {
        "id": "opera-game",
        "label": "The Opera Game",
        "subtitle": "Morphy · Paris, 1858",
        "kind": "pgn",
        "text": """[Event "Casual Game"]
[Site "Paris FRA"]
[Date "1858.??.??"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]
[WhiteElo "?"]
[BlackElo "?"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6
7. Qb3 Qe7 8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7
12. O-O-O Rd8 13. Rxd7 Rxd7 14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8
17. Rd8# 1-0
""",
    },
    {
        "id": "immortal-game",
        "label": "The Immortal Game",
        "subtitle": "Anderssen · London, 1851",
        "kind": "pgn",
        "text": """[Event "Immortal Game"]
[Site "London ENG"]
[Date "1851.06.21"]
[White "Adolf Anderssen"]
[Black "Lionel Kieseritzky"]
[Result "1-0"]

1. e4 e5 2. f4 exf4 3. Bc4 Qh4+ 4. Kf1 b5 5. Bxb5 Nf6 6. Nf3 Qh6
7. d3 Nh5 8. Nh4 Qg5 9. Nf5 c6 10. g4 Nf6 11. Rg1 cxb5 12. h4 Qg6
13. h5 Qg5 14. Qf3 Ng8 15. Bxf4 Qf6 16. Nc3 Bc5 17. Nd5 Qxb2
18. Bd6 Bxg1 19. e5 Qxa1+ 20. Ke2 Na6 21. Nxg7+ Kd8 22. Qf6+ Nxf6
23. Be7# 1-0
""",
    },
    {
        "id": "scholars-mate",
        "label": "Scholar's Mate",
        "subtitle": "A four-move miniature",
        "kind": "pgn",
        "text": """[Event "Quick finish"]
[Site "Chess Master"]
[Date "2026.10.21"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
""",
    },
    {
        "id": "fen-mate-in-one",
        "label": "Mate in one",
        "subtitle": "White to play · Qg7#",
        "kind": "fen",
        "text": "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1",
    },
    {
        "id": "fen-queen-endgame",
        "label": "Queen endgame",
        "subtitle": "King and queen vs king",
        "kind": "fen",
        "text": "8/8/8/4k3/8/8/8/4K2Q w - - 0 1",
    },
    {
        "id": "fen-start",
        "label": "Starting position",
        "subtitle": "The classic 8×8",
        "kind": "fen",
        "text": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    },
]


def samples_payload() -> list[dict[str, Any]]:
    """Return a defensive copy for the JS API."""

    return [dict(sample) for sample in SAMPLES]
