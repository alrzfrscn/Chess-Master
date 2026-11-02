"""LAN sharing and multi-client state synchronization over the HTTP bridge."""

from __future__ import annotations

import json
import urllib.request

import pytest

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.web_server import WebAppServer
from chess_master.paths import WEB_ROOT

SEATS = {"both", "white", "black", "spectator"}


@pytest.fixture()
def lan_server(monkeypatch):
    # Deterministic address so URLs can be asserted on offline machines.
    monkeypatch.setattr("chess_master.bridge.api.local_ipv4", lambda: "192.168.1.50")
    api = ChessMasterApi()
    server = WebAppServer(WEB_ROOT, api, port=0)
    api._attach_server(server)
    server.start()
    try:
        yield api, server
    finally:
        server.stop()
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
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def data_of(response: dict) -> dict:
    assert response["ok"] is True, response["error"]
    return response["data"]


def test_lan_info_reports_the_bind_address_and_seat_urls(lan_server) -> None:
    api, server = lan_server
    info = data_of(api.get_lan_info())

    assert info["enabled"] is False
    assert info["host"] == "127.0.0.1"
    assert info["port"] == server.port
    assert info["lan_ip"] == "192.168.1.50"
    assert set(info["seat_urls"]) == SEATS
    assert info["lan_url"] == f"http://192.168.1.50:{server.port}/"
    assert info["seat_urls"]["white"] == f"http://192.168.1.50:{server.port}/?seat=white"


def test_set_lan_mode_rebinds_and_restores_loopback(lan_server) -> None:
    api, server = lan_server
    port = server.port

    enabled = data_of(api.set_lan_mode(True))
    assert enabled["enabled"] is True
    assert enabled["host"] == "0.0.0.0"
    assert enabled["port"] == port
    assert enabled["seat_urls"]["both"] == f"http://192.168.1.50:{port}/?seat=both"

    # The loopback UI keeps working while the server is exposed.
    health = urllib.request.urlopen(f"{server.url}api/health", timeout=10)
    assert health.status == 200

    disabled = data_of(api.set_lan_mode(False))
    assert disabled["enabled"] is False
    assert disabled["host"] == "127.0.0.1"
    assert disabled["port"] == port


def test_set_lan_mode_validates_input_and_requires_a_server() -> None:
    detached = ChessMasterApi()
    error = detached.set_lan_mode(True)["error"]
    assert error["code"] == "invalid_input"
    assert "no web server" in error["message"].lower()

    attached = ChessMasterApi()
    api = attached
    api._attach_server(WebAppServer(WEB_ROOT, api, port=0))
    error = api.set_lan_mode("on")["error"]  # type: ignore[arg-type]
    assert error["code"] == "invalid_input"


def test_http_clients_share_game_id_and_revision(lan_server) -> None:
    api, server = lan_server
    api.set_lan_mode(True)

    created = data_of(post(server, "new_game", "pass_play", {"white_name": "Phone A", "black_name": "Phone B"}))
    game_id = created["game_id"]

    moved = data_of(post(server, "make_move", "e4"))
    assert moved["revision"] > created["revision"]

    polled = data_of(post(server, "engine_poll"))["state"]
    assert polled["game_id"] == game_id
    assert polled["revision"] == moved["revision"]
    assert polled["ply"] == 1

    data_of(post(server, "navigate_ply", 0))
    polled = data_of(post(server, "engine_poll"))["state"]
    assert polled["ply"] == 0
    assert polled["branch_available"] is True
    assert polled["revision"] > moved["revision"]
