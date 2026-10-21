"""Built-in ECO opening book, opening-name detection and optional Polyglot.

The bundled ``openings_data.tsv`` is derived from the CC0
``lichess-org/chess-openings`` dataset (3,865 named lines, all 500 ECO codes,
A00-E99). Each row stores the ECO code, the English name, the legal UCI line,
and the final EPD. The detector uses a UCI-prefix trie, so book status ends at
the first move that leaves named theory.

A Polyglot ``.bin`` file can be supplied for local/book extensions. Polyglot has
no ECO metadata, so it only extends ``last_book_ply``; names still come from the
ECO trie.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import chess

__all__ = [
    "DEFAULT_DATA_PATH",
    "OpeningBook",
    "OpeningInfo",
    "OpeningLine",
    "get_default_book",
]

logger = logging.getLogger(__name__)

DEFAULT_DATA_PATH = Path(__file__).with_name("openings_data.tsv")


@dataclass(frozen=True, slots=True)
class OpeningLine:
    eco: str
    name: str
    uci: tuple[str, ...]
    epd: str

    @property
    def family(self) -> str:
        return self.name.split(":", 1)[0].strip()

    @property
    def variation(self) -> str | None:
        parts = self.name.split(":", 1)
        if len(parts) < 2:
            return None
        text = parts[1].strip()
        return text or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eco": self.eco,
            "name": self.family,
            "variation": self.variation,
            "full_name": self.name,
            "epd": self.epd,
        }


@dataclass(slots=True)
class OpeningInfo:
    eco: str | None
    name: str
    variation: str | None
    last_book_ply: int
    source: str = "eco"
    full_name: str | None = None
    book_move_ucis: tuple[str, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return bool(self.eco or self.last_book_ply)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eco": self.eco,
            "name": self.name,
            "variation": self.variation,
            "last_book_ply": int(self.last_book_ply),
            "source": self.source,
            "full_name": self.full_name or self.name,
        }


@lru_cache(maxsize=1)
def _load_builtin_lines(path: Path = DEFAULT_DATA_PATH) -> tuple[OpeningLine, ...]:
    if not path.is_file():
        logger.warning("Opening data file is missing: %s", path)
        return ()
    lines: list[OpeningLine] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            uci_text = (row.get("uci") or "").strip()
            if not uci_text:
                continue
            uci = tuple(uci_text.split())
            lines.append(
                OpeningLine(
                    eco=(row.get("eco") or "").strip() or "A00",
                    name=(row.get("name") or "").strip() or "Unknown opening",
                    uci=uci,
                    epd=(row.get("epd") or "").strip(),
                )
            )
    lines.sort(key=lambda line: (len(line.uci), line.eco, line.name))
    return tuple(lines)


class OpeningBook:
    """UCI-prefix trie over the curated ECO dictionary."""

    def __init__(
        self,
        *,
        lines: Iterable[OpeningLine] | None = None,
        data_path: Path | None = None,
        polyglot_path: str | Path | None = None,
    ) -> None:
        source = tuple(lines) if lines is not None else _load_builtin_lines(data_path or DEFAULT_DATA_PATH)
        self.lines = source
        self._root: dict[str, Any] = {"children": {}}
        self._epd: dict[str, OpeningLine] = {}
        self._polyglot_reader: Any | None = None
        self._polyglot_path: Path | None = None
        for line in source:
            self._insert(line)
            if line.epd and line.epd not in self._epd:
                self._epd[line.epd] = line
        if polyglot_path:
            self._load_polyglot(polyglot_path)

    # -- construction ----------------------------------------------------
    def _insert(self, line: OpeningLine) -> None:
        node = self._root
        for move in line.uci:
            children = node["children"]
            node = children.setdefault(move, {"children": {}})
        if not node.get("eco"):
            node.update(
                {
                    "eco": line.eco,
                    "name": line.name,
                    "variation": line.variation,
                    "full_name": line.name,
                }
            )

    def _load_polyglot(self, path: str | Path) -> None:
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise ValueError(f"Polyglot book not found: {candidate}")
        try:
            import chess.polyglot

            self._polyglot_reader = chess.polyglot.open_reader(str(candidate))
            self._polyglot_path = candidate
        except Exception as exc:  # pragma: no cover - optional dependency surface
            raise ValueError(f"Could not open Polyglot book {candidate}: {exc}") from exc

    def close(self) -> None:
        if self._polyglot_reader is not None:
            try:
                self._polyglot_reader.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("Could not close Polyglot book", exc_info=True)
            self._polyglot_reader = None

    # -- lookups ---------------------------------------------------------
    def lookup_position(self, board: chess.Board) -> OpeningLine | None:
        """Find an opening line whose final EPD matches this position."""

        epd = " ".join(board.fen().split()[:4])
        line = self._epd.get(epd)
        if line is not None:
            return line
        # Some transpositions differ only by the en-passant field. Fall back to
        # the first three fields (piece placement, side, castling).
        short = " ".join(epd.split()[:3])
        for candidate_epd, candidate in self._epd.items():
            if " ".join(candidate_epd.split()[:3]) == short:
                return candidate
        return None

    def detect(self, moves: Sequence[str]) -> OpeningInfo:
        """Walk the UCI prefix and return the deepest named opening.

        Moves after the first deviation are not book moves. With a Polyglot
        book the contiguous prefix can continue beyond the ECO trie, but the
        ECO name remains the deepest named line.
        """

        uci = tuple(str(move) for move in moves)
        if not uci:
            return OpeningInfo(None, "Starting position", None, 0, "start")

        node = self._root
        depth = 0
        best: OpeningInfo | None = None
        for index, move in enumerate(uci, start=1):
            child = node.get("children", {}).get(move)
            if child is None:
                break
            node = child
            depth = index
            if node.get("eco"):
                best = OpeningInfo(
                    eco=str(node["eco"]),
                    name=str(node["name"]).split(":", 1)[0].strip(),
                    variation=node.get("variation"),
                    last_book_ply=depth,
                    source="eco",
                    full_name=node.get("full_name"),
                )

        polyglot_depth = 0
        if self._polyglot_reader is not None:
            polyglot_depth = self._polyglot_prefix_length(uci)

        last_book = max(depth, polyglot_depth)
        if best is None:
            if polyglot_depth <= 0:
                return OpeningInfo(None, "Unknown opening", None, 0, "none")
            return OpeningInfo(
                eco=None,
                name="Opening book",
                variation=None,
                last_book_ply=polyglot_depth,
                source="polyglot",
                full_name="Opening book",
            )
        best.last_book_ply = last_book
        best.book_move_ucis = uci[:last_book]
        if polyglot_depth > depth:
            best.source = "eco+polyglot"
        return best

    def _polyglot_prefix_length(self, uci: Sequence[str]) -> int:
        if self._polyglot_reader is None:
            return 0
        board = chess.Board()
        depth = 0
        for move_text in uci:
            try:
                move = chess.Move.from_uci(move_text)
            except ValueError:
                break
            if move not in board.legal_moves:
                break
            entries = list(self._polyglot_reader.find_all(board))
            if not any(entry.move == move for entry in entries):
                break
            board.push(move)
            depth += 1
        return depth

    def is_book_move(self, moves: Sequence[str], ply: int) -> bool:
        info = self.detect(moves[: max(0, int(ply))])
        return int(ply) <= info.last_book_ply

    def stats(self) -> dict[str, Any]:
        return {
            "lines": len(self.lines),
            "ecos": len({line.eco for line in self.lines}),
            "polyglot": str(self._polyglot_path) if self._polyglot_path else None,
        }


_DEFAULT_BOOK: OpeningBook | None = None


def get_default_book() -> OpeningBook:
    """Process-wide shared book so the 3,865-line trie is built once."""

    global _DEFAULT_BOOK
    if _DEFAULT_BOOK is None:
        _DEFAULT_BOOK = OpeningBook()
    return _DEFAULT_BOOK
