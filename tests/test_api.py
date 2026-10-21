"""Bridge API contract tests, including HTTP serialization."""

from __future__ import annotations

import json

import pytest

from chess_master.bridge.api import ChessMasterApi
from chess_master.engine.game import GameSession
from chess_master.models.samples import SAMPLES


@pytest.fixture
def api() -> ChessMasterApi:
    return ChessMasterApi()


def assert_ok(response: dict) -> dict:
    assert response["ok"] is True
    assert response["error"] is None
    assert isinstance(response["data"], dict)
    return response["data"]


def assert_error(response: dict, code: str) -> dict:
    assert response["ok"] is False
    assert response["data"] is None
    assert response["error"]["code"] == code
    assert response["error"]["message"]
    return response["error"]


def test_api_info(api: ChessMasterApi) -> None:
    data = assert_ok(api.get_api_info())
    assert data["phase"] == 3
    assert data["analysis"]["winprob"] == "lichess-logistic"
    assert set(
        [
            "new_game",
            "load_pgn",
            "load_fen",
            "make_move",
            "navigate_ply",
            "get_game_state",
            "export_pgn",
            "engine_poll",
            "set_analysis_options",
            "configure_engine_path",
            "get_engine_status",
        ]
    ).issubset(set(data["methods"]))
    assert data["capabilities"]["drawable_arrows"] is True
    assert data["capabilities"]["engine"] is True
    assert data["capabilities"]["live_evaluation"] is True
    assert data["capabilities"]["best_move_arrows"] is True
    assert data["capabilities"]["game_review"] is True
    assert data["capabilities"]["review_presets"] == ["fast", "standard", "deep"]
    assert {
        "start_game_review",
        "cancel_game_review",
        "get_game_review",
    }.issubset(set(data["methods"]))
    assert data["elo"]["min"] == 400
    assert data["elo"]["max"] == 3000
    assert len(data["elo"]["presets"]) == 7


def test_get_game_state_initial(api: ChessMasterApi) -> None:
    state = assert_ok(api.get_game_state())
    assert state["ply"] == 0
    assert state["fen"].startswith("rnbqkbnr/pppppppp")
    assert state["players"]["white"]["name"] == "White"
    assert state["review"]["status"] == "idle"
    assert state["review"]["available"] is False


def test_review_requires_a_move_and_a_known_preset(api: ChessMasterApi) -> None:
    error = assert_error(api.start_game_review("standard"), "invalid_input")
    assert "at least one move" in error["message"].lower()
    assert_error(api.start_game_review("turbo"), "invalid_input")
    assert_error(api.start_game_review("fast", {"use_book": "yes"}), "invalid_input")


def test_idle_review_can_be_read_and_cancelled(api: ChessMasterApi) -> None:
    data = assert_ok(api.get_game_review())
    assert data["review"]["status"] == "idle"
    assert data["report"] is None

    data = assert_ok(api.cancel_game_review())
    assert data["review"]["status"] == "idle"
    assert data["review"]["reason"] == "user"


def test_new_game_and_move(api: ChessMasterApi) -> None:
    state = assert_ok(api.new_game("pass_play", {"white_name": "A", "black_name": "B"}))
    assert state["players"]["white"]["name"] == "A"

    state = assert_ok(api.make_move("e4"))
    assert state["moves"][0]["san"] == "e4"
    assert state["ply"] == 1

    state = assert_ok(api.navigate_ply(0))
    assert state["ply"] == 0
    assert state["branch_available"] is True


def test_load_pgn_and_export(api: ChessMasterApi) -> None:
    pgn = SAMPLES[0]["text"]
    state = assert_ok(api.load_pgn(pgn))
    assert state["source"] == "pgn"
    assert state["players"]["white"]["name"] == "Paul Morphy"
    assert state["total_plies"] > 10
    assert state["status"]["state"] == "checkmate"
    exported = assert_ok(api.export_pgn())
    assert "Paul Morphy" in exported["pgn"]
    assert "Rd8#" in exported["pgn"]


def test_load_fen_and_promotion_error(api: ChessMasterApi) -> None:
    state = assert_ok(api.load_fen("k7/4P3/8/8/8/8/8/4K3 w - - 0 1"))
    assert state["source"] == "fen"
    error = assert_error(api.make_move("e7e8"), "promotion_required")
    assert error["details"]["options"] == ["q", "r", "b", "n"]

    state = assert_ok(api.make_move("e7e8q"))
    assert state["moves"][0]["san"] == "e8=Q+"


def test_invalid_fen_returns_structured_error(api: ChessMasterApi) -> None:
    error = assert_error(api.load_fen("not a fen"), "invalid_fen")
    assert "six fields" in error["message"].lower()


def test_invalid_pgn_returns_structured_error(api: ChessMasterApi) -> None:
    error = assert_error(api.load_pgn('[Event "x"]\n\n1. e4 e5 2. Bh6 *'), "invalid_pgn")
    assert "illegal" in error["message"].lower()


def test_illegal_move_returns_structured_error(api: ChessMasterApi) -> None:
    error = assert_error(api.make_move("e5"), "illegal_move")
    assert "legal" in error["message"].lower()


def test_invalid_move_type(api: ChessMasterApi) -> None:
    assert_error(api.make_move(None), "invalid_input")  # type: ignore[arg-type]


def test_invalid_navigation(api: ChessMasterApi) -> None:
    assert_error(api.navigate_ply(4), "invalid_ply")


def test_get_samples_are_valid() -> None:
    api = ChessMasterApi()
    data = assert_ok(api.get_samples())
    assert len(data["samples"]) >= 4
    assert {sample["kind"] for sample in data["samples"]} == {"pgn", "fen"}


def test_every_response_is_json_serializable(api: ChessMasterApi) -> None:
    responses = [
        api.get_api_info(),
        api.get_game_state(),
        api.new_game(),
        api.make_move("e4"),
        api.load_fen("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"),
        api.export_pgn(),
        api.reset_game(),
        api.get_samples(),
        api.get_game_review(),
    ]
    for response in responses:
        json.dumps(response)


def test_api_session_is_independent_of_direct_sessions() -> None:
    api = ChessMasterApi()
    direct = GameSession.new_game()
    direct.make_move("e4")
    api_state = assert_ok(api.get_game_state())
    assert api_state["total_plies"] == 0
    assert direct.to_state()["total_plies"] == 1
