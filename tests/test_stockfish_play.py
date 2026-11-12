"""End-to-end Play vs Stockfish flows through the JS-facing API."""

from __future__ import annotations

import time

import chess
import pytest

from chess_master.analysis.winprob import MATE_CP
from chess_master.bridge.api import ChessMasterApi
from chess_master.engine.stockfish import discover_engine


pytestmark = pytest.mark.engine


@pytest.fixture(scope="module", autouse=True)
def require_engine() -> None:
    discovery = discover_engine()
    if not discovery.available:
        pytest.skip("Stockfish binary is not installed")


@pytest.fixture()
def api():
    instance = ChessMasterApi()
    try:
        yield instance
    finally:
        instance._shutdown()


def data(response: dict) -> dict:
    assert response["ok"] is True, response
    return response["data"]


def wait_for_poll(api: ChessMasterApi, predicate, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = data(api.engine_poll())
        if predicate(last):
            return last
        time.sleep(0.03)
    raise AssertionError(f"poll timed out; last state: {last}")


def test_human_white_gets_a_bot_reply(api: ChessMasterApi) -> None:
    state = data(api.new_game("stockfish", {"player_color": "white", "bot_elo": 800, "white_name": "Ada"}))
    assert state["engine"]["available"] is True
    assert state["orientation"] == "white"
    assert state["engine"]["bot_color"] == "black"
    assert state["engine"]["thinking"] is False
    assert state["players"]["black"]["name"] == "Stockfish 800"
    assert state["players"]["white"]["name"] == "Ada"

    after_human = data(api.make_move("e4"))
    assert after_human["total_plies"] == 1
    assert after_human["turn"] == "black"
    assert after_human["engine"]["thinking"] is True

    result = wait_for_poll(api, lambda payload: payload["moved"] is not None)
    assert result["moved"] is not None
    state = result["state"]
    assert state["total_plies"] == 2
    assert state["turn"] == "white"
    assert state["last_move"]["color"] == "black"
    assert state["is_viewing_live"] is True

    board = chess.Board()
    board.push_uci("e2e4")
    assert chess.Move.from_uci(result["moved"]) in board.legal_moves


def test_player_as_black_receives_the_opening_move(api: ChessMasterApi) -> None:
    state = data(api.new_game("stockfish", {"player_color": "black", "bot_elo": 1000}))
    assert state["orientation"] == "black"
    assert state["engine"]["bot_color"] == "white"
    assert state["players"]["white"]["is_engine"] is True

    result = wait_for_poll(api, lambda payload: payload["moved"] is not None)
    state = result["state"]
    assert state["total_plies"] == 1
    assert state["turn"] == "black"
    assert state["orientation"] == "black"
    assert state["last_move"]["color"] == "white"
    assert chess.Move.from_uci(result["moved"]) in chess.Board().legal_moves

    after_human = data(api.make_move("e7e5"))
    assert after_human["total_plies"] == 2
    assert after_human["turn"] == "white"
    result = wait_for_poll(api, lambda payload: payload["moved"] is not None)
    assert result["state"]["total_plies"] == 3
    assert result["state"]["last_move"]["color"] == "white"


def test_navigation_discards_a_stale_bot_move(api: ChessMasterApi) -> None:
    data(api.new_game("stockfish", {"player_color": "white", "bot_elo": 3000}))
    data(api.make_move("e4"))

    state = data(api.navigate_ply(0))
    revision = state["revision"]
    assert state["total_plies"] == 1
    assert state["ply"] == 0
    assert state["engine"]["thinking"] is False

    time.sleep(1.4)
    for _ in range(25):
        poll = data(api.engine_poll())
        current = poll["state"]
        assert current["total_plies"] == 1
        assert current["revision"] == revision
        assert current["evaluation"]["token"]["revision"] == revision
        assert current["evaluation"]["token"]["ply"] == 0
        time.sleep(0.04)

    # Returning to the live tip lets the bot finish the same position.
    data(api.navigate_ply(1))
    result = wait_for_poll(api, lambda payload: payload["moved"] is not None)
    assert result["state"]["total_plies"] == 2
    assert result["state"]["last_move"]["color"] == "black"


def test_live_eval_is_white_pov_cached_and_tagged(api: ChessMasterApi) -> None:
    data(api.set_analysis_options({"enabled": True, "arrows": True, "depth": 10, "multipv": 3}))
    data(api.new_game("pass_play", {"white_name": "Eval", "black_name": "Test"}))
    data(api.make_move("e4"))

    result = wait_for_poll(
        api,
        lambda payload: payload["evaluation"]["updated_at"] is not None
        and payload["state"]["evaluation"]["token"]["revision"] == payload["state"]["revision"],
    )
    state = result["state"]
    evaluation = state["evaluation"]
    assert evaluation["available"] is True
    assert evaluation["token"] == {
        "game_id": state["game_id"],
        "revision": state["revision"],
        "ply": state["ply"],
        "fen": state["fen"],
    }
    assert evaluation["multipv"] == 3
    assert evaluation["depth"] >= 10
    assert len(evaluation["lines"]) == 3
    assert evaluation["best_move"]["from"] in chess.SQUARE_NAMES
    assert evaluation["best_move"]["to"] in chess.SQUARE_NAMES
    assert -MATE_CP < evaluation["score_cp"] < MATE_CP
    assert 0.0 <= evaluation["win_prob_white"] <= 100.0
    assert evaluation["enabled"] is True
    assert evaluation["arrows"] is True
    assert evaluation["bars"] is True

    before = evaluation["updated_at"]
    viewed = data(api.navigate_ply(0))
    assert viewed["revision"] == state["revision"] + 1
    assert viewed["evaluation"]["token"]["revision"] == viewed["revision"]
    polled = wait_for_poll(api, lambda payload: payload["evaluation"]["updated_at"] is not None)
    assert polled["state"]["evaluation"]["token"]["ply"] == 0
    assert polled["state"]["evaluation"]["updated_at"] != before


def test_import_mode_supports_eval_and_arrow(api: ChessMasterApi) -> None:
    data(api.set_analysis_options({"enabled": True, "arrows": True, "depth": 8, "multipv": 3}))
    state = data(api.load_fen("8/8/8/3k4/8/3K4/8/7R w - - 0 1"))
    assert state["mode"] == "import"
    assert state["source"] == "fen"

    result = wait_for_poll(
        api,
        lambda payload: payload["evaluation"]["updated_at"] is not None
        and payload["state"]["evaluation"]["token"]["ply"] == 0,
    )
    evaluation = result["state"]["evaluation"]
    assert evaluation["available"] is True
    assert evaluation["best_move"] is not None
    assert evaluation["arrows"] is True
    assert evaluation["bars"] is True
    assert evaluation["token"]["fen"] == state["fen"]


def test_analysis_toggles_are_reflected_and_clear_hints(api: ChessMasterApi) -> None:
    data(api.set_analysis_options({"enabled": True, "arrows": True, "depth": 8, "multipv": 1}))
    data(api.new_game("pass_play"))
    result = wait_for_poll(api, lambda payload: payload["evaluation"]["updated_at"] is not None)
    assert result["state"]["evaluation"]["best_move"] is not None
    assert result["state"]["evaluation"]["arrows"] is True

    off = data(api.set_analysis_options({"enabled": False, "arrows": False, "depth": 8, "multipv": 1}))
    assert off["options"] == {"enabled": False, "arrows": False, "depth": 8, "multipv": 1}
    poll = data(api.engine_poll())
    assert poll["state"]["engine"]["analysis"]["enabled"] is False
    assert poll["state"]["evaluation"]["enabled"] is False
    assert poll["state"]["evaluation"]["arrows"] is False
    assert poll["state"]["evaluation"]["best_move"] is None


def test_missing_engine_is_graceful_and_configurable(tmp_path) -> None:
    missing = tmp_path / "stockfish-not-here.exe"
    api = ChessMasterApi(engine_path=str(missing))
    try:
        state = data(api.new_game("stockfish", {"player_color": "white", "bot_elo": 1200}))
        assert state["engine"]["available"] is False
        assert state["engine"]["status"] == "missing"
        assert "not found" in state["engine"]["note"].lower()
        assert state["engine"]["configurable"] is True
        assert state["evaluation"]["available"] is False

        poll = data(api.engine_poll())
        assert poll["moved"] is None
        assert poll["state"]["engine"]["thinking"] is False
        for _ in range(10):
            poll = data(api.engine_poll())
            assert poll["moved"] is None
            assert poll["state"]["total_plies"] == 0

        rejected = api.configure_engine_path(str(tmp_path / "nope"))
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "invalid_input"
    finally:
        api._shutdown()


def test_engine_status_reports_cache_and_worker(api: ChessMasterApi) -> None:
    data(api.set_analysis_options({"enabled": True, "arrows": False, "depth": 8}))
    wait_for_poll(api, lambda payload: payload["evaluation"]["updated_at"] is not None)
    status = data(api.get_engine_status())
    assert status["engine"]["available"] is True
    assert status["analysis"]["depth"] == 8
    assert status["cache"]["entries"] >= 1
    assert status["model"]["winprob"] == "lichess-logistic"
    assert status["engine"]["jobs"]["running"] in {None, "analysis", "bot"}


ROOK_FEN = "8/8/8/4k3/8/8/8/4K2R w - - 0 1"
ROOK_FEN_BLACK_TO_MOVE = "8/8/8/4k3/8/8/8/4K2R b - - 0 1"


def test_custom_fen_white_to_move_gets_a_bot_reply(api: ChessMasterApi) -> None:
    state = data(
        api.new_game(
            "stockfish",
            {"start_fen": ROOK_FEN, "player_color": "white", "bot_elo": 800},
        )
    )
    assert state["start_fen"] == ROOK_FEN
    assert state["turn"] == "white"

    data(api.make_move("h1h5"))
    result = wait_for_poll(api, lambda payload: payload["moved"] is not None)
    assert result["state"]["total_plies"] == 2
    assert result["state"]["last_move"]["color"] == "black"
    board = chess.Board(ROOK_FEN)
    board.push_uci("h1h5")
    assert chess.Move.from_uci(result["moved"]) in board.legal_moves


def test_custom_fen_black_to_move_the_bot_moves_first(api: ChessMasterApi) -> None:
    state = data(
        api.new_game(
            "stockfish",
            {"start_fen": ROOK_FEN_BLACK_TO_MOVE, "player_color": "white", "bot_elo": 800},
        )
    )
    assert state["start_fen"] == ROOK_FEN_BLACK_TO_MOVE
    assert state["turn"] == "black"
    assert state["orientation"] == "white"
    assert state["engine"]["bot_color"] == "black"

    result = wait_for_poll(api, lambda payload: payload["moved"] is not None)
    assert result["state"]["total_plies"] == 1
    assert result["state"]["last_move"]["color"] == "black"
    assert (
        chess.Move.from_uci(result["moved"])
        in chess.Board(ROOK_FEN_BLACK_TO_MOVE).legal_moves
    )

