"""Online rooms: seating, fair play, endings and per-coordinator engine jobs."""

from __future__ import annotations

import chess
import pytest

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.rooms import RoomManager
from chess_master.engine.stockfish import EngineToken, StockfishManager


def assert_ok(response: dict) -> dict:
    assert response["ok"] is True, response.get("error")
    assert response["error"] is None
    return response["data"]


def assert_error(response: dict, code: str) -> dict:
    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert response["error"]["message"]
    return response["error"]


def make_api(client_id: str, rooms: RoomManager) -> ChessMasterApi:
    return ChessMasterApi(client_id=client_id, rooms=rooms)


def open_room(rooms: RoomManager):
    alice = make_api("alice", rooms)
    bob = make_api("bob", rooms)
    carol = make_api("carol", rooms)
    created = assert_ok(alice.create_room({"time_control": "3+0"}))
    joined = assert_ok(bob.join_room(created["room"]["room_id"]))
    spectated = assert_ok(carol.join_room(created["room"]["room_id"]))
    return alice, bob, carol, created, joined, spectated


def test_create_join_and_spectate() -> None:
    rooms = RoomManager()
    alice, bob, carol, created, joined, spectated = open_room(rooms)

    room = created["room"]
    assert created["mode"] == "online"
    assert len(room["room_id"]) == 8
    assert room["status"] == "waiting"
    assert room["seat"] == "white"
    assert room["fair_play"] is True
    assert created["clocks"]["running"] is False

    assert joined["room"]["status"] == "active"
    assert joined["room"]["seat"] == "black"
    assert joined["clocks"]["running"] is True
    assert spectated["room"]["seat"] is None
    assert spectated["room"]["spectator"] is True

    # Every seat sees the same authoritative table.
    assert assert_ok(alice.get_game_state())["room"]["status"] == "active"
    assert assert_ok(carol.get_game_state())["room"]["room_id"] == room["room_id"]


def test_only_your_seat_may_move() -> None:
    rooms = RoomManager()
    alice, bob, carol, *_ = open_room(rooms)

    assert_ok(alice.make_move("d2d4"))
    assert_error(alice.make_move("d7d5"), "invalid_input")
    assert_error(carol.make_move("d7d5"), "invalid_input")
    assert_ok(bob.make_move("d7d5"))


def test_fair_play_locks_until_the_game_ends() -> None:
    rooms = RoomManager()
    alice, bob, carol, *_ = open_room(rooms)
    assert_ok(alice.make_move("e2e4"))
    assert_ok(bob.make_move("e7e5"))

    state = assert_ok(alice.get_game_state())
    assert state["room"]["fair_play"] is True
    assert state["evaluation"]["enabled"] is False
    assert state["evaluation"]["arrows"] is False
    assert_error(carol.navigate_ply(0), "invalid_input")
    assert_error(alice.start_game_review("fast"), "invalid_input")

    ended = assert_ok(alice.resign_game())
    assert ended["status"]["state"] == "resigned"
    assert ended["status"]["reason"] == "resignation"
    assert ended["status"]["winner"] == "black"
    assert ended["status"]["result"] == "0-1"
    assert ended["room"]["fair_play"] is False
    assert ended["room"]["rematch_available"] is True

    assert_ok(alice.navigate_ply(0))
    review = assert_ok(alice.start_game_review("fast"))
    assert review["review"]["status"] in {"queued", "running", "failed"}
    assert_ok(alice.cancel_game_review())

    archived = assert_ok(alice.list_archived_games())
    record = next(row for row in archived["games"] if row["game_id"] == ended["game_id"])
    assert record["termination_state"] == "resigned"
    assert record["termination_reason"] == "resignation"
    assert record["winner"] == "black"


def test_draw_offer_decline_and_agreement() -> None:
    rooms = RoomManager()
    alice, bob, carol, *_ = open_room(rooms)
    assert_ok(alice.make_move("e2e4"))
    assert_ok(bob.make_move("e7e5"))

    assert_error(bob.offer_or_accept_draw("accept"), "invalid_input")
    assert_error(carol.offer_or_accept_draw("offer"), "invalid_input")

    offered = assert_ok(alice.offer_or_accept_draw("offer"))
    assert offered["status"]["draw_offer_by"] == "white"
    # A move clears the pending offer.
    assert_ok(alice.make_move("g1f3"))
    assert assert_ok(alice.get_game_state())["status"]["draw_offer_by"] is None

    assert_ok(alice.offer_or_accept_draw("offer"))
    declined = assert_ok(bob.offer_or_accept_draw("decline"))
    assert declined["status"]["draw_offer_by"] is None
    assert declined["status"]["state"] == "in_progress"

    assert_ok(alice.offer_or_accept_draw("offer"))
    agreed = assert_ok(bob.offer_or_accept_draw("accept"))
    assert agreed["status"]["state"] == "draw"
    assert agreed["status"]["reason"] == "agreement"
    assert agreed["status"]["result"] == "1/2-1/2"
    assert agreed["room"]["fair_play"] is False

    archived = assert_ok(bob.list_archived_games())
    record = next(row for row in archived["games"] if row["game_id"] == agreed["game_id"])
    assert record["termination_state"] == "draw"
    assert record["termination_reason"] == "agreement"


def test_rematch_swaps_colors_and_resets_the_clocks() -> None:
    rooms = RoomManager()
    alice, bob, carol, created, *_ = open_room(rooms)
    assert_ok(alice.make_move("e2e4"))
    assert_ok(bob.make_move("e7e5"))
    ended = assert_ok(bob.resign_game())
    assert ended["status"]["winner"] == "white"

    assert_error(carol.rematch_room(), "invalid_input")

    rematch = assert_ok(bob.rematch_room())
    assert rematch["room"]["seat"] == "white"  # bob was Black, now plays White
    assert rematch["room"]["status"] == "active"
    assert rematch["room"]["rematch_available"] is False
    assert rematch["game_id"] != ended["game_id"]
    assert rematch["total_plies"] == 0
    assert rematch["status"]["state"] == "in_progress"
    assert rematch["clocks"]["running"] is True

    alice_state = assert_ok(alice.get_game_state())
    assert alice_state["room"]["seat"] == "black"
    assert alice_state["orientation"] == "black"
    assert_error(alice.make_move("d2d4"), "invalid_input")
    assert_ok(bob.make_move("d2d4"))
    assert_ok(alice.make_move("d7d5"))


def test_idle_rooms_expire() -> None:
    now = [1000.0]
    rooms = RoomManager(clock=lambda: now[0], idle_ttl=10.0)
    alice = make_api("alice", rooms)
    code = assert_ok(alice.create_room())["room"]["room_id"]
    now[0] += 11.0
    bob = make_api("bob", rooms)
    assert_error(bob.join_room(code), "invalid_input")


def test_event_fingerprint_tracks_room_changes() -> None:
    from chess_master.bridge.web_server import _state_fingerprint

    rooms = RoomManager()
    alice = make_api("alice", rooms)
    bob = make_api("bob", rooms)
    code = assert_ok(alice.create_room())["room"]["room_id"]
    waiting = _state_fingerprint(alice)
    assert waiting["room_status"] == "waiting"
    assert waiting["seat"] == "white"

    assert_ok(bob.join_room(code))
    active = _state_fingerprint(alice)
    assert active["room_status"] == "active"
    assert active["seats"] == {"white": True, "black": True}
    assert active["seat"] == "white"

    assert_ok(alice.make_move("e2e4"))
    moved = _state_fingerprint(alice)
    assert moved["revision"] != active["revision"]
    assert moved["turn"] == "black"
    assert moved["fair_play"] is True

    assert_ok(alice.offer_or_accept_draw("offer"))
    assert _state_fingerprint(alice)["draw_offer_by"] == "white"
    assert_ok(bob.offer_or_accept_draw("accept"))
    ended = _state_fingerprint(alice)
    assert ended["status"] == "draw"
    assert ended["fair_play"] is False
    assert ended["rematch_available"] is True


def test_web_sessions_share_one_room_registry() -> None:
    from chess_master.bridge.sessions import SessionManager

    sessions = SessionManager(ChessMasterApi())
    try:
        alice = sessions.api_for("session-a", "alice")
        bob = sessions.api_for("session-b", "bob")
        code = assert_ok(alice.create_room())["room"]["room_id"]
        joined = assert_ok(bob.join_room(code))
        assert joined["room"]["room_id"] == code
        assert joined["room"]["seat"] == "black"
        assert assert_ok(alice.get_game_state())["room"]["status"] == "active"
    finally:
        sessions.close()


def test_same_browser_two_tabs_take_opposite_seats() -> None:
    """Two tabs share localStorage's client_id but have different session_ids."""

    from chess_master.bridge.sessions import SessionManager

    sessions = SessionManager(ChessMasterApi())
    try:
        tab_one = sessions.api_for("session_1", "shared-client")
        tab_two = sessions.api_for("session_2", "shared-client")
        code = assert_ok(tab_one.create_room())["room"]["room_id"]

        joined = assert_ok(tab_two.join_room(code))
        assert joined["room"]["seat"] == "black"
        assert joined["room"]["status"] == "active"
        assert joined["room"]["spectator"] is False
        assert assert_ok(tab_one.get_game_state())["room"]["seat"] == "white"

        # Each tab keeps its own seat on reconnect.
        assert assert_ok(sessions.api_for("session_1", "shared-client").get_game_state())[
            "room"
        ]["seat"] == "white"
        assert assert_ok(sessions.api_for("session_2", "shared-client").get_game_state())[
            "room"
        ]["seat"] == "black"
    finally:
        sessions.close()


def test_client_id_reconnect_across_tabs() -> None:
    """A stable client_id still reclaims its seat from a new tab."""

    rooms = RoomManager()
    host = make_api("alice", rooms)
    guest = make_api("bob", rooms)
    code = assert_ok(host.create_room())["room"]["room_id"]
    assert_ok(guest.join_room(code))

    # Same client, new session (browser restart): the client keeps its seat.
    returning = ChessMasterApi(client_id="bob", session_id="fresh-session", rooms=rooms)
    state = assert_ok(returning.join_room(code))
    assert state["room"]["seat"] == "black"
    assert state["room"]["status"] == "active"


def test_stockfish_jobs_are_isolated_per_coordinator(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "stockfish.exe"
    binary.write_bytes(b"")
    manager = StockfishManager(str(binary))
    monkeypatch.setattr(manager, "ensure_started", lambda: False)

    token_a = EngineToken("game-a", 1, 0, chess.STARTING_FEN)
    token_b = EngineToken("game-b", 1, 0, chess.STARTING_FEN)
    assert (
        manager.submit_analysis(
            token_a,
            start_fen=chess.STARTING_FEN,
            moves=[],
            use_cache=False,
            owner="tab-a",
        )
        == "queued"
    )
    assert (
        manager.submit_analysis(
            token_b,
            start_fen=chess.STARTING_FEN,
            moves=[],
            use_cache=False,
            owner="tab-b",
        )
        == "queued"
    )
    with manager._lock:
        assert set(manager._jobs) == {("tab-a", "analysis"), ("tab-b", "analysis")}

    # A new revision in one tab drops only that tab's queued job.
    manager.set_active_token(
        EngineToken("game-a", 2, 1, chess.STARTING_FEN), owner="tab-a"
    )
    with manager._lock:
        assert set(manager._jobs) == {("tab-b", "analysis")}
        assert ("tab-a", "analysis") not in manager._latest_seq
    manager.set_active_token(
        EngineToken("game-b", 2, 1, chess.STARTING_FEN), owner="tab-b"
    )
    with manager._lock:
        assert not manager._jobs
