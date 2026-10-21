"""PGN import/export tests."""

from __future__ import annotations

import chess
import pytest

from chess_master.engine.errors import InvalidPgnError
from chess_master.engine.game import GameSession
from chess_master.engine.pgn_io import build_pgn, parse_pgn
from chess_master.models.samples import SAMPLES


def test_multiline_header_block_and_comments(read_fixture) -> None:
    parsed = parse_pgn(read_fixture("ruy_lopez_comments.pgn"))

    assert parsed.headers["White"] == "Alice"
    assert parsed.headers["Black"] == "Bob"
    assert parsed.headers["WhiteTitle"] == "IM"
    assert parsed.headers["WhiteElo"] == "2100"
    assert parsed.headers["Comment"].startswith("A header block")
    assert parsed.start_comment == "Starting comment before the first move."
    assert parsed.comment_count >= 2
    assert parsed.moves[0].comment == "King's pawn"
    assert parsed.moves[5].comment == "Main line"
    assert len(parsed.moves) == 20
    assert parsed.moves[-1].san == "Nbd7"
    assert parsed.moves[-1].fen_after == parsed.moves[-1].fen_after


def test_pgn_replays_to_expected_fen(read_fixture) -> None:
    session = GameSession.from_pgn(read_fixture("ruy_lopez_comments.pgn"))
    state = session.to_state()

    assert state["source"] == "pgn"
    assert state["mode"] == "import"
    assert state["players"]["white"]["name"] == "Alice"
    assert state["players"]["white"]["title"] == "IM"
    assert state["players"]["white"]["rating"] == 2100
    assert state["total_plies"] == 20
    assert state["ply"] == 20
    assert state["fen"] == session.board.fen()
    assert state["status"]["state"] == "in_progress"
    assert state["headers"]["Result"] == "1-0"


def test_semicolon_and_brace_comments_are_preserved(read_fixture) -> None:
    parsed = parse_pgn(read_fixture("ruy_lopez_comments.pgn"))
    exported = build_pgn(parsed.start_fen, parsed.headers, parsed.moves, start_comment=parsed.start_comment)
    assert "Starting comment before the first move." in exported
    assert "King's pawn" in exported
    assert "Main line" in exported


def test_variations_are_skipped_and_counted(read_fixture) -> None:
    parsed = parse_pgn(read_fixture("variations.pgn"))
    assert parsed.variants_ignored >= 3
    assert any("variation" in warning.lower() for warning in parsed.warnings)
    assert [move.san for move in parsed.moves] == [
        "e4",
        "e5",
        "Nf3",
        "Nc6",
        "Bb5",
        "a6",
        "Ba4",
        "Nf6",
    ]
    assert parsed.meta["variations_ignored"] == parsed.variants_ignored


def test_fen_setup_pgn() -> None:
    parsed = parse_pgn((__import__("pathlib").Path(__file__).parent / "fixtures" / "fen_setup.pgn").read_text(encoding="utf-8"))
    assert parsed.start_fen == "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"
    assert len(parsed.moves) == 1
    assert parsed.moves[0].san == "Qg7#"

    session = GameSession.from_pgn(
        (__import__("pathlib").Path(__file__).parent / "fixtures" / "fen_setup.pgn").read_text(encoding="utf-8")
    )
    state = session.to_state()
    assert state["start_fen"] == parsed.start_fen
    assert state["status"]["state"] == "checkmate"
    assert state["status"]["winner"] == "white"
    assert state["headers"]["SetUp"] == "1"


def test_illegal_pgn_is_rejected(read_fixture) -> None:
    with pytest.raises(InvalidPgnError) as error:
        parse_pgn(read_fixture("illegal_move.pgn"))
    assert "illegal" in error.value.message.lower()


def test_empty_pgn_is_rejected() -> None:
    with pytest.raises(InvalidPgnError):
        parse_pgn("   ")


def test_header_only_pgn_is_rejected() -> None:
    with pytest.raises(InvalidPgnError):
        parse_pgn('[Event "No moves"]\n[Result "*"]\n')


def test_non_standard_variant_is_rejected() -> None:
    pgn = """[Event "Chess960"]
[Variant "Chess960"]
[SetUp "1"]
[FEN "bqnb1rkr/pp3ppp/3ppn2/2p5/5P2/P2P4/NPP1P1PP/BQ1BNRKR w HFhf - 2 9"]

1. Nf3 *
"""
    with pytest.raises(InvalidPgnError):
        parse_pgn(pgn)


def test_pgn_round_trip_preserves_main_line_and_headers(read_fixture) -> None:
    parsed = parse_pgn(read_fixture("ruy_lopez_comments.pgn"))
    exported = build_pgn(parsed.start_fen, parsed.headers, parsed.moves, start_comment=parsed.start_comment)
    reparsed = parse_pgn(exported)

    assert reparsed.headers["White"] == parsed.headers["White"]
    assert reparsed.start_fen == parsed.start_fen
    assert [(m.san, m.uci) for m in reparsed.moves] == [(m.san, m.uci) for m in parsed.moves]
    assert reparsed.moves[0].comment == parsed.moves[0].comment
    assert reparsed.moves[5].comment == parsed.moves[5].comment


def test_session_export_uses_current_branch() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")
    session.navigate_ply(1)
    session.make_move("c5")

    exported = session.export_pgn()["pgn"]
    assert "1. e4 c5" in exported
    assert "e5" not in exported.split("1. e4 c5", 1)[1]


def test_exported_pgn_can_be_reimported() -> None:
    session = GameSession.new_game()
    for move in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6"):
        session.make_move(move)
    exported = session.export_pgn()["pgn"]
    reparsed = parse_pgn(exported)
    assert [m.uci for m in reparsed.moves] == [m.uci for m in session._moves]  # noqa: SLF001


def test_pgn_result_is_preserved_on_import() -> None:
    pgn = """[Event "Resignation"]
[White "A"]
[Black "B"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 0-1
"""
    parsed = parse_pgn(pgn)
    assert parsed.headers["Result"] == "0-1"


@pytest.mark.parametrize("sample", [s for s in SAMPLES if s["kind"] == "pgn"], ids=lambda s: s["id"])
def test_famous_pgn_samples_are_valid(sample) -> None:
    parsed = parse_pgn(sample["text"])
    assert len(parsed.moves) > 0


@pytest.mark.parametrize("sample", [s for s in SAMPLES if s["kind"] == "fen"], ids=lambda s: s["id"])
def test_fen_samples_are_valid(sample) -> None:
    board = chess.Board(sample["text"])
    assert board.is_valid()
