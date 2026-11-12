"""Curated drill catalog and drill-launch API contract."""

from __future__ import annotations

import chess
import pytest

from chess_master.bridge.api import ChessMasterApi
from chess_master.engine.fen import validate_fen
from chess_master.models.drills import DRILLS, get_drill

REQUIRED_KEYS = {
    "id",
    "title",
    "category",
    "difficulty",
    "player_color",
    "recommended_elo",
    "fen",
    "goal",
    "hint",
}


def assert_ok(response: dict) -> dict:
    assert response["ok"] is True
    assert response["error"] is None
    return response["data"]


@pytest.mark.parametrize("drill", DRILLS, ids=[drill["id"] for drill in DRILLS])
def test_drill_position_is_legal_and_playable(drill: dict) -> None:
    assert REQUIRED_KEYS.issubset(drill)
    assert drill["id"] and drill["title"] and drill["goal"] and drill["hint"]
    assert drill["player_color"] in ("white", "black")
    assert 400 <= drill["recommended_elo"] <= 3000

    board = validate_fen(drill["fen"])
    assert board.is_valid()
    assert board.status() == chess.Status.VALID
    assert not board.is_game_over()
    assert board.turn == (chess.WHITE if drill["player_color"] == "white" else chess.BLACK)
    assert board.legal_moves.count() > 0


def test_catalog_has_eight_unique_drills() -> None:
    assert len(DRILLS) == 8
    assert len({drill["id"] for drill in DRILLS}) == 8
    assert get_drill("queen-mate")["title"].startswith("Queen")


@pytest.fixture
def api() -> ChessMasterApi:
    return ChessMasterApi()


def test_get_drills_lists_the_catalog(api: ChessMasterApi) -> None:
    data = assert_ok(api.get_drills())
    assert data["count"] == 8
    assert [drill["id"] for drill in data["drills"]] == [drill["id"] for drill in DRILLS]


@pytest.mark.parametrize("drill", DRILLS, ids=[drill["id"] for drill in DRILLS])
def test_start_drill_starts_stockfish_from_the_drill(
    api: ChessMasterApi, drill: dict
) -> None:
    state = assert_ok(api.start_drill(drill["id"]))

    assert state["drill"]["id"] == drill["id"]
    assert state["mode"] == "stockfish"
    assert state["start_fen"] == drill["fen"]
    assert state["fen"] == drill["fen"]
    assert state["headers"]["SetUp"] == "1"
    assert state["headers"]["FEN"] == drill["fen"]
    assert state["event"] == f"Drill - {drill['title']}"
    assert state["turn"] == drill["player_color"]
    assert state["engine"]["player_color"] == drill["player_color"]
    assert state["engine"]["bot_color"] != drill["player_color"]
    assert state["engine"]["bot_elo"] == drill["recommended_elo"]
    assert state["status"]["state"] == "in_progress"


def test_start_drill_can_run_as_pass_and_play(api: ChessMasterApi) -> None:
    state = assert_ok(api.start_drill("lucena", {"mode": "pass_play"}))
    assert state["mode"] == "pass_play"
    assert state["start_fen"] == get_drill("lucena")["fen"]
    assert state["players"]["white"]["is_engine"] is False
    assert state["players"]["black"]["is_engine"] is False


def test_start_drill_rejects_unknown_ids(api: ChessMasterApi) -> None:
    response = api.start_drill("no-such-drill")
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_input"
