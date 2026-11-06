"""Local SQLite archive for games and completed Game Review reports.

The store is deliberately small and synchronous: every write happens on the
PyWebView API thread while the API's re-entrant lock is held.  A second lock
here protects callers that use the store directly from multiple threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chess_master.paths import DEFAULT_DB_PATH

__all__ = ["GAME_COLUMNS", "GameStore", "default_db_path"]

GAME_COLUMNS: tuple[str, ...] = (
    "game_id",
    "created_at",
    "updated_at",
    "mode",
    "source",
    "event",
    "white_name",
    "black_name",
    "bot_elo",
    "player_color",
    "time_control",
    "result",
    "termination_state",
    "termination_reason",
    "winner",
    "total_plies",
    "start_fen",
    "final_fen",
    "pgn",
    "moves_json",
    "headers_json",
    "opening_eco",
    "opening_name",
    "white_accuracy",
    "black_accuracy",
    "white_acpl",
    "black_acpl",
    "white_estimated_elo",
    "black_estimated_elo",
    "brilliant_count",
    "blunder_count",
    "review_preset",
    "review_signature",
)

# A plain re-archive (opening another game) carries no review metrics.  Keep the
# values a completed review already stored instead of overwriting them with NULL.
_REVIEW_COLUMNS = frozenset(
    {
        "white_accuracy",
        "black_accuracy",
        "white_acpl",
        "black_acpl",
        "white_estimated_elo",
        "black_estimated_elo",
        "brilliant_count",
        "blunder_count",
        "review_preset",
        "review_signature",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    mode TEXT,
    source TEXT,
    event TEXT,
    white_name TEXT,
    black_name TEXT,
    bot_elo INTEGER,
    player_color TEXT,
    time_control TEXT,
    result TEXT,
    termination_state TEXT,
    termination_reason TEXT,
    winner TEXT,
    total_plies INTEGER,
    start_fen TEXT,
    final_fen TEXT,
    pgn TEXT,
    moves_json TEXT,
    headers_json TEXT,
    opening_eco TEXT,
    opening_name TEXT,
    white_accuracy REAL,
    black_accuracy REAL,
    white_acpl REAL,
    black_acpl REAL,
    white_estimated_elo INTEGER,
    black_estimated_elo INTEGER,
    brilliant_count INTEGER,
    blunder_count INTEGER,
    review_preset TEXT,
    review_signature TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    game_id TEXT PRIMARY KEY,
    signature TEXT,
    preset TEXT,
    schema_version INTEGER,
    completed_at TEXT,
    report_json TEXT,
    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS games_updated_at ON games (updated_at DESC);
"""


def default_db_path() -> Path:
    return DEFAULT_DB_PATH


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class GameStore:
    """Thread-safe access to the two-table local archive."""

    def __init__(self, path: str | Path | None = None) -> None:
        raw = str(path) if path is not None else ""
        self.path: str | Path = raw if raw == ":memory:" else Path(raw or DEFAULT_DB_PATH)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- games -----------------------------------------------------------
    def upsert_game(self, record: Mapping[str, Any]) -> dict[str, Any]:
        game_id = str(record.get("game_id") or "").strip()
        if not game_id:
            raise ValueError("An archived game needs a game_id.")
        row = {key: record.get(key) for key in GAME_COLUMNS}
        row["game_id"] = game_id
        if not row.get("updated_at"):
            row["updated_at"] = _utc_now()
        columns = ", ".join(GAME_COLUMNS)
        placeholders = ", ".join("?" for _ in GAME_COLUMNS)
        updates = ", ".join(
            f"{column} = COALESCE(excluded.{column}, games.{column})"
            if column in _REVIEW_COLUMNS
            else f"{column} = excluded.{column}"
            for column in GAME_COLUMNS
            if column != "game_id"
        )
        with self._lock:
            self._conn.execute(
                f"INSERT INTO games ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(game_id) DO UPDATE SET {updates}",
                [row[column] for column in GAME_COLUMNS],
            )
            self._conn.commit()
            stored = self._conn.execute(
                "SELECT * FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
        return dict(stored) if stored is not None else {}

    def get_game(self, game_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM games WHERE game_id = ?", (str(game_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_games(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        mode: str | None = None,
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: list[Any] = []
        if mode:
            where.append("mode = ?")
            params.append(mode)
        if query:
            needle = f"%{query.strip()}%"
            where.append(
                "(white_name LIKE ? OR black_name LIKE ? OR event LIKE ? "
                "OR opening_name LIKE ? OR opening_eco LIKE ?)"
            )
            params.extend([needle, needle, needle, needle, needle])
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._lock:
            total = int(
                self._conn.execute(f"SELECT COUNT(*) FROM games{clause}", params).fetchone()[0]
            )
            rows = self._conn.execute(
                f"SELECT * FROM games{clause} ORDER BY updated_at DESC, created_at DESC "
                "LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def delete_game(self, game_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM games WHERE game_id = ?", (str(game_id),))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear(self) -> int:
        with self._lock:
            deleted = int(self._conn.execute("SELECT COUNT(*) FROM games").fetchone()[0])
            self._conn.execute("DELETE FROM reviews")
            self._conn.execute("DELETE FROM games")
            self._conn.commit()
            self._conn.execute("VACUUM")
        return deleted

    # -- reviews ---------------------------------------------------------
    def save_review(
        self,
        game_id: str,
        *,
        signature: str | None,
        preset: str | None,
        schema_version: int | None,
        report: Mapping[str, Any] | str,
        completed_at: str | None = None,
    ) -> None:
        payload = report if isinstance(report, str) else json.dumps(report, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO reviews "
                "(game_id, signature, preset, schema_version, completed_at, report_json) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(game_id) DO UPDATE SET signature = excluded.signature, "
                "preset = excluded.preset, schema_version = excluded.schema_version, "
                "completed_at = excluded.completed_at, report_json = excluded.report_json",
                (
                    str(game_id),
                    signature,
                    preset,
                    schema_version,
                    completed_at or _utc_now(),
                    payload,
                ),
            )
            self._conn.commit()

    def get_review(self, game_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reviews WHERE game_id = ?", (str(game_id),)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            record["report"] = json.loads(record.get("report_json") or "{}")
        except ValueError:
            record["report"] = None
        return record

    # -- diagnostics -----------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(result = '1-0') AS white_wins, "
                "SUM(result = '0-1') AS black_wins, "
                "SUM(result = '1/2-1/2') AS draws, "
                "SUM(termination_state = 'timeout') AS timeouts, "
                "SUM(review_signature IS NOT NULL) AS reviewed, "
                "AVG(CASE WHEN white_accuracy IS NOT NULL AND black_accuracy IS NOT NULL "
                "THEN (white_accuracy + black_accuracy) / 2.0 END) AS avg_accuracy, "
                "MAX(MAX(COALESCE(white_estimated_elo, 0), "
                "COALESCE(black_estimated_elo, 0))) AS peak_elo "
                "FROM games"
            ).fetchone()
        data = dict(row) if row is not None else {}
        counts = {
            key: int(data.get(key) or 0)
            for key in (
                "total",
                "white_wins",
                "black_wins",
                "draws",
                "timeouts",
                "reviewed",
                "peak_elo",
            )
        }
        counts["avg_accuracy"] = round(float(data.get("avg_accuracy") or 0.0), 1)
        return counts

    def history_trend(self, *, limit: int = 12) -> list[dict[str, Any]]:
        """Reviewed games, oldest first, for the performance sparkline."""

        limit = max(1, min(int(limit), 50))
        with self._lock:
            rows = self._conn.execute(
                "SELECT game_id, updated_at, white_name, black_name, result, "
                "white_accuracy, black_accuracy, white_estimated_elo, black_estimated_elo "
                "FROM games WHERE review_signature IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        points: list[dict[str, Any]] = []
        for row in reversed(rows):
            record = dict(row)
            accuracies = [
                float(value)
                for value in (record.get("white_accuracy"), record.get("black_accuracy"))
                if value is not None
            ]
            elos = [
                int(value)
                for value in (
                    record.get("white_estimated_elo"),
                    record.get("black_estimated_elo"),
                )
                if value is not None
            ]
            points.append(
                {
                    "game_id": record["game_id"],
                    "date": record.get("updated_at"),
                    "white_name": record.get("white_name"),
                    "black_name": record.get("black_name"),
                    "result": record.get("result"),
                    "accuracy": round(sum(accuracies) / len(accuracies), 1)
                    if accuracies
                    else None,
                    "estimated_elo": max(elos) if elos else None,
                }
            )
        return points

    def db_info(self) -> dict[str, Any]:
        path = str(self.path)
        size = 0
        if self.path != ":memory:":
            try:
                size = Path(self.path).stat().st_size
            except OSError:
                size = 0
        return {"path": path, "size_bytes": size}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
