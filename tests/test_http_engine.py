"""The Phase 2 engine surface works over the loopback HTTP transport too."""

from __future__ import annotations

import json
import time
import urllib.request

import pytest

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.web_server import WebAppServer
from chess_master.engine.stockfish import discover_engine
from chess_master.paths import WEB_ROOT


pytestmark = pytest.mark.engine


@pytest.fixture()
def server():
    if not discover_engine().available:
        pytest.skip("Stockfish binary is not installed")
    api = ChessMasterApi()
    app_server = WebAppServer(WEB_ROOT, api, port=0, expose_api=True)
    app_server.start()
    try:
        yield app_server
    finally:
        app_server.stop()
        api._shutdown()


def post(server: WebAppServer, method: str, *args, **kwargs) -> dict:
    body = json.dumps({"args": list(args), "kwargs": kwargs}).encode("utf-8")
    request = urllib.request.Request(
        f"{server.url}api/{method}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert response.status == 200
    return payload


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_health_reports_phase_three_and_engine(server: WebAppServer) -> None:
    info = get_json(f"{server.url}api/health")
    assert info["ok"] is True
    assert info["data"]["phase"] == 3
    assert info["data"]["capabilities"]["engine"] is True
    assert info["data"]["engine"]["available"] is True
    assert info["data"]["elo"]["min"] == 400


def test_http_poll_plays_a_bot_opening_move(server: WebAppServer) -> None:
    created = post(server, "new_game", "stockfish", {"player_color": "black", "bot_elo": 800})
    assert created["ok"] is True
    state = created["data"]
    assert state["orientation"] == "black"
    assert state["engine"]["bot_color"] == "white"

    deadline = time.monotonic() + 20.0
    moved = None
    while time.monotonic() < deadline:
        poll = post(server, "engine_poll")
        assert poll["ok"] is True
        if poll["data"]["moved"]:
            moved = poll["data"]
            break
        time.sleep(0.05)
    assert moved is not None, "HTTP engine_poll never applied the bot move"
    assert moved["state"]["total_plies"] == 1
    assert moved["state"]["last_move"]["color"] == "white"
    assert moved["state"]["turn"] == "black"
    assert moved["state"]["evaluation"]["token"]["revision"] == moved["state"]["revision"]


def test_http_analysis_options_and_status(server: WebAppServer) -> None:
    options = post(server, "set_analysis_options", {"enabled": True, "arrows": True, "depth": 8, "multipv": 3})
    assert options["ok"] is True
    assert options["data"]["options"]["arrows"] is True

    deadline = time.monotonic() + 20.0
    evaluation = None
    while time.monotonic() < deadline:
        poll = post(server, "engine_poll")
        candidate = poll["data"]["evaluation"]
        if candidate.get("updated_at"):
            evaluation = candidate
            break
        time.sleep(0.05)
    assert evaluation is not None
    assert evaluation["multipv"] == 3
    assert evaluation["best_move"] is not None
    assert evaluation["arrows"] is True

    status = post(server, "get_engine_status")
    assert status["ok"] is True
    assert status["data"]["cache"]["entries"] >= 1
    assert status["data"]["engine"]["available"] is True
