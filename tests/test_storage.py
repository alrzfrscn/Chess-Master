"""SQLite archive: auto-save, review reports, restore, export and reset."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from chess_master.analysis.review import line_signature
from chess_master.bridge.api import ChessMasterApi
from chess_master.models.samples import SAMPLES


@pytest.fixture
def api(tmp_path: Path) -> ChessMasterApi:
    return ChessMasterApi(db_path=tmp_path / "archive.db")


def _fools_mate(api: ChessMasterApi, white: str = "White", black: str = "Black") -> dict:
    api.new_game("pass_play", {"white_name": white, "black_name": black})
    for move in ("f3", "e5", "g4", "Qh4#"):
        api.make_move(move)
    return api.get_game_state()["data"]


def _report(game: dict, *, preset: str = "standard") -> dict:
    signature = line_signature(
        game["game_id"], game["start_fen"], [move["uci"] for move in game["moves"]]
    )
    return {
        "available": True,
        "schema_version": 2,
        "phase": 4,
        "status": "complete",
        "game_id": game["game_id"],
        "signature": signature,
        "preset": preset,
        "preset_label": "Standard",
        "engine": {"name": "Stockfish", "depth": 14, "confirm_depth": 16},
        "players": {"white": "White", "black": "Black"},
        "result": "0-1",
        "opening": {"eco": "C20", "name": "King's Pawn Game"},
        "totals": {"plies": len(game["moves"]), "counts": {"brilliant": 1, "blunder": 2}},
        "summary": {
            "accuracy": {"white": 72.5, "black": 88.25},
            "acpl": {"white": 45.0, "black": 18.5},
            "acpl_trimmed": {"white": 40.0, "black": 15.0},
            "estimated_elo": {"white": 1100, "black": 1450},
            "counts": {"brilliant": 1, "blunder": 2},
        },
        "progress": {"done": 4, "total": 4, "phase": "complete", "percent": 100.0},
        "timing": {"started_at": "2026-11-04T10:00:00Z", "finished_at": "2026-11-04T10:00:05Z"},
    }


def test_terminal_game_is_auto_saved(api: ChessMasterApi) -> None:
    game = _fools_mate(api, "Morphy", "Anderssen")

    archive = api.list_archived_games()["data"]
    assert archive["total"] == 1
    row = archive["games"][0]
    assert row["game_id"] == game["game_id"]
    assert row["termination_state"] == "checkmate"
    assert row["termination_reason"] == "checkmate"
    assert row["winner"] == "black"
    assert row["result"] == "0-1"
    assert row["total_plies"] == 4
    assert row["white_name"] == "Morphy"
    assert "Qh4#" in row["pgn"]
    assert archive["stats"]["black_wins"] == 1
    assert archive["db_info"]["path"].endswith("archive.db")
    assert archive["db_info"]["size_bytes"] > 0


def test_pgn_import_is_auto_saved(api: ChessMasterApi) -> None:
    state = api.load_pgn(SAMPLES[0]["text"])["data"]
    archive = api.list_archived_games()["data"]
    assert archive["total"] == 1
    row = archive["games"][0]
    assert row["game_id"] == state["game_id"]
    assert row["source"] == "pgn"
    assert row["total_plies"] == state["total_plies"]
    assert "Paul Morphy" in row["pgn"]


def test_save_current_game_is_idempotent(api: ChessMasterApi) -> None:
    api.new_game("pass_play", {"white_name": "Solo", "black_name": "Rival"})
    api.make_move("e4")
    api.make_move("e5")
    assert api.save_current_game()["data"]["saved"] is True
    assert api.save_current_game()["data"]["saved"] is True

    archive = api.list_archived_games()["data"]
    assert archive["total"] == 1
    assert archive["games"][0]["total_plies"] == 2


def test_list_filters_search_and_paging(api: ChessMasterApi) -> None:
    _fools_mate(api, "Alpha", "Beta")
    api.new_game("pass_play", {"white_name": "Gamma", "black_name": "Delta"})
    api.make_move("e4")
    api.make_move("e5")
    api.save_current_game()

    archive = api.list_archived_games()["data"]
    assert archive["total"] == 2

    first_page = api.list_archived_games(limit=1, offset=0)["data"]
    assert len(first_page["games"]) == 1
    second_page = api.list_archived_games(limit=1, offset=1)["data"]
    assert second_page["games"][0]["game_id"] != first_page["games"][0]["game_id"]

    searched = api.list_archived_games(query="Gamma")["data"]
    assert searched["total"] == 1
    assert searched["games"][0]["white_name"] == "Gamma"

    filtered = api.list_archived_games(mode="import")["data"]
    assert filtered["total"] == 0


def test_review_report_persistence_and_restore(api: ChessMasterApi) -> None:
    game = _fools_mate(api)
    report = _report(game)

    # Simulate a completed Game Review arriving from the coordinator.
    api._engine.restore_review(report, signature=report["signature"], preset="standard")
    payload = api.get_game_review()["data"]
    assert payload["review"]["status"] == "complete"
    assert payload["report"]["summary"]["accuracy"]["white"] == 72.5

    archive = api.list_archived_games()["data"]
    row = archive["games"][0]
    assert row["review_signature"] == report["signature"]
    assert row["review_preset"] == "standard"
    assert row["white_accuracy"] == 72.5
    assert row["black_accuracy"] == 88.25
    assert row["white_acpl"] == 45.0
    assert row["black_acpl"] == 18.5
    assert row["white_estimated_elo"] == 1100
    assert row["black_estimated_elo"] == 1450
    assert row["brilliant_count"] == 1
    assert row["blunder_count"] == 2
    assert row["opening_eco"] == "C20"
    assert row["opening_name"] == "King's Pawn Game"
    assert archive["stats"]["reviewed"] == 1

    # Loading the row resets the coordinator, then restores the stored report.
    loaded = api.load_archived_game(game["game_id"])["data"]
    assert loaded["restored_review"] is True
    assert loaded["game_id"] == game["game_id"]
    assert loaded["total_plies"] == 4
    assert loaded["status"]["state"] == "checkmate"

    restored = api.get_game_review()["data"]
    assert restored["review"]["status"] == "complete"
    assert restored["report"]["signature"] == report["signature"]
    assert restored["report"]["summary"]["accuracy"]["black"] == 88.25
    assert restored["review"]["summary"]["acpl"]["white"] == 45.0


def test_archive_stats_trend_and_eco_search(api: ChessMasterApi) -> None:
    game = _fools_mate(api, "Trend White", "Trend Black")
    report = _report(game)
    api._engine.restore_review(report, signature=report["signature"], preset="standard")
    api.get_game_review()

    archive = api.list_archived_games()["data"]
    stats = archive["stats"]
    assert stats["avg_accuracy"] == 80.4
    assert stats["peak_elo"] == 1450
    assert stats["reviewed"] == 1

    trend = archive["trend"]
    assert len(trend) == 1
    assert trend[0]["game_id"] == game["game_id"]
    assert trend[0]["accuracy"] == 80.4
    assert trend[0]["estimated_elo"] == 1450

    for query in ("C20", "King's Pawn", "Trend White"):
        found = api.list_archived_games(query=query)["data"]
        assert found["total"] == 1, query
        assert found["games"][0]["game_id"] == game["game_id"]


def test_rearchive_preserves_review_metrics(api: ChessMasterApi) -> None:
    game = _fools_mate(api, "Keep White", "Keep Black")
    report = _report(game)
    api._engine.restore_review(report, signature=report["signature"], preset="standard")
    api.get_game_review()

    # Opening another game re-archives the current line without review fields.
    api.new_game("pass_play", {"white_name": "Next White", "black_name": "Next Black"})
    api.save_current_game()

    row = api._store.get_game(game["game_id"])
    assert row is not None
    assert row["white_accuracy"] == 72.5
    assert row["black_accuracy"] == 88.25
    assert row["white_estimated_elo"] == 1100
    assert row["black_estimated_elo"] == 1450
    assert row["brilliant_count"] == 1
    assert row["review_signature"] == report["signature"]
    assert api.list_archived_games()["data"]["stats"]["reviewed"] == 1


def test_archived_review_is_available_without_replaying_moves(api: ChessMasterApi) -> None:
    game = _fools_mate(api)
    report = _report(game)
    api._engine.restore_review(report, signature=report["signature"], preset="standard")
    api.get_game_review()

    # A fresh API on the same database behaves like a relaunch.
    reopened = ChessMasterApi(db_path=api._store.path)
    try:
        state = reopened.load_archived_game(game["game_id"])["data"]
        assert state["restored_review"] is True
        payload = reopened.get_game_review()["data"]
        assert payload["report"]["game_id"] == game["game_id"]
        assert payload["review"]["status"] == "complete"
    finally:
        reopened._shutdown()


def test_export_archive_pgn_batch(api: ChessMasterApi) -> None:
    first = _fools_mate(api, "Export One", "Export Two")
    api.new_game("pass_play", {"white_name": "Export Three", "black_name": "Export Four"})
    api.make_move("e4")
    api.make_move("e5")
    api.save_current_game()
    second_id = api.get_game_state()["data"]["game_id"]

    batch = api.export_archive_pgn([first["game_id"], second_id])["data"]
    assert batch["count"] == 2
    assert batch["filename"].startswith("chess-master-archive-")
    assert batch["filename"].endswith(".pgn")
    assert "Export One" in batch["pgn"]
    assert "Export Three" in batch["pgn"]

    everything = api.export_archive_pgn()["data"]
    assert everything["count"] == 2


def test_delete_one_game_then_clear_the_archive(api: ChessMasterApi) -> None:
    first = _fools_mate(api)
    api.new_game("pass_play", {"white_name": "Keep", "black_name": "Me"})
    api.make_move("e4")
    api.make_move("e5")
    api.save_current_game()

    deleted = api.delete_archived_game(first["game_id"])["data"]
    assert deleted["deleted"] is True
    assert api.list_archived_games()["data"]["total"] == 1

    cleared = api.clear_game_archive()["data"]
    assert cleared["deleted"] == 1
    archive = api.list_archived_games()["data"]
    assert archive["total"] == 0
    assert archive["games"] == []
    assert archive["stats"]["total"] == 0


def test_load_missing_archive_returns_structured_error(api: ChessMasterApi) -> None:
    response = api.load_archived_game("does-not-exist")
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_input"


def test_cleared_archive_is_not_rearchived_by_new_game(api: ChessMasterApi) -> None:
    _fools_mate(api)
    assert api.clear_game_archive()["data"]["deleted"] == 1
    assert api.list_archived_games()["data"]["total"] == 0

    api.new_game("pass_play")
    api.engine_poll()
    assert api.list_archived_games()["data"]["total"] == 0


def test_custom_clock_survives_a_timeout_archive(tmp_path: Path) -> None:
    api = ChessMasterApi(db_path=tmp_path / "clock.db")
    try:
        api.new_game(
            "pass_play",
            {"time_control": {"initial_ms": 120, "increment_ms": 30, "label": "Practice"}},
        )
        time.sleep(0.3)
        polled = api.engine_poll()["data"]
        assert polled["state"]["status"]["state"] == "timeout"

        row = api.list_archived_games()["data"]["games"][0]
        assert row["termination_state"] == "timeout"
        loaded = api.load_archived_game(row["game_id"])["data"]
        assert loaded["status"]["state"] == "timeout"
        assert loaded["clocks"]["enabled"] is True
        assert loaded["clocks"]["initial_ms"] == 120
        assert loaded["clocks"]["increment_ms"] == 30
        assert loaded["clocks"]["running"] is False
        assert loaded["clocks"]["white_ms"] == 0
    finally:
        api._shutdown()
