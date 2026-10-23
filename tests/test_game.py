"""Game session, navigation, branching and move notation tests."""

from __future__ import annotations

import chess
import pytest

from chess_master.engine.errors import (
    GameOverError,
    IllegalMoveError,
    InvalidInputError,
    InvalidModeError,
    InvalidPlyError,
    PromotionRequiredError,
)
from chess_master.engine.game import GameSession


def test_new_game_state() -> None:
    session = GameSession.new_game("pass_play", {"white_name": "Alice", "black_name": "Bob"})
    state = session.to_state()

    assert state["schema_version"] == 1
    assert state["phase"] == 4
    assert state["mode"] == "pass_play"
    assert state["mode_label"] == "Pass & Play"
    assert state["players"]["white"]["name"] == "Alice"
    assert state["players"]["black"]["name"] == "Bob"
    assert state["fen"] == chess.STARTING_FEN
    assert state["ply"] == 0
    assert state["total_plies"] == 0
    assert state["turn"] == "white"
    assert state["status"]["state"] == "in_progress"
    assert state["is_viewing_live"] is True
    assert state["branch_available"] is False
    assert state["engine"]["available"] is False
    assert state["engine"]["enabled"] is False
    assert state["evaluation"]["best_move"] is None


def test_stockfish_mode_carries_engine_configuration() -> None:
    session = GameSession.new_game("stockfish", {"player_color": "white", "bot_elo": 1800})
    state = session.to_state()
    assert state["mode"] == "stockfish"
    assert state["players"]["black"]["name"] == "Stockfish 1800"
    assert state["players"]["black"]["is_engine"] is True
    assert state["players"]["black"]["rating"] == 1800
    assert state["engine"]["enabled"] is True
    assert state["engine"]["status"] == "unmanaged"
    assert state["engine"]["player_color"] == "white"
    assert state["engine"]["bot_color"] == "black"
    assert state["engine"]["bot_elo"] == 1800
    assert state["engine"]["bot"]["mode"] == "limit_strength"
    assert state["engine"]["bot"]["uci_elo"] == 1800
    assert state["orientation"] == "white"


def test_stockfish_player_can_choose_black() -> None:
    session = GameSession.new_game("stockfish", {"player_color": "black", "bot_elo": 2200})
    state = session.to_state()
    assert state["orientation"] == "black"
    assert state["players"]["white"]["is_engine"] is True
    assert state["players"]["white"]["name"] == "Stockfish 2200"
    assert state["engine"]["bot_color"] == "white"
    assert state["mode_label"] == "Play vs Stockfish"
    assert state["event"] == "Chess Master - Play vs Stockfish"


def test_stockfish_human_name_tracks_the_resolved_color() -> None:
    white = GameSession.new_game(
        "stockfish", {"player_color": "white", "bot_elo": 1200, "human_name": "Ada"}
    ).to_state()
    assert white["players"]["white"]["name"] == "Ada"
    assert white["players"]["black"]["name"] == "Stockfish 1200"

    black = GameSession.new_game(
        "stockfish", {"player_color": "black", "bot_elo": 1200, "human_name": "Grace"}
    ).to_state()
    assert black["players"]["black"]["name"] == "Grace"
    assert black["players"]["white"]["name"] == "Stockfish 1200"


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(InvalidModeError):
        GameSession.new_game("teleport")


def test_make_move_accepts_san_and_uci() -> None:
    session = GameSession.new_game()
    state = session.make_move("e4")
    assert state["total_plies"] == 1
    assert state["moves"][0]["san"] == "e4"
    assert state["moves"][0]["uci"] == "e2e4"
    assert state["turn"] == "black"

    state = session.make_move("g8f6")
    assert state["total_plies"] == 2
    assert state["moves"][1]["san"] == "Nf6"
    assert state["last_move"]["uci"] == "g8f6"


def test_illegal_move_is_rejected_without_mutating_game() -> None:
    session = GameSession.new_game()
    with pytest.raises(IllegalMoveError):
        session.make_move("e5")
    assert session.total_plies == 0
    assert session.board.fen() == chess.STARTING_FEN


def test_non_string_move_is_rejected() -> None:
    session = GameSession.new_game()
    with pytest.raises(InvalidInputError):
        session.make_move(42)  # type: ignore[arg-type]


def test_navigation_returns_historical_position_without_mutating() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")

    state = session.navigate_ply(1)
    assert state["ply"] == 1
    assert state["total_plies"] == 3
    assert state["fen"] == session._moves[0].fen_after  # noqa: SLF001
    assert state["turn"] == "black"
    assert state["branch_available"] is True
    assert state["can_prev"] is True
    assert state["can_next"] is True
    assert state["last_move"]["san"] == "e4"

    live = session.navigate_ply(3)
    assert live["ply"] == 3
    assert live["is_viewing_live"] is True


@pytest.mark.parametrize("ply", [-1, 4, 1.5, True, "x"])
def test_invalid_ply_is_rejected(ply: object) -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")
    with pytest.raises(InvalidPlyError):
        session.navigate_ply(ply)


def test_making_a_move_from_history_creates_a_branch() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")
    session.navigate_ply(1)

    state = session.make_move("c5")
    assert state["total_plies"] == 2
    assert [move["san"] for move in state["moves"]] == ["e4", "c5"]
    assert state["ply"] == 2
    assert state["branch_available"] is False
    assert state["is_viewing_live"] is True


def test_branching_updates_move_numbers_and_last_move() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.navigate_ply(1)
    state = session.make_move("c5")
    assert state["moves"][1]["move_number"] == 1
    assert state["moves"][1]["color"] == "black"
    assert state["last_move"]["san"] == "c5"


def test_promotion_requires_a_choice_for_uci() -> None:
    session = GameSession.from_fen("k7/4P3/8/8/8/8/8/4K3 w - - 0 1")
    with pytest.raises(PromotionRequiredError) as error:
        session.make_move("e7e8")
    assert error.value.details["options"] == ["q", "r", "b", "n"]

    state = session.make_move("e7e8q")
    assert state["moves"][0]["promotion"] == "q"
    assert state["moves"][0]["san"] == "e8=Q+"


def test_promotion_by_san() -> None:
    session = GameSession.from_fen("k7/4P3/8/8/8/8/8/4K3 w - - 0 1")
    state = session.make_move("e8=N")
    assert state["moves"][0]["promotion"] == "n"
    assert state["moves"][0]["san"] == "e8=N"


def test_checkmate_is_reported_and_blocks_further_moves() -> None:
    session = GameSession.new_game()
    session.make_move("f3")
    session.make_move("e5")
    session.make_move("g4")
    state = session.make_move("Qh4#")

    assert state["status"]["state"] == "checkmate"
    assert state["status"]["winner"] == "black"
    assert state["status"]["result"] == "0-1"
    assert state["status"]["check"] is True
    assert state["status"]["check_square"] == "e1"

    with pytest.raises(GameOverError):
        session.make_move("Ke2")


def test_stalemate_is_reported() -> None:
    # Black king a8, White king c7, White queen b6: Black to move and stalemated.
    session = GameSession.from_fen("k7/2K5/1Q6/8/8/8/8/8 b - - 0 1")
    state = session.to_state()
    assert state["status"]["state"] == "draw"
    assert state["status"]["reason"] == "stalemate"
    assert state["status"]["result"] == "1/2-1/2"


def test_reset_returns_to_the_root_position() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    state = session.reset()
    assert state["ply"] == 0
    assert state["total_plies"] == 0
    assert state["moves"] == []
    assert state["fen"] == chess.STARTING_FEN
    assert state["status"]["result"] == "*"


def test_reset_of_fen_position_keeps_custom_root() -> None:
    session = GameSession.from_fen("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    session.make_move("Qg7#")
    state = session.reset()
    assert state["fen"] == "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"
    assert state["total_plies"] == 0


def test_export_pgn_contains_headers_and_moves() -> None:
    session = GameSession.new_game("pass_play", {"white_name": "Alice", "black_name": "Bob"})
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")
    exported = session.export_pgn()
    assert '[White "Alice"]' in exported["pgn"]
    assert '[Black "Bob"]' in exported["pgn"]
    assert "1. e4 e5 2. Nf3" in exported["pgn"]
    assert exported["total_plies"] == 3


def test_state_is_json_serializable() -> None:
    import json

    session = GameSession.new_game()
    session.make_move("e4")
    payload = json.dumps(session.to_state())
    assert '"schema_version": 1' in payload
    assert '"uci": "e2e4"' in payload


def test_random_color_resolves_to_a_real_side() -> None:
    session = GameSession.new_game("stockfish", {"player_color": "random", "bot_elo": 1000})
    state = session.to_state()
    assert state["orientation"] in {"white", "black"}
    assert state["engine"]["player_color"] == state["orientation"]
    assert {state["engine"]["player_color"], state["engine"]["bot_color"]} == {"white", "black"}


@pytest.mark.parametrize("elo", [399, 3001, 1200.5, True, "strong"])
def test_invalid_bot_elo_is_rejected(elo: object) -> None:
    with pytest.raises(InvalidInputError):
        GameSession.new_game("stockfish", {"bot_elo": elo})


def test_invalid_player_color_is_rejected() -> None:
    with pytest.raises(InvalidInputError):
        GameSession.new_game("stockfish", {"player_color": "green"})


def test_reset_keeps_stockfish_configuration() -> None:
    session = GameSession.new_game("stockfish", {"player_color": "black", "bot_elo": 2200})
    session.make_move("e4")
    state = session.reset()
    assert state["total_plies"] == 0
    assert state["engine"]["bot_elo"] == 2200
    assert state["engine"]["player_color"] == "black"
    assert state["orientation"] == "black"


def test_move_ucis_preserves_full_history() -> None:
    session = GameSession.new_game()
    session.make_move("e4")
    session.make_move("e5")
    session.make_move("Nf3")
    assert session.move_ucis() == ["e2e4", "e7e5", "g1f3"]
    assert session.move_ucis(2) == ["e2e4", "e7e5"]
