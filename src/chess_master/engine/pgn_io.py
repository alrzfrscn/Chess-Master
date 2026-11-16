"""PGN import and export on top of :mod:`chess.pgn`."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import chess
import chess.pgn

from chess_master.engine.errors import InvalidPgnError
from chess_master.engine.fen import validate_fen
from chess_master.engine.replay import capture_on_move
from chess_master.models.game import MoveRecord

__all__ = ["PgnImport", "build_pgn", "parse_pgn", "players_from_headers"]

_SUPPORTED_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}


@dataclass(slots=True)
class PgnImport:
    """A validated main line extracted from a PGN document."""

    start_fen: str
    moves: list[MoveRecord]
    headers: dict[str, str]
    start_comment: str = ""
    variants_ignored: int = 0
    comment_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def meta(self) -> dict[str, Any]:
        return {
            "variations_ignored": self.variants_ignored,
            "comment_count": self.comment_count,
            "warnings": list(self.warnings),
        }


def _count_variation_edges(root: chess.pgn.GameNode) -> int:
    total = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if len(node.variations) > 1:
            total += len(node.variations) - 1
        stack.extend(node.variations)
    return total


def _safe_rating(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip()
    if not text.isdigit():
        return None
    rating = int(text)
    return rating if 0 < rating < 4000 else None


def players_from_headers(headers: dict[str, str]) -> dict[str, Any]:
    """Build player banner data from PGN tags, using sensible fallbacks."""

    players: dict[str, Any] = {}
    for color, name_key, elo_key, title_key in (
        ("white", "White", "WhiteElo", "WhiteTitle"),
        ("black", "Black", "BlackElo", "BlackTitle"),
    ):
        raw_name = (headers.get(name_key) or "").strip()
        name = raw_name if raw_name and raw_name != "?" else color.title()
        title = (headers.get(title_key) or "").strip() or None
        players[color] = {
            "color": color,
            "name": name,
            "title": title,
            "rating": _safe_rating(headers.get(elo_key)),
            "is_engine": False,
            "extras": {},
        }
    return players


def parse_pgn(pgn_text: str) -> PgnImport:
    """Parse a PGN document and replay its main line.

    * Multiple header tags, multi-line tag blocks, brace comments and
      semicolon comments are supported through python-chess.
    * Variations are intentionally skipped in Phase 1; their count is reported
      so the UI can tell the user what was left out.
    * Any illegal move or malformed move text rejects the whole import rather
      than silently producing a partial game.
    """

    if not isinstance(pgn_text, str):
        raise InvalidPgnError("A PGN must be provided as text.")
    text = pgn_text.strip()
    if not text:
        raise InvalidPgnError("Paste a PGN to import a game.")

    try:
        game = chess.pgn.read_game(io.StringIO(text))
    except (ValueError, KeyError, IndexError) as exc:
        raise InvalidPgnError(
            "That PGN could not be parsed.",
            details={"reason": str(exc)},
        ) from exc

    if game is None:
        raise InvalidPgnError("No PGN game was found in that text.")

    if game.errors:
        raise InvalidPgnError(
            "The PGN contains illegal or malformed moves.",
            details={"errors": [str(error) for error in game.errors[:5]]},
        )

    headers = {key: value for key, value in game.headers.items() if value is not None}
    variant = (headers.get("Variant") or "Standard").strip().lower()
    if variant not in {"standard", "chess"}:
        raise InvalidPgnError(
            "Only standard-chess PGNs are supported in Phase 1.",
            details={"variant": headers.get("Variant")},
        )

    try:
        start_board = game.board()
    except ValueError as exc:
        raise InvalidPgnError(
            "The PGN's setup position is invalid.",
            details={"reason": str(exc)},
        ) from exc

    # Validate the setup position with the same rules as the FEN importer.
    start_fen = validate_fen(start_board.fen()).fen()

    board = chess.Board(start_fen)
    moves: list[MoveRecord] = []
    comment_count = 0

    for node in game.mainline():
        move = node.move
        if move is None:
            raise InvalidPgnError("The PGN contains a null move, which is not supported.")
        if move not in board.legal_moves:
            raise InvalidPgnError(
                "The PGN contains a move that is illegal in sequence.",
                details={"ply": len(moves) + 1, "move": str(move)},
            )

        fen_before = board.fen()
        color = "white" if board.turn == chess.WHITE else "black"
        move_number = board.fullmove_number
        san = board.san(move)
        captured = capture_on_move(board, move)
        board.push(move)

        comment = (node.comment or "").strip()
        if comment:
            comment_count += 1

        moves.append(
            MoveRecord(
                ply=len(moves) + 1,
                move_number=move_number,
                color=color,
                san=san,
                uci=move.uci(),
                fen_before=fen_before,
                fen_after=board.fen(),
                comment=comment,
                captured=captured,
            )
        )

    if not moves:
        raise InvalidPgnError("The PGN does not contain any moves.")

    variants_ignored = _count_variation_edges(game)
    warnings: list[str] = []
    if variants_ignored:
        warnings.append(
            f"{variants_ignored} variation line"
            f"{'s were' if variants_ignored != 1 else ' was'} skipped; "
            "Phase 1 keeps the main line only."
        )

    result = headers.get("Result", "*")
    if result not in _SUPPORTED_RESULTS:
        headers["Result"] = "*"

    return PgnImport(
        start_fen=start_fen,
        moves=moves,
        headers=headers,
        start_comment=(game.comment or "").strip(),
        variants_ignored=variants_ignored,
        comment_count=comment_count,
        warnings=warnings,
    )


def build_pgn(
    start_fen: str,
    headers: dict[str, str],
    moves: list[MoveRecord],
    *,
    start_comment: str = "",
) -> str:
    """Serialize the current main line into a clean, round-trippable PGN."""

    game = chess.pgn.Game()
    for key, value in headers.items():
        game.headers[key] = value

    if start_fen != chess.STARTING_FEN:
        game.setup(start_fen)

    result = headers.get("Result", "*")
    game.headers["Result"] = result if result in _SUPPORTED_RESULTS else "*"

    if start_comment:
        game.comment = start_comment

    node: chess.pgn.ChildNode = game
    board = chess.Board(start_fen)
    for record in moves:
        move = chess.Move.from_uci(record.uci)
        if move not in board.legal_moves:
            raise InvalidPgnError(
                "Cannot export a game with an illegal move.",
                details={"ply": record.ply, "uci": record.uci},
            )
        node = node.add_main_variation(move)
        if record.comment:
            node.comment = record.comment
        board.push(move)

    return str(game)
