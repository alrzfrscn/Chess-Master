"""Game Review pipeline: two passes, book skipping and the report contract."""

from __future__ import annotations

import chess
import pytest

from chess_master.analysis.review import (
    REVIEW_MULTIPV,
    REVIEW_SCHEMA_VERSION,
    ReviewCancelled,
    ReviewRequest,
    ReviewSearchResult,
    run_game_review,
)


def _fen_sequence(start_fen: str, moves: tuple[str, ...]) -> list[str]:
    board = chess.Board(start_fen)
    fens = [board.fen()]
    for uci in moves:
        board.push(chess.Move.from_uci(uci))
        fens.append(board.fen())
    return fens


def _search_from_map(mapping: dict[str, tuple[str | None, int]], calls: list[tuple[str, int, int]]):
    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        calls.append((board.fen(), depth, multipv))
        uci, cp = mapping[board.fen()]
        return ReviewSearchResult(
            lines=[{"rank": 1, "uci": uci, "score_cp": cp}],
            depth=depth,
            multipv=multipv,
            time_ms=5,
        )

    return search


def test_pipeline_grades_every_non_book_ply_and_builds_the_report() -> None:
    moves = ("e2e4", "e7e5", "f1c4", "b8c6")
    fens = _fen_sequence(chess.STARTING_FEN, moves)
    values = [(20, "e2e4"), (40, "d7d5"), (90, "d2d4"), (80, "b8c6"), (90, "g1f3")]
    mapping = {
        fen: (uci, cp) for fen, (cp, uci) in zip(fens, values, strict=True)
    }
    calls: list[tuple[str, int, int]] = []
    progress: list[dict] = []

    request = ReviewRequest(
        game_id="contract",
        start_fen=chess.STARTING_FEN,
        moves=moves,
        preset="fast",
        use_book=False,
    )
    report = run_game_review(
        request,
        _search_from_map(mapping, calls),
        on_progress=progress.append,
    )

    assert report["schema_version"] == REVIEW_SCHEMA_VERSION
    assert report["phase"] == 4
    assert report["status"] == "complete"
    assert report["game_id"] == "contract"
    assert report["signature"] == request.signature
    assert report["engine"]["depth"] == 10
    assert report["engine"]["confirm_depth"] == 12
    assert report["engine"]["multipv"] == REVIEW_MULTIPV
    assert report["model"]["review_clamp_cp"] == 1000
    assert report["opening"]["source"] == "none"
    assert report["progress"] == {"done": 5, "total": 5, "phase": "complete", "percent": 100.0}

    assert len(calls) == 5
    assert all(depth == 10 and multipv == 3 for _, depth, multipv in calls)
    assert report["timing"]["searches"] == 5
    assert report["timing"]["search_time_ms"] == 25
    assert report["timing"]["cache_hits"] == 0
    assert report["totals"]["searched_positions"] == 5
    assert report["totals"]["confirmed_positions"] == 0
    assert report["totals"]["graded_moves"] == 4
    assert report["totals"]["book_moves"] == 0

    moves_report = report["moves"]
    assert [move["classification"] for move in moves_report] == [
        "best",
        "good",
        "excellent",
        "best",
    ]
    assert [move["cp_loss"] for move in moves_report] == pytest.approx([0.0, 50.0, 10.0, 0.0])
    assert moves_report[3]["win_loss"] == 0.0
    assert moves_report[3]["accuracy"] == 100.0
    assert all(move["win_loss"] is not None for move in moves_report)
    assert all(move["accuracy"] is not None for move in moves_report)
    assert all(move["is_book"] is False for move in moves_report)
    assert all(move["is_forced"] is False for move in moves_report)

    summary = report["summary"]
    assert summary["counts"] == {
        "brilliant": 0,
        "great": 0,
        "best": 2,
        "excellent": 1,
        "good": 1,
        "book": 0,
        "inaccuracy": 0,
        "mistake": 0,
        "miss": 0,
        "blunder": 0,
    }
    assert summary["acpl"]["white"] == pytest.approx(5.0)
    assert summary["acpl"]["black"] == pytest.approx(25.0)
    assert summary["white"]["counts"]["best"] == 1
    assert summary["white"]["counts"]["excellent"] == 1
    assert summary["black"]["counts"]["best"] == 1
    assert summary["black"]["counts"]["good"] == 1
    assert summary["estimated_elo"]["white"] is not None
    assert summary["estimated_elo"]["black"] is not None

    for key in (
        "ply",
        "move_number",
        "color",
        "san",
        "uci",
        "fen_before",
        "fen_after",
        "is_book",
        "is_forced",
        "phase",
        "eval_before",
        "eval_after",
        "win_loss",
        "cp_loss",
        "accuracy",
        "best_uci",
        "best_san",
        "multi_pv",
        "classification",
        "label",
        "symbol",
        "explanation",
        "confirmed",
    ):
        assert key in moves_report[0]

    assert moves_report[0]["best_move"]["uci"] == "e2e4"
    assert moves_report[0]["best_move"]["san"] == "e4"
    assert moves_report[0]["best_move"]["is_played"] is True
    assert moves_report[1]["best_move"]["uci"] == "d7d5"
    assert moves_report[1]["best_move"]["is_played"] is False
    assert moves_report[0]["pv_followup"] is None

    chart = report["eval_chart"]
    assert chart["total_plies"] == 4
    assert chart["midline"] == 50.0
    assert len(chart["points"]) == 5
    assert chart["points"][0]["ply"] == 0
    assert chart["points"][4]["ply"] == 4
    assert [point["classification"] for point in chart["points"][1:]] == [
        "best",
        "good",
        "excellent",
        "best",
    ]
    assert report["key_moments"] == []

    assert progress
    assert progress[-1]["phase"] == "complete"
    assert progress[-1]["percent"] == 100.0
    assert any(item["phase"] == "scan" for item in progress)


def test_report_exposes_best_move_and_a_playable_followup_line() -> None:
    moves = ("e2e4", "e7e5")
    fens = _fen_sequence(chess.STARTING_FEN, moves)
    pv_by_fen = {
        fens[0]: ("e2e4", 20, ["e2e4", "e7e5", "g1f3"]),
        fens[1]: ("e7e5", 15, ["e7e5", "g1f3", "b8c6"]),
        fens[2]: ("g1f3", 10, ["g1f3", "b8c6", "f1b5"]),
    }

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        uci, cp, pv = pv_by_fen[board.fen()]
        return ReviewSearchResult(
            lines=[{"rank": 1, "uci": uci, "score_cp": cp, "pv_uci": pv}],
            depth=depth,
            multipv=multipv,
            time_ms=5,
        )

    request = ReviewRequest(
        game_id="followup",
        start_fen=chess.STARTING_FEN,
        moves=moves,
        preset="fast",
        use_book=False,
    )
    report = run_game_review(request, search)

    first = report["moves"][0]
    assert first["best_move"]["uci"] == "e2e4"
    followup = first["pv_followup"]
    assert followup["start_fen"] == fens[1]
    assert followup["uci"] == ["e7e5", "g1f3", "b8c6"]
    assert followup["san"] == ["e5", "Nf3", "Nc6"]
    assert followup["fens"] == [
        fens[1],
        followup["moves"][0]["fen_after"],
        followup["moves"][1]["fen_after"],
        followup["moves"][2]["fen_after"],
    ]
    assert followup["moves"][0]["color"] == "black"
    assert followup["moves"][0]["move_number"] == 1
    assert followup["moves"][1]["color"] == "white"
    assert followup["moves"][1]["move_number"] == 2
    assert report["moves"][1]["pv_followup"]["uci"] == ["g1f3", "b8c6", "f1b5"]


def test_book_prefix_is_skipped_and_unscored() -> None:
    moves = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4")

    def no_search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        raise AssertionError("Book-only review must not call Stockfish")

    request = ReviewRequest(
        game_id="book",
        start_fen=chess.STARTING_FEN,
        moves=moves,
        preset="standard",
    )
    report = run_game_review(request, no_search)

    assert report["opening"]["eco"] == "C50"
    assert report["opening"]["name"] == "Italian Game"
    assert report["opening"]["last_book_ply"] == 5
    assert report["totals"]["book_moves"] == 5
    assert report["totals"]["graded_moves"] == 0
    assert report["totals"]["searched_positions"] == 0
    assert report["timing"]["searches"] == 0
    assert report["timing"]["cache_hits"] == 0
    assert report["totals"]["counts"]["book"] == 5
    assert report["summary"]["accuracy"] == {"white": None, "black": None}
    assert report["moves"][0]["classification"] == "book"
    assert report["moves"][0]["eval_before"] is None
    assert report["moves"][0]["accuracy"] is None
    assert report["moves"][0]["cp_loss"] is None
    assert report["progress"]["percent"] == 100.0


def test_brilliant_is_confirmed_two_plies_deeper() -> None:
    start = "6k1/8/6p1/8/8/8/8/3Q2K1 w - - 0 1"
    positions = _fen_sequence(start, ("d1h5",))
    calls: list[tuple[str, int]] = []

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        calls.append((board.fen(), depth))
        if depth == 12:
            cp = 350 if board.fen() == positions[0] else 200
            uci = "d1h5" if board.fen() == positions[0] else None
        else:
            cp = 300 if board.fen() == positions[0] else 150
            uci = "d1h5" if board.fen() == positions[0] else None
        return ReviewSearchResult(
            lines=[{"rank": 1, "uci": uci, "score_cp": cp}],
            depth=depth,
            time_ms=4,
        )

    report = run_game_review(
        ReviewRequest(
            game_id="brilliant",
            start_fen=start,
            moves=("d1h5",),
            preset="fast",
        ),
        search,
    )

    assert [depth for _, depth in calls] == [10, 10, 12, 12]
    assert report["totals"]["confirmed_positions"] == 2
    assert report["totals"]["counts"]["brilliant"] == 1
    assert report["moves"][0]["classification"] == "brilliant"
    assert report["moves"][0]["confirmed"] is True
    assert report["moves"][0]["needs_confirmation"] is False
    assert report["moves"][0]["brilliant_gates"]["pass_2_confirmed"] is True
    assert report["timing"]["searches"] == 4


def test_pass_two_can_reject_a_shallow_brilliant() -> None:
    start = "6k1/8/6p1/8/8/8/8/3Q2K1 w - - 0 1"
    positions = _fen_sequence(start, ("d1h5",))

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        if depth == 12:
            if board.fen() == positions[0]:
                lines = [
                    {"rank": 1, "uci": "d1d8", "score_cp": 350},
                    {"rank": 2, "uci": "d1d7", "score_cp": 340},
                    {"rank": 3, "uci": "d1d6", "score_cp": 330},
                    {"rank": 4, "uci": "d1h5", "score_cp": 100},
                ]
            else:
                lines = [{"rank": 1, "uci": None, "score_cp": 250}]
        else:
            lines = [
                {
                    "rank": 1,
                    "uci": "d1h5" if board.fen() == positions[0] else None,
                    "score_cp": 300 if board.fen() == positions[0] else 150,
                }
            ]
        return ReviewSearchResult(lines=lines, depth=depth, time_ms=3)

    report = run_game_review(
        ReviewRequest(
            game_id="rejected",
            start_fen=start,
            moves=("d1h5",),
            preset="fast",
        ),
        search,
    )

    assert report["totals"]["confirmed_positions"] == 2
    assert report["moves"][0]["classification"] != "brilliant"
    assert report["totals"]["counts"]["brilliant"] == 0


def test_great_is_confirmed_two_plies_deeper() -> None:
    fens = _fen_sequence(chess.STARTING_FEN, ("e2e4",))
    calls: list[int] = []

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        calls.append(depth)
        if depth == 12:
            if board.fen() == fens[0]:
                lines = [
                    {"rank": 1, "uci": "e2e4", "score_cp": 150},
                    {"rank": 2, "uci": "d2d4", "score_cp": -50},
                ]
            else:
                lines = [{"rank": 1, "uci": None, "score_cp": 100}]
        else:
            if board.fen() == fens[0]:
                lines = [
                    {"rank": 1, "uci": "e2e4", "score_cp": 100},
                    {"rank": 2, "uci": "d2d4", "score_cp": -100},
                ]
            else:
                lines = [{"rank": 1, "uci": None, "score_cp": 50}]
        return ReviewSearchResult(lines=lines, depth=depth, time_ms=3)

    report = run_game_review(
        ReviewRequest(
            game_id="great",
            start_fen=chess.STARTING_FEN,
            moves=("e2e4",),
            preset="fast",
            use_book=False,
        ),
        search,
    )

    assert calls == [10, 10, 12, 12]
    assert report["totals"]["confirmed_positions"] == 2
    assert report["totals"]["counts"]["great"] == 1
    assert report["moves"][0]["classification"] == "great"
    assert report["moves"][0]["confirmed"] is True
    assert report["moves"][0]["multi_pv_cliff"] >= 10.0


def test_terminal_checkmate_is_scored_without_an_engine_search() -> None:
    start = "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"
    fen = chess.Board(start).fen()
    calls = 0

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        nonlocal calls
        calls += 1
        assert board.fen() == fen
        return ReviewSearchResult(
            lines=[{"rank": 1, "uci": "f7g7", "score_cp": 0}],
            depth=depth,
            time_ms=2,
        )

    report = run_game_review(
        ReviewRequest(
            game_id="mate",
            start_fen=start,
            moves=("f7g7",),
            preset="fast",
            use_book=False,
        ),
        search,
    )

    assert calls == 1
    assert report["timing"]["searches"] == 1
    assert report["timing"]["terminal_positions"] == 1
    assert report["moves"][0]["classification"] == "best"
    assert report["moves"][0]["cp_loss"] == 0
    assert report["moves"][0]["mate_after"] == 0


def test_cancellation_stops_before_the_next_search() -> None:
    moves = ("e2e4", "e7e5", "g1f3", "b8c6")
    fens = _fen_sequence(chess.STARTING_FEN, moves)
    mapping = {
        fen: (None, cp)
        for fen, cp in zip(fens, (20, 40, 10, 30, 20), strict=True)
    }
    calls: list[str] = []
    checks = 0

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        calls.append(board.fen())
        uci, cp = mapping[board.fen()]
        return ReviewSearchResult(
            lines=[{"rank": 1, "uci": uci, "score_cp": cp}],
            depth=depth,
            time_ms=1,
        )

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(ReviewCancelled):
        run_game_review(
            ReviewRequest(
                game_id="cancelled",
                start_fen=chess.STARTING_FEN,
                moves=moves,
                preset="fast",
                use_book=False,
            ),
            search,
            should_cancel=should_cancel,
        )

    assert len(calls) == 1


def test_review_request_rejects_depth_above_the_confirmation_cap() -> None:
    with pytest.raises(ValueError):
        ReviewRequest(
            game_id="too-deep",
            start_fen=chess.STARTING_FEN,
            moves=("e2e4",),
            depth=20,
        )


def test_depth_at_the_cap_skips_confirmation_and_leaves_candidates_unconfirmed() -> None:
    start = "6k1/8/6p1/8/8/8/8/3Q2K1 w - - 0 1"
    positions = _fen_sequence(start, ("d1h5",))

    def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
        cp = 300 if board.fen() == positions[0] else 150
        uci = "d1h5" if board.fen() == positions[0] else None
        return ReviewSearchResult(
            lines=[{"rank": 1, "uci": uci, "score_cp": cp}],
            depth=depth,
            time_ms=4,
        )

    request = ReviewRequest(
        game_id="cap",
        start_fen=start,
        moves=("d1h5",),
        depth=18,
        use_book=False,
    )

    assert request.base_depth == 18
    assert request.confirm_depth == 18
    assert request.confirm_depth >= request.base_depth

    report = run_game_review(request, search)

    assert report["schema_version"] == REVIEW_SCHEMA_VERSION
    assert report["engine"]["confirm_depth"] == request.confirm_depth
    candidates = [move for move in report["moves"] if move["needs_confirmation"]]
    assert candidates
    assert all(move["confirmed"] is False for move in candidates)
