"""Packaging contract: frozen paths and windowless Stockfish spawning."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import chess.engine
import pytest

import chess_master.paths as paths_module
from chess_master.engine import stockfish as stockfish_module


@pytest.fixture(autouse=True)
def _restore_source_paths(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(paths_module)


def _freeze(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    app = tmp_path / "app"
    bundle.mkdir()
    app.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "ChessMaster.exe"))
    return bundle, app


def test_source_mode_paths_are_unchanged() -> None:
    module = importlib.reload(paths_module)

    assert (module.PROJECT_ROOT / "pyproject.toml").is_file()
    assert module.WEB_ROOT == module.PACKAGE_ROOT / "web"
    assert module.BIN_ROOT == module.PROJECT_ROOT / "bin"
    assert module.STATE_ROOT == module.PROJECT_ROOT / "state"
    assert module.DEFAULT_DB_PATH == module.STATE_ROOT / "chess_master.db"
    assert module.OPENINGS_DATA_PATH.is_file()


def test_frozen_installed_mode_uses_localappdata(monkeypatch, tmp_path) -> None:
    bundle, app = _freeze(monkeypatch, tmp_path)
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    (app / "bin").mkdir()
    (bundle / "bin").mkdir()
    (bundle / "chess_master" / "analysis").mkdir(parents=True)
    (bundle / "chess_master" / "web").mkdir(parents=True)

    module = importlib.reload(paths_module)

    assert module.BUNDLE_ROOT == bundle
    assert module.APP_DIR == app
    assert module.WEB_ROOT == bundle / "chess_master" / "web"
    assert module.OPENINGS_DATA_PATH == bundle / "chess_master" / "analysis" / "openings_data.tsv"
    assert module.BIN_ROOT == app / "bin"
    assert module.STATE_ROOT == local / "ChessMaster" / "state"
    assert module.DEFAULT_DB_PATH == module.STATE_ROOT / "chess_master.db"
    assert module.STATE_ROOT.is_dir()


def test_frozen_bin_falls_back_to_bundle(monkeypatch, tmp_path) -> None:
    bundle, _app = _freeze(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    (bundle / "bin").mkdir()

    module = importlib.reload(paths_module)

    assert module.BIN_ROOT == bundle / "bin"


def test_frozen_portable_flag_keeps_state_beside_exe(monkeypatch, tmp_path) -> None:
    _bundle, app = _freeze(monkeypatch, tmp_path)
    (app / "portable.flag").write_text("", encoding="utf-8")

    module = importlib.reload(paths_module)

    assert module.STATE_ROOT == app / "state"
    assert module.DEFAULT_DB_PATH == app / "state" / "chess_master.db"


def test_engine_factory_uses_create_no_window_on_windows(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(path: str, **kwargs: object) -> object:
        captured["path"] = path
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", fake_popen)
    monkeypatch.setattr(stockfish_module.os, "name", "nt")

    stockfish_module.StockfishManager._default_engine_factory("C:/stockfish/stockfish.exe")

    assert captured["path"] == "C:/stockfish/stockfish.exe"
    assert captured["timeout"] == 10.0
    assert captured["creationflags"] == 0x08000000
