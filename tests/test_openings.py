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


def test_explore_lists_book_continuations_from_the_start() -> None:
    data = get_default_book().explore([])
    assert data["in_book"] is True
    assert data["current"] == {
        "eco": None,
        "name": "Starting position",
        "variation": None,
        "full_name": "Starting position",
        "last_book_ply": 0,
    }
    moves = data["continuations"]
    assert {"e2e4", "d2d4", "g1f3", "c2c4"}.issubset({item["uci"] for item in moves})
    counts = [item["line_count"] for item in moves]
    assert counts == sorted(counts, reverse=True)
    assert all(item["line_count"] > 0 for item in moves)
    expected_keys = {
        "uci",
        "san",
        "from",
        "to",
        "eco",
        "name",
        "variation",
        "full_name",
        "line_count",
        "share_pct",
    }
    assert all(expected_keys.issubset(item) for item in moves)
    assert abs(sum(item["share_pct"] for item in moves) - 100.0) < 1.5


def test_explore_follows_the_prefix_into_the_book() -> None:
    data = get_default_book().explore(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"])
    assert data["in_book"] is True
    assert data["current"]["eco"] == "C50"
    assert data["current"]["full_name"].startswith("Italian Game")
    assert data["current"]["last_book_ply"] == 5

    best = data["continuations"][0]
    assert best["uci"] == "f8c5"
    assert best["san"] == "Bc5"
    assert best["from"] == "f8" and best["to"] == "c5"
    assert best["full_name"] == "Italian Game: Giuoco Piano"
    assert best["line_count"] > 0
    assert best["share_pct"] > 50


def test_explore_off_book_position_reports_no_continuations() -> None:
    data = get_default_book().explore(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "a7a5"])
    assert data["in_book"] is False
    assert data["continuations"] == []
    assert data["current"]["eco"] == "C50"


def test_explore_reenters_the_book_by_transposition() -> None:
    # Nimzo-Indian via 1.c4 e6 2.Nc3 Nf6 3.d4 Bb4, then find the EPD match.
    data = get_default_book().explore(["c2c4", "e7e6", "b1c3", "g8f6", "d2d4", "f8b4"])
    assert data["transposed"] is True
    assert data["in_book"] is True
    assert data["current"]["eco"] == "E20"
    assert data["current"]["full_name"].startswith("Nimzo-Indian")
    assert data["continuations"]


def test_search_openings_by_name_and_eco() -> None:
    book = get_default_book()

    sicilian = book.search_openings("sicilian", limit=5)
    assert len(sicilian) == 5
    assert all("sicilian" in line["full_name"].lower() for line in sicilian)

    najdorf = book.search_openings("Najdorf")
    assert najdorf
    assert all("najdorf" in line["full_name"].lower() for line in najdorf)

    eco_lines = book.search_openings(eco_prefix="B90")
    assert eco_lines
    assert all(line["eco"] == "B90" for line in eco_lines)
    expected_keys = {"eco", "name", "variation", "full_name", "uci_line", "ply_count", "epd"}
    assert all(expected_keys.issubset(line) for line in eco_lines)
    assert eco_lines[0]["ply_count"] == len(eco_lines[0]["uci_line"].split())

    assert len(book.search_openings(eco_prefix="B90", limit=2)) == 2
    assert book.search_openings("no-such-opening-name") == []


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
