"""Packaging: frozen path resolution, console suppression and CLI wiring."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import chess.engine
import pytest

from chess_master import app, paths
from chess_master.analysis import openings
from chess_master.engine.stockfish import StockfishManager


@pytest.fixture
def frozen_paths(monkeypatch, tmp_path):
    """Reload ``chess_master.paths`` as a PyInstaller onedir build."""

    meipass = tmp_path / "meipass"
    meipass.mkdir()
    exe_dir = tmp_path / "app"
    (exe_dir / "bin" / "stockfish").mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "ChessMaster.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    module = importlib.reload(paths)
    try:
        yield module, meipass, exe_dir
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_frozen_paths_resolve_from_meipass_and_exe_dir(frozen_paths) -> None:
    module, meipass, exe_dir = frozen_paths

    assert module.FROZEN is True
    assert module.ASSET_ROOT == meipass / "chess_master"
    assert module.WEB_ROOT == meipass / "chess_master" / "web"
    assert module.APP_DIR == exe_dir
    assert module.BIN_ROOT == exe_dir / "bin"
    assert module.STATE_ROOT == exe_dir / "state"
    assert module.STATE_ROOT.is_dir()
    assert module.DEFAULT_DB_PATH == exe_dir / "state" / "chess_master.db"


def test_frozen_paths_fall_back_when_app_dir_is_not_writable(frozen_paths, monkeypatch, tmp_path) -> None:
    _, meipass, _ = frozen_paths

    blocker = tmp_path / "blocker"
    blocker.write_text("")
    unwritable = blocker / "app"
    # Move the executable under a regular file so no folder can be created.
    monkeypatch.setattr(sys, "executable", str(unwritable / "ChessMaster.exe"))
    module = importlib.reload(paths)

    assert module.STATE_ROOT == tmp_path / "localappdata" / "ChessMaster" / "state"
    assert module.STATE_ROOT.is_dir()
    # No bundled bin next to the exe, so the PyInstaller payload is used.
    assert module.BIN_ROOT == meipass / "bin"


def test_openings_data_lives_in_the_asset_root() -> None:
    assert openings.DEFAULT_DATA_PATH == paths.ASSET_ROOT / "analysis" / "openings_data.tsv"
    assert openings.DEFAULT_DATA_PATH.is_file()


def test_engine_factory_hides_the_console_on_windows(monkeypatch) -> None:
    if not hasattr(subprocess, "CREATE_NO_WINDOW"):
        pytest.skip("CREATE_NO_WINDOW is Windows-only")
    captured: dict[str, object] = {}

    def fake_popen(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", staticmethod(fake_popen))
    monkeypatch.setattr("chess_master.engine.stockfish.os.name", "nt")

    StockfishManager._default_engine_factory(r"C:\bin\stockfish.exe")

    assert captured["path"] == r"C:\bin\stockfish.exe"
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert captured["timeout"] == 10.0


def test_serve_accepts_host_and_port(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_server(port=8765, *, host="127.0.0.1", engine_path=None) -> int:
        seen.update(port=port, host=host, engine_path=engine_path)
        return 0

    monkeypatch.setattr(app, "run_server", fake_run_server)
    assert app.main(["--serve", "--host", "0.0.0.0", "--port", "8123"]) == 0
    assert seen == {"port": 8123, "host": "0.0.0.0", "engine_path": None}


def test_serve_reads_host_and_port_from_the_environment(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_server(port=8765, *, host="127.0.0.1", engine_path=None) -> int:
        seen.update(port=port, host=host)
        return 0

    monkeypatch.setattr(app, "run_server", fake_run_server)
    monkeypatch.setenv("CHESS_MASTER_HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    assert app.main(["--serve"]) == 0
    assert seen == {"port": 9000, "host": "0.0.0.0"}
