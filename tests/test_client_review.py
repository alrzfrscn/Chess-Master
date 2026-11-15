"""Browser-assisted Game Review: plan, parity with the native pipeline and SQLite."""

from __future__ import annotations

import urllib.request

import chess
import pytest

from chess_master.analysis.review import ReviewRequest, ReviewSearchResult, run_game_review
from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.web_server import WebAppServer
from chess_master.paths import WEB_ROOT


def assert_ok(response: dict) -> dict:
    assert response["ok"] is True, response
    assert response["error"] is None
    return response["data"]


def _first_legal_uci(fen: str) -> str:
    return next(iter(chess.Board(fen).legal_moves)).uci()


def _payload(plan: dict, values: dict[int, int], depth: int, best: dict[int, str] | None = None) -> dict:
    """A browser payload shaped like ``wasmEngine.runReviewSearch`` output."""

    best = best or {}
    terminals = set(plan["terminal_positions"])
    payload = {}
    for index in plan["needed_positions"]:
        if index in terminals:
            continue
        fen = plan["fens"][index]
        payload[str(index)] = {
            "lines": [
                {
                    "rank": 1,
                    "uci": best.get(index, _first_legal_uci(fen)),
                    "score_cp": values[index],
                }
            ],
            "depth": depth,
            "multipv": 1,
            "time_ms": 4,
        }
    return payload


def _slim(report: dict) -> list[tuple]:
    return [
        (move["ply"], move["classification"], round(float(move["cp_loss"]), 6), round(float(move["accuracy"]), 6))
        for move in report["moves"]
    ]


def test_prepare_returns_the_real_work_plan() -> None:
    api = ChessMasterApi()
    try:
        assert_ok(api.new_game("pass_play"))
        for uci in ("e2e4", "e7e5", "g1f3"):
            assert_ok(api.make_move(uci))
        plan = assert_ok(api.prepare_client_review("fast", {"use_book": False}))

        assert plan["base_depth"] == 10
        assert plan["confirm_depth"] == 12
        assert plan["multipv"] == 3
        assert plan["last_book_ply"] == 0
        assert plan["needed_positions"] == [0, 1, 2, 3]
        assert plan["terminal_positions"] == []
        assert len(plan["fens"]) == 4
    finally:
        api._shutdown()


def test_client_report_matches_the_native_pipeline() -> None:
    api = ChessMasterApi()
    try:
        assert_ok(api.new_game("pass_play"))
        for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
            assert_ok(api.make_move(uci))
        state = assert_ok(api.get_game_state())
        plan = assert_ok(api.prepare_client_review("fast", {"use_book": False}))
        values = {0: 20, 1: 40, 2: 90, 3: 80, 4: 90}
        evals = _payload(plan, values, plan["base_depth"])

        result = assert_ok(
            api.complete_client_review("fast", evals, {"use_book": False})
        )
        assert result["confirmed"] is False

        by_fen = {
            plan["fens"][index]: ReviewSearchResult(
                lines=[
                    {
                        "rank": 1,
                        "uci": evals[str(index)]["lines"][0]["uci"],
                        "score_cp": values[index],
                    }
                ],
                depth=plan["base_depth"],
                time_ms=4,
            )
            for index in plan["needed_positions"]
        }

        def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult:
            return by_fen[board.fen()]

        direct = run_game_review(
            ReviewRequest(
                game_id=state["game_id"],
                start_fen=state["start_fen"],
                moves=tuple(move["uci"] for move in state["moves"]),
                preset="fast",
                use_book=False,
            ),
            search,
            confirm=False,
        )

        assert _slim(result["report"]) == _slim(direct)
        assert result["report"]["summary"]["counts"] == direct["summary"]["counts"]
        assert result["report"]["summary"]["accuracy"] == direct["summary"]["accuracy"]
        assert result["report"]["summary"]["acpl"] == direct["summary"]["acpl"]
    finally:
        api._shutdown()


def test_pass_two_confirms_brilliant_and_saves_the_review() -> None:
    start = "6k1/8/6p1/8/8/8/8/3Q2K1 w - - 0 1"
    api = ChessMasterApi()
    try:
        assert_ok(api.load_fen(start))
        assert_ok(api.make_move("d1h5"))
        game_id = assert_ok(api.get_game_state())["game_id"]
        plan = assert_ok(api.prepare_client_review("fast", {"use_book": False}))
        base = _payload(plan, {0: 350, 1: 200}, plan["base_depth"], best={0: "d1h5"})

        confirmation = assert_ok(
            api.find_review_confirm_positions("fast", base, {"use_book": False})
        )
        assert confirmation["confirm_depth"] == 12
        assert confirmation["needed_positions"] == [0, 1]
        assert [item["index"] for item in confirmation["positions"]] == [0, 1]

        deeper = _payload(plan, {0: 300, 1: 150}, plan["confirm_depth"], best={0: "d1h5"})
        result = assert_ok(
            api.complete_client_review(
                "fast",
                base,
                {"use_book": False, "confirm_evals": deeper},
            )
        )

        report = result["report"]
        assert result["confirmed"] is True
        assert report["moves"][0]["classification"] == "brilliant"
        assert report["moves"][0]["confirmed"] is True
        assert report["totals"]["confirmed_positions"] == 2

        stored = api._store.get_review(game_id)
        assert stored is not None
        assert stored["report"]["signature"] == report["signature"]
        assert stored["report"]["summary"]["counts"]["brilliant"] == 1
    finally:
        api._shutdown()


def test_complete_rejects_missing_browser_searches() -> None:
    api = ChessMasterApi()
    try:
        assert_ok(api.new_game("pass_play"))
        assert_ok(api.make_move("e2e4"))
        response = api.complete_client_review("fast", {}, {"use_book": False})
        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_input"
    finally:
        api._shutdown()


def test_wasm_is_served_with_the_right_mime_type() -> None:
    api = ChessMasterApi()
    server = WebAppServer(WEB_ROOT, api, port=0)
    server.start()
    try:
        with urllib.request.urlopen(
            f"{server.url}assets/vendor/stockfish/stockfish-lite.wasm", timeout=15
        ) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "application/wasm"
            assert response.read(4) == b"\x00asm"
    finally:
        server.stop()
        api._shutdown()
