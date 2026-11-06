"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def read_fixture():
    return fixture_text


@pytest.fixture(autouse=True)
def isolated_default_archive(tmp_path, monkeypatch):
    """Keep auto-saved test games out of the user's real state/ archive."""

    from chess_master.storage import db as store_db

    monkeypatch.setattr(store_db, "DEFAULT_DB_PATH", tmp_path / "chess_master.db")
