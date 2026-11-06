"""Professional clock: presets, increment settlement and time forfeit."""

from __future__ import annotations

import time

import pytest

from chess_master.engine.clock import GameClock, resolve_time_control
from chess_master.engine.errors import GameOverError, InvalidInputError
from chess_master.engine.game import GameSession


@pytest.mark.parametrize(
    ("preset", "initial", "increment", "category"),
    [
        ("1+0", 60_000, 0, "bullet"),
        ("2+1", 120_000, 1_000, "bullet"),
        ("3+0", 180_000, 0, "blitz"),
        ("3+2", 180_000, 2_000, "blitz"),
        ("5+0", 300_000, 0, "blitz"),
        ("10+0", 600_000, 0, "rapid"),
        ("15+10", 900_000, 10_000, "rapid"),
    ],
)
def test_time_control_presets(preset, initial, increment, category) -> None:
    control = resolve_time_control(preset)
    assert control is not None
    assert (control.initial_ms, control.increment_ms, control.category) == (
        initial,
        increment,
        category,
    )
    assert control.label == preset


def test_unlimited_and_custom_controls() -> None:
    assert resolve_time_control(None) is None
    assert resolve_time_control("unlimited") is None
    assert resolve_time_control("UNLIMITED") is None

    control = resolve_time_control(
        {"initial_ms": 90_000, "increment_ms": 3_000, "label": "Practice", "category": "blitz"}
    )
    assert control is not None
    assert control.to_dict() == {
        "initial_ms": 90_000,
        "increment_ms": 3_000,
        "label": "Practice",
        "category": "blitz",
    }

    derived = resolve_time_control({"initial_ms": 600_000})
    assert derived is not None
    assert derived.category == "rapid"
    assert derived.label == "600+0"
    assert derived.increment_ms == 0


def test_invalid_controls_are_rejected() -> None:
    with pytest.raises(InvalidInputError):
        resolve_time_control("0+0")
    with pytest.raises(InvalidInputError):
        resolve_time_control({"initial_ms": 0})
    with pytest.raises(InvalidInputError):
        resolve_time_control({"initial_ms": 60_000, "increment_ms": -1})
    with pytest.raises(InvalidInputError):
        resolve_time_control({"initial_ms": 60_000, "category": "speedrun"})
    with pytest.raises(InvalidInputError):
        resolve_time_control(42)


def test_clock_tick_and_increment_after_a_move() -> None:
    control = resolve_time_control("3+2")
    assert control is not None
    clock = GameClock(control, last_tick_monotonic=0.0)

    clock.tick(now=0.25)
    assert clock.white_ms == 179_750
    clock.apply_move("white", now=0.50)
    assert clock.white_ms == 181_500  # 179_500 remaining + 2 000 increment
    assert clock.black_ms == 180_000
    assert clock.active_color == "black"

    clock.apply_move("black", now=0.75)
    assert clock.black_ms == 181_750
    assert clock.active_color == "white"


def test_no_increment_when_the_flag_fell() -> None:
    control = resolve_time_control({"initial_ms": 100, "increment_ms": 2_000})
    assert control is not None
    clock = GameClock(control, last_tick_monotonic=0.0)
    clock.apply_move("white", now=0.2)
    assert clock.white_ms == 0
    assert clock.flagged_color() == "white"


def test_game_session_starts_white_clock_on_first_turn() -> None:
    session = GameSession.new_game("pass_play", {"time_control": "3+2"})
    clocks = session.to_state()["clocks"]
    assert clocks["enabled"] is True
    assert clocks["control"] == "3+2"
    assert clocks["white_ms"] == 180_000
    assert clocks["black_ms"] == 180_000
    assert clocks["active_color"] == "white"
    assert clocks["running"] is True


def test_unlimited_games_have_no_clock() -> None:
    session = GameSession.new_game("pass_play")
    clocks = session.to_state()["clocks"]
    assert clocks["enabled"] is False
    assert clocks["label"] == "Unlimited"
    assert clocks["white_ms"] is None
    assert clocks["running"] is False


def test_make_move_adds_increment_and_switches_active_side() -> None:
    session = GameSession.new_game("pass_play", {"time_control": "5+0"})
    state = session.make_move("e4")
    assert state["clocks"]["active_color"] == "black"
    assert state["clocks"]["white_ms"] == 300_000
    assert state["clocks"]["black_ms"] == 300_000


def test_time_forfeit_is_terminal_and_locks_the_board() -> None:
    session = GameSession.new_game(
        "pass_play",
        {"time_control": {"initial_ms": 20, "increment_ms": 0}},
    )
    time.sleep(0.05)

    state = session.to_state()
    assert state["status"] == {
        "state": "timeout",
        "reason": "time_forfeit",
        "winner": "black",
        "result": "0-1",
        "check": False,
        "check_square": None,
        "claimable_draw": None,
    }
    assert state["clocks"]["flagged"] is True
    assert state["clocks"]["running"] is False
    assert state["headers"]["Result"] == "0-1"

    with pytest.raises(GameOverError):
        session.make_move("e4")


def test_black_can_flag_after_white_moves() -> None:
    session = GameSession.new_game(
        "pass_play",
        {"time_control": {"initial_ms": 40, "increment_ms": 0}},
    )
    session.make_move("e4")
    assert session.to_state()["status"]["state"] == "in_progress"
    time.sleep(0.08)

    state = session.to_state()
    assert state["status"]["state"] == "timeout"
    assert state["status"]["winner"] == "white"
    assert state["status"]["result"] == "1-0"
    assert state["clocks"]["white_ms"] > 0
    assert state["clocks"]["black_ms"] == 0


def test_reset_restores_the_clock() -> None:
    session = GameSession.new_game("pass_play", {"time_control": "1+0"})
    session.make_move("e4")
    session.reset()
    state = session.to_state()
    assert state["status"]["state"] == "in_progress"
    assert state["clocks"]["white_ms"] == 60_000
    assert state["clocks"]["black_ms"] == 60_000
    assert state["clocks"]["active_color"] == "white"


def test_archive_round_trip_keeps_the_result() -> None:
    session = GameSession.new_game("pass_play", {"time_control": "3+0", "white_name": "A", "black_name": "B"})
    session.make_move("f3")
    session.make_move("e5")
    session.make_move("g4")
    session.make_move("Qh4#")
    record = session.to_archive()
    assert record["termination_state"] == "checkmate"
    assert record["result"] == "0-1"

    restored = GameSession.from_archive(record)
    assert restored.total_plies == 4
    assert restored.to_state()["status"]["state"] == "checkmate"
    assert restored.to_state()["players"]["white"]["name"] == "A"
