"""Bundled ECO data, trie detection and book-prefix behaviour."""

from __future__ import annotations

import chess
import pytest

from chess_master.analysis.openings import (
    DEFAULT_DATA_PATH,
    OpeningBook,
    OpeningInfo,
    OpeningLine,
    get_default_book,
)


def _replay_line(uci: tuple[str, ...]) -> chess.Board:
    board = chess.Board()
    for move_text in uci:
        board.push(chess.Move.from_uci(move_text))
    return board


def test_bundled_dataset_is_complete() -> None:
    book = get_default_book()
    stats = book.stats()
    assert DEFAULT_DATA_PATH.is_file()
    assert stats["lines"] == 3865
    assert stats["ecos"] == 500
    assert stats["polyglot"] is None


def test_every_bundled_line_is_legal() -> None:
    book = get_default_book()
    checked = 0
    for line in book.lines:
        board = chess.Board()
        for move_text in line.uci:
            move = chess.Move.from_uci(move_text)
            assert move in board.legal_moves, f"{line.eco} {line.name}: {move_text}"
            board.push(move)
        checked += 1
    assert checked == 3865


def test_detects_known_openings_to_the_deepest_named_line() -> None:
    book = get_default_book()

    italian = book.detect(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"])
    assert italian.eco == "C50"
    assert italian.name == "Italian Game"
    assert italian.last_book_ply == 5

    ruy = book.detect(
        [
            "e2e4",
            "e7e5",
            "g1f3",
            "b8c6",
            "f1b5",
            "a7a6",
            "b5a4",
            "g8f6",
            "e1g1",
            "f8e7",
        ]
    )
    assert ruy.eco == "C84"
    assert ruy.name == "Ruy Lopez"
    assert ruy.variation == "Closed"
    assert ruy.full_name == "Ruy Lopez: Closed"
    assert ruy.last_book_ply == 10
    assert ruy.book_move_ucis[-1] == "f8e7"


def test_detection_stops_at_the_first_deviation() -> None:
    book = get_default_book()
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "a7a5", "d2d4"]
    info = book.detect(moves)
    assert info.eco == "C50"
    assert info.last_book_ply == 5
    assert info.book_move_ucis == tuple(moves[:5])
    assert book.is_book_move(moves, 5) is True
    assert book.is_book_move(moves, 6) is False


def test_empty_line_is_the_starting_position() -> None:
    info = get_default_book().detect([])
    assert info.eco is None
    assert info.name == "Starting position"
    assert info.last_book_ply == 0
    assert info.source == "start"
    assert info.found is False


def test_custom_trie_and_epd_lookup() -> None:
    line = OpeningLine(
        eco="C40",
        name="King's Knight Opening",
        uci=("e2e4", "e7e5", "g1f3"),
        epd="",
    )
    board = _replay_line(line.uci)
    epd = " ".join(board.fen().split()[:4])
    line = OpeningLine(eco=line.eco, name=line.name, uci=line.uci, epd=epd)
    book = OpeningBook(lines=[line])

    found = book.lookup_position(board)
    assert found is not None
    assert found.eco == "C40"
    assert book.detect(list(line.uci)).name == "King's Knight Opening"
    assert book.detect(["e2e4", "c7c5"]).source == "none"


def test_polyglot_missing_file_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="not found"):
        OpeningBook(lines=[], polyglot_path=tmp_path / "book.bin")


def test_opening_info_marks_polyglot_only_lines() -> None:
    info = OpeningInfo(
        eco=None,
        name="Opening book",
        variation=None,
        last_book_ply=4,
        source="polyglot",
    )
    assert info.found is True
    assert info.to_dict()["source"] == "polyglot"
