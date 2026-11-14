"""Stage 2: concurrent web sessions, archive scoping and the event stream."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.sessions import SessionManager, is_valid_session_id
from chess_master.bridge.web_server import WebAppServer
from chess_master.engine.stockfish import discover_engine
from chess_master.paths import WEB_ROOT


def post(
    server: WebAppServer,
    method: str,
    *args,
    session: str | None = None,
    client: str | None = None,
    **kwargs,
) -> dict:
    body = json.dumps({"args": list(args), "kwargs": kwargs}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if session:
        headers["X-Chess-Session"] = session
    if client:
        headers["X-Chess-Client"] = client
    request = urllib.request.Request(
        f"{server.url}api/{method}", data=body, method="POST", headers=headers
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert response.status == 200
    return payload


# -- SessionManager -------------------------------------------------------


def test_session_manager_isolates_boards_and_reuses_facades(tmp_path) -> None:
    manager = SessionManager(db_path=tmp_path / "sessions.db")
    try:
        session_a = manager.api_for("session-a", "client-a")
        session_b = manager.api_for("session-b", "client-b")
        assert session_a is not session_b
        assert session_a is manager.api_for("session-a", "client-a")

        assert session_a.new_game("pass_play")["ok"] is True
        assert session_b.new_game("pass_play")["ok"] is True
        assert session_a.make_move("e4")["ok"] is True
        assert session_b.make_move("d4")["ok"] is True

        state_a = session_a.get_game_state()["data"]
        state_b = session_b.get_game_state()["data"]
        assert state_a["game_id"] != state_b["game_id"]
        assert [move["san"] for move in state_a["moves"]] == ["e4"]
        assert [move["san"] for move in state_b["moves"]] == ["d4"]
        assert state_a["total_plies"] == state_b["total_plies"] == 1
        assert session_a.client_id == "client-a"
        assert session_b.client_id == "client-b"
        # A reload may arrive from the same tab; its identity follows the header.
        manager.api_for("session-a")
        assert session_a.client_id == "client-a"
        manager.api_for("session-a", "client-c")
        assert session_a.client_id == "client-c"
    finally:
        manager.close()


def test_session_manager_validates_and_evicts() -> None:
    now = [0.0]
    manager = SessionManager(
        db_path=":memory:",
        max_sessions=2,
        idle_ttl=10.0,
        clock=lambda: now[0],
    )
    try:
        assert is_valid_session_id("a" * 64) is True
        assert is_valid_session_id("") is False
        assert is_valid_session_id("bad id!") is False
        assert is_valid_session_id("a" * 65) is False
        assert is_valid_session_id(None) is False
        with pytest.raises(ValueError):
            manager.api_for("not valid")

        manager.api_for("first")
        manager.api_for("second")
        assert manager.active_sessions == 2
        manager.api_for("third")  # capacity: the least recently used goes
        assert manager.active_sessions == 2

        now[0] += 11.0
        manager.api_for("fourth")  # TTL: every idle session expires
        assert manager.active_sessions == 1
    finally:
        manager.close()


# -- HTTP session routing ------------------------------------------------


def test_http_session_headers_isolate_boards(tmp_path) -> None:
    api = ChessMasterApi(db_path=tmp_path / "web.db")
    server = WebAppServer(WEB_ROOT, api, port=0, expose_api=True)
    server.start()
    try:
        assert post(server, "new_game", "pass_play", session="tab-a", client="client-a")["ok"] is True
        assert post(server, "new_game", "pass_play", session="tab-b", client="client-b")["ok"] is True
        assert post(server, "make_move", "e4", session="tab-a")["ok"] is True
        assert post(server, "make_move", "d4", session="tab-b")["ok"] is True

        state_a = post(server, "get_game_state", session="tab-a")["data"]
        state_b = post(server, "get_game_state", session="tab-b")["data"]
        assert [move["san"] for move in state_a["moves"]] == ["e4"]
        assert [move["san"] for move in state_b["moves"]] == ["d4"]

        # No header keeps the original single-session facade (desktop + old tests).
        legacy = post(server, "get_game_state")["data"]
        assert legacy["total_plies"] == 0
        assert legacy["game_id"] != state_a["game_id"]
    finally:
        server.stop()
        api._shutdown()


def test_invalid_session_header_is_rejected(tmp_path) -> None:
    api = ChessMasterApi(db_path=tmp_path / "web.db")
    server = WebAppServer(WEB_ROOT, api, port=0, expose_api=True)
    server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            post(server, "get_game_state", session="bad session!")
        assert excinfo.value.code == 400
        assert json.loads(excinfo.value.read().decode("utf-8"))["error"]["code"] == "invalid_session"
    finally:
        server.stop()
        api._shutdown()


# -- Archive scoping and share links -------------------------------------


def _saved_game(api: ChessMasterApi, white: str, first: str) -> str:
    assert api.new_game("pass_play", {"white_name": white, "black_name": "Rival"})["ok"] is True
    assert api.make_move(first)["ok"] is True
    assert api.make_move("e5")["ok"] is True
    saved = api.save_current_game()["data"]
    return saved["game"]["game_id"]


def test_client_archive_scoping_and_shared_game_links(tmp_path) -> None:
    manager = SessionManager(db_path=tmp_path / "scope.db")
    try:
        session_a = manager.api_for("session-a", "client-a")
        session_b = manager.api_for("session-b", "client-b")

        game_a = _saved_game(session_a, "Alpha", "e4")
        game_b = _saved_game(session_b, "Bravo", "d4")

        games_a = session_a.list_archived_games()["data"]
        games_b = session_b.list_archived_games()["data"]
        assert games_a["total"] == 1
        assert games_b["total"] == 1
        assert games_a["games"][0]["game_id"] == game_a
        assert games_b["games"][0]["game_id"] == game_b
        assert games_a["stats"]["total"] == 1
        assert games_a["trend"] == []

        # A share link resolves by exact game id, across clients.
        shared = session_b.load_archived_game(game_a)["data"]
        assert shared["game_id"] == game_a
        assert shared["players"]["white"]["name"] == "Alpha"

        # Re-saving a shared game does not move it into the visitor's archive.
        assert session_b.save_current_game()["data"]["saved"] is True
        assert session_b.list_archived_games()["data"]["total"] == 1
        assert session_a.list_archived_games()["data"]["total"] == 1

        # Clearing one client's archive never touches the other client's games.
        cleared = session_a.clear_game_archive()["data"]
        assert cleared["deleted"] == 1
        assert session_a.list_archived_games()["data"]["total"] == 0
        assert session_b.list_archived_games()["data"]["total"] == 1
    finally:
        manager.close()


def test_http_archive_scoping_and_share_link(tmp_path) -> None:
    api = ChessMasterApi(db_path=tmp_path / "scope-web.db")
    server = WebAppServer(WEB_ROOT, api, port=0, expose_api=True)
    server.start()
    try:
        game_a = _saved_game_via_http(server, "tab-a", "client-a", "Alpha", "e4")
        game_b = _saved_game_via_http(server, "tab-b", "client-b", "Bravo", "d4")

        assert post(server, "list_archived_games", session="tab-a", client="client-a")["data"]["total"] == 1
        assert post(server, "list_archived_games", session="tab-b", client="client-b")["data"]["total"] == 1

        shared = post(server, "load_archived_game", game_a, session="tab-b", client="client-b")
        assert shared["ok"] is True
        assert shared["data"]["game_id"] == game_a

        cleared = post(server, "clear_game_archive", session="tab-a", client="client-a")["data"]
        assert cleared["deleted"] == 1
        assert post(server, "list_archived_games", session="tab-b", client="client-b")["data"]["total"] == 1
        assert game_b != game_a
    finally:
        server.stop()
        api._shutdown()


def _saved_game_via_http(
    server: WebAppServer, session: str, client: str, white: str, first: str
) -> str:
    post(
        server,
        "new_game",
        "pass_play",
        {"white_name": white, "black_name": "Rival"},
        session=session,
        client=client,
    )
    post(server, "make_move", first, session=session)
    post(server, "make_move", "e5", session=session)
    saved = post(server, "save_current_game", session=session, client=client)
    assert saved["data"]["saved"] is True
    return saved["data"]["game"]["game_id"]


# -- Real-time event stream ----------------------------------------------


def _read_event(response) -> tuple[str | None, dict]:
    event = None
    while True:
        raw = response.readline()
        if not raw:
            raise AssertionError("The event stream closed unexpectedly.")
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            return event, json.loads(line[len("data: "):])


def test_event_stream_pushes_state_changes(tmp_path) -> None:
    api = ChessMasterApi(db_path=tmp_path / "events.db")
    server = WebAppServer(WEB_ROOT, api, port=0, expose_api=True)
    server.start()
    response = None
    try:
        # Open the session first so its game exists before the stream starts.
        assert post(server, "get_game_state", session="stream-a")["ok"] is True
        response = urllib.request.urlopen(
            f"{server.url}api/events?session=stream-a", timeout=20
        )
        event, snapshot = _read_event(response)
        assert event == "state"
        assert snapshot["ok"] is True
        assert snapshot["total_plies"] == 0

        def play() -> None:
            time.sleep(0.4)
            post(server, "make_move", "e4", session="stream-a")

        worker = threading.Thread(target=play, daemon=True)
        worker.start()

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            _, update = _read_event(response)
            if update.get("total_plies") == 1:
                assert update["game_id"] == snapshot["game_id"]
                break
        else:  # pragma: no cover - timeout
            raise AssertionError("No state event arrived for the played move.")
        worker.join(timeout=5.0)
    finally:
        if response is not None:
            response.close()
        server.stop()
        api._shutdown()


# -- Shared Stockfish worker ---------------------------------------------


@pytest.mark.engine
def test_two_sessions_share_one_stockfish_worker(tmp_path) -> None:
    if not discover_engine().available:
        pytest.skip("Stockfish binary is not installed")
    manager = SessionManager(db_path=tmp_path / "engine.db")
    try:
        session_a = manager.api_for("engine-a", "client-a")
        session_b = manager.api_for("engine-b", "client-b")
        for session in (session_a, session_b):
            started = session.new_game(
                "stockfish",
                {"player_color": "black", "bot_elo": 600, "bot_pace": "instant"},
            )
            assert started["ok"] is True
            assert started["data"]["engine"]["bot_color"] == "white"

        moved = {"a": False, "b": False}
        deadline = time.monotonic() + 40.0
        while time.monotonic() < deadline and not all(moved.values()):
            for key, session in (("a", session_a), ("b", session_b)):
                if moved[key]:
                    continue
                poll = session.engine_poll()["data"]
                if poll["moved"]:
                    moved[key] = True
                    assert poll["state"]["last_move"]["color"] == "white"
                    assert poll["state"]["total_plies"] == 1
            time.sleep(0.02)
        assert all(moved.values()), "both sessions must receive their own bot move"

        state_a = session_a.get_game_state()["data"]
        state_b = session_b.get_game_state()["data"]
        assert state_a["game_id"] != state_b["game_id"]
        assert session_a.make_move("e5")["ok"] is True
        assert session_a.get_game_state()["data"]["total_plies"] == 2
        assert session_b.get_game_state()["data"]["total_plies"] == 1
    finally:
        manager.close()
