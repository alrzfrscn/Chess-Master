"""Stockfish discovery, Elo calibration and the single-owner worker.

The whole application shares **one** Stockfish process.  A dedicated worker
thread owns the ``chess.engine.SimpleEngine`` instance and consumes one
coalesced job per kind (``analysis`` and ``bot``).  API calls only enqueue
jobs, so PyWebView's JS bridge and the loopback HTTP server never wait for a
search.

Every job carries an :class:`EngineToken` — ``(game_id, revision, ply, fen)``.
The worker checks the token between UCI info updates and the coordinator checks
it again before publishing a result, so a stale search can never leak into a
newer revision.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import random
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import chess
import chess.engine

from chess_master.analysis.review import (
    REVIEW_MULTIPV,
    ReviewCancelled,
    ReviewRequest,
    ReviewSearchResult,
    run_game_review,
)
from chess_master.analysis.winprob import (
    MATE_CP,
    WINPROB_VERSION,
    white_cp_from_mate,
    win_probability,
)
from chess_master.paths import BIN_ROOT, PROJECT_ROOT

__all__ = [
    "AnalysisCache",
    "BotChoice",
    "ELO_PRESETS",
    "EngineDiscovery",
    "EngineJob",
    "EngineLine",
    "EngineProfile",
    "EngineToken",
    "FULL_STRENGTH_AT",
    "LIMIT_STRENGTH_MIN",
    "MAX_ELO",
    "MIN_ELO",
    "StockfishManager",
    "analysis_payload_from_infos",
    "discover_engine",
    "elo_profile",
    "format_score",
    "normalize_fen",
    "score_white",
    "select_bot_move",
    "strength_label",
    "terminal_evaluation",
]

logger = logging.getLogger(__name__)

MIN_ELO = 400
MAX_ELO = 3000
LIMIT_STRENGTH_MIN = 1320
FULL_STRENGTH_AT = 3000
DEFAULT_ELO = 1400
DEFAULT_DEPTH = 16
DEFAULT_MULTIPV = 3
WORKER_THREADS = 1
WORKER_HASH_MB = 128

ELO_PRESETS = (
    {"elo": 600, "label": "Beginner"},
    {"elo": 1000, "label": "Casual"},
    {"elo": 1400, "label": "Club"},
    {"elo": 1800, "label": "Advanced"},
    {"elo": 2200, "label": "Master"},
    {"elo": 2600, "label": "Grandmaster"},
    {"elo": 3000, "label": "Maximum"},
)


# ---------------------------------------------------------------------------
# Tokens and snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EngineToken:
    """Identity of one view of one game revision."""

    game_id: str
    revision: int
    ply: int
    fen: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "revision": self.revision,
            "ply": self.ply,
            "fen": self.fen,
        }

    def matches(self, other: "EngineToken | None") -> bool:
        return other is not None and self == other

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EngineToken":
        return cls(
            game_id=str(value["game_id"]),
            revision=int(value["revision"]),
            ply=int(value["ply"]),
            fen=str(value["fen"]),
        )


def normalize_fen(fen: str) -> str:
    """Drop half-move/full-move counters so transpositions share a cache key."""

    parts = str(fen).strip().split()
    if len(parts) < 4:
        return str(fen).strip()
    return " ".join(parts[:4])


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EngineDiscovery:
    """Result of looking for a Stockfish executable."""

    path: Path | None
    source: str
    candidates: tuple[str, ...] = ()
    requested: str | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.path is not None and self.path.is_file()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "source": self.source,
            "available": self.available,
            "requested": self.requested,
            "error": self.error,
            "candidates": list(self.candidates),
        }


_EXECUTABLE_SUFFIXES = (".exe", ".bin", "") if os.name == "nt" else ("",)
_NAME_PREFERENCE = ("avx2", "bmi2", "x86-64", "modern", "popcnt", "universal")


def _looks_like_engine(path: Path) -> bool:
    if not path.is_file():
        return False
    lowered = path.name.lower()
    if not lowered.startswith("stockfish"):
        return False
    if any(lowered.endswith(suffix) for suffix in (".nnue", ".zip", ".tar", ".gz", ".txt", ".md")):
        return False
    if os.name != "nt" and not os.access(path, os.X_OK):
        return False
    return True


def _rank_candidates(paths: Iterable[Path]) -> list[Path]:
    def score(path: Path) -> tuple[int, int, str]:
        lowered = path.name.lower()
        preference = next(
            (index for index, marker in enumerate(_NAME_PREFERENCE) if marker in lowered),
            len(_NAME_PREFERENCE),
        )
        return (preference, len(lowered), lowered)

    return sorted({path.resolve() for path in paths if _looks_like_engine(path)}, key=score)


def _search_directory(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    if directory.is_file():
        return [directory] if _looks_like_engine(directory) else []
    found: list[Path] = []
    try:
        for suffix in _EXECUTABLE_SUFFIXES:
            found.extend(directory.glob(f"stockfish*{suffix}"))
            found.extend(directory.glob(f"*/stockfish*{suffix}"))
    except OSError:
        return []
    return _rank_candidates(found)


def discover_engine(
    explicit: str | os.PathLike[str] | None = None,
    *,
    project_root: Path | None = None,
) -> EngineDiscovery:
    """Find Stockfish in the approved priority order.

    1. explicit config / CLI argument
    2. ``STOCKFISH_PATH``
    3. project-local ``bin/`` then ``assets/engine/`` (and package assets)
    4. ``shutil.which("stockfish")``
    """

    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    requested: str | None = None
    tried: list[str] = []

    raw = explicit if explicit is not None else os.environ.get("STOCKFISH_PATH")
    if explicit is not None:
        source = "config"
        requested = str(explicit)
    elif raw:
        source = "env"
    else:
        source = ""

    if raw:
        candidate = Path(str(raw)).expanduser()
        tried.append(str(candidate))
        if candidate.is_dir():
            matches = _search_directory(candidate)
            if matches:
                return EngineDiscovery(matches[0], source, tuple(tried), requested=requested)
            return EngineDiscovery(
                None,
                source,
                tuple(tried),
                requested=requested,
                error=f"No Stockfish executable found in {candidate}.",
            )
        if _looks_like_engine(candidate):
            return EngineDiscovery(candidate.resolve(), source, tuple(tried), requested=requested)
        return EngineDiscovery(
            None,
            source,
            tuple(tried),
            requested=requested,
            error=f"Stockfish executable not found at {candidate}.",
        )

    local_directories = [
        root / "bin",
        root / "bin" / "stockfish",
        root / "assets" / "engine",
        root / "assets" / "engine" / "stockfish",
        BIN_ROOT,
    ]
    seen: set[Path] = set()
    for directory in local_directories:
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        tried.append(str(directory))
        matches = _search_directory(directory)
        if matches:
            return EngineDiscovery(matches[0], f"project:{directory.name}", tuple(tried))

    which = shutil.which("stockfish")
    if which:
        tried.append(which)
        path = Path(which)
        if _looks_like_engine(path):
            return EngineDiscovery(path.resolve(), "path", tuple(tried))

    return EngineDiscovery(
        None,
        "missing",
        tuple(tried),
        error=(
            "Stockfish was not found. Place a binary in bin/ or assets/engine/, "
            "set STOCKFISH_PATH, or choose a custom path in the app."
        ),
    )


# ---------------------------------------------------------------------------
# Elo calibration
# ---------------------------------------------------------------------------


def strength_label(elo: int) -> str:
    if elo < 800:
        return "Beginner"
    if elo < 1200:
        return "Casual"
    if elo < 1600:
        return "Club"
    if elo < 2000:
        return "Advanced"
    if elo < 2400:
        return "Master"
    if elo < 2800:
        return "Grandmaster"
    return "Maximum"


@dataclass(frozen=True, slots=True)
class EngineProfile:
    """How one requested Elo is translated into UCI options and limits."""

    elo: int
    label: str
    mode: str
    limit_strength: bool
    uci_elo: int | None
    skill_level: int
    multipv: int
    depth: int
    nodes: int | None
    movetime: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "elo": self.elo,
            "label": self.label,
            "mode": self.mode,
            "limit_strength": self.limit_strength,
            "uci_elo": self.uci_elo,
            "skill_level": self.skill_level,
            "multipv": self.multipv,
            "depth": self.depth,
            "nodes": self.nodes,
            "movetime": round(self.movetime, 3),
            "uci_options": self.uci_options(),
        }

    def uci_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "Threads": WORKER_THREADS,
            "Hash": WORKER_HASH_MB,
            "UCI_LimitStrength": self.limit_strength,
            "Skill Level": self.skill_level,
            "MultiPV": self.multipv,
        }
        if self.uci_elo is not None:
            options["UCI_Elo"] = self.uci_elo
        return options


def _coerce_elo(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Elo must be a whole number between 400 and 3000.")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, (int, float)) or not float(value).is_integer():
        raise ValueError("Elo must be a whole number between 400 and 3000.")
    elo = int(value)
    if elo < MIN_ELO or elo > MAX_ELO:
        raise ValueError(f"Elo must be between {MIN_ELO} and {MAX_ELO}.")
    return elo


def elo_profile(elo: int, *, uci_elo_range: tuple[int, int] = (1320, 3190)) -> EngineProfile:
    """Map 400–3000 Elo to a Stockfish personality.

    ``>= 1320`` uses Stockfish's calibrated ``UCI_LimitStrength`` / ``UCI_Elo``
    pair.  ``3000`` is deliberately full strength.  Below 1320 the engine has
    no calibrated Elo, so Skill Level, a shallow node/depth budget and a
    three-line weighted chooser produce human-like inaccuracies.
    """

    try:
        wanted = _coerce_elo(elo)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    label = strength_label(wanted)
    if wanted >= FULL_STRENGTH_AT:
        return EngineProfile(
            elo=wanted,
            label=label,
            mode="full",
            limit_strength=False,
            uci_elo=None,
            skill_level=20,
            multipv=1,
            depth=18,
            nodes=None,
            movetime=1.2,
        )

    if wanted >= LIMIT_STRENGTH_MIN:
        low, high = uci_elo_range
        clamped = max(low, min(high, wanted))
        ratio = (wanted - LIMIT_STRENGTH_MIN) / (FULL_STRENGTH_AT - LIMIT_STRENGTH_MIN)
        return EngineProfile(
            elo=wanted,
            label=label,
            mode="limit_strength",
            limit_strength=True,
            uci_elo=clamped,
            skill_level=20,
            multipv=1,
            depth=10 + round(ratio * 6),
            nodes=None,
            movetime=0.25 + ratio * 0.75,
        )

    ratio = (wanted - MIN_ELO) / (LIMIT_STRENGTH_MIN - MIN_ELO)
    return EngineProfile(
        elo=wanted,
        label=label,
        mode="skill_weighted",
        limit_strength=False,
        uci_elo=None,
        skill_level=max(0, min(10, round(ratio * 10))),
        multipv=3,
        depth=3 + round(ratio * 7),
        nodes=int(2_000 + ratio * 180_000),
        movetime=0.08 + ratio * 0.22,
    )


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------


def score_white(score: chess.engine.PovScore) -> tuple[int | None, int]:
    """Return ``(mate, white_pov_cp)`` from any PovScore.

    ``mate`` is positive when White mates, negative when Black mates, and ``0``
    for a terminal mate.  Mate lines also receive a bounded pseudo-cp value so
    ordering and charts never see 100000.
    """

    white = score.pov(chess.WHITE)
    if white.is_mate():
        mate = white.mate()
        mate_value = 0 if mate is None else int(mate)
        return mate_value, white_cp_from_mate(mate_value)
    return None, int(white.score(mate_score=MATE_CP))


def _mate_probability(mate: int) -> float:
    if mate > 0:
        return 100.0
    if mate < 0:
        return 0.0
    return 0.0


def format_score(cp: int | None, mate: int | None) -> str:
    """Human label from White POV: ``+1.4``, ``-0.8``, ``0.0``, ``M3``."""

    if mate is not None:
        if mate > 0:
            return f"M{mate}"
        if mate < 0:
            return f"-M{abs(mate)}"
        return "M0"
    if cp is None:
        return "—"
    pawns = cp / 100.0
    if abs(pawns) < 0.05:
        return "0.0"
    return f"{pawns:+.1f}"


def _pv_san(board: chess.Board, pv: Sequence[chess.Move], limit: int = 8) -> list[str]:
    preview = board.copy(stack=True)
    result: list[str] = []
    for move in list(pv)[:limit]:
        if move not in preview.legal_moves:
            break
        try:
            result.append(preview.san(move))
        except ValueError:
            break
        preview.push(move)
    return result


@dataclass(slots=True)
class EngineLine:
    rank: int
    score_cp: int
    mate: int | None
    win_prob_white: float
    score_text: str
    pv_uci: list[str]
    pv_san: list[str]
    best_move: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "score_cp": self.score_cp,
            "mate": self.mate,
            "win_prob_white": round(self.win_prob_white, 2),
            "score_text": self.score_text,
            "pv_uci": list(self.pv_uci),
            "pv_san": list(self.pv_san),
            "best_move": copy.deepcopy(self.best_move),
        }


def _line_from_info(board: chess.Board, info: Mapping[str, Any], rank: int) -> EngineLine | None:
    score = info.get("score")
    pv = info.get("pv") or []
    if score is None or not pv:
        return None
    mate, cp = score_white(score)
    if mate is not None:
        probability = _mate_probability(mate)
    else:
        probability = win_probability(cp)
    pv_uci = [move.uci() for move in pv]
    pv_san = _pv_san(board, pv)
    first = pv[0]
    best_move = {
        "uci": first.uci(),
        "from": chess.square_name(first.from_square),
        "to": chess.square_name(first.to_square),
        "promotion": first.promotion and chess.piece_symbol(first.promotion),
        "san": pv_san[0] if pv_san else None,
    }
    return EngineLine(
        rank=rank,
        score_cp=cp,
        mate=mate,
        win_prob_white=probability,
        score_text=format_score(cp, mate),
        pv_uci=pv_uci,
        pv_san=pv_san,
        best_move=best_move,
    )


def analysis_payload_from_infos(
    board: chess.Board,
    infos: Sequence[Mapping[str, Any]],
    *,
    token: EngineToken | None = None,
    cached: bool = False,
    engine_name: str = "Stockfish",
    engine_version: str | None = None,
) -> dict[str, Any] | None:
    """Normalize a MultiPV search into the wire payload (always White POV)."""

    ordered = sorted(
        enumerate(infos),
        key=lambda pair: (int(pair[1].get("multipv", pair[0] + 1) or pair[0] + 1), pair[0]),
    )
    lines: list[EngineLine] = []
    for index, (_, info) in enumerate(ordered, start=1):
        line = _line_from_info(board, info, index)
        if line is not None:
            lines.append(line)
    if not lines:
        return None

    best = lines[0]
    depths = [int(info.get("depth", 0) or 0) for info in infos]
    nodes = [int(info.get("nodes", 0) or 0) for info in infos]
    times = [float(info.get("time", 0.0) or 0.0) for info in infos]
    payload: dict[str, Any] = {
        "available": True,
        "engine": engine_name,
        "engine_version": engine_version,
        "depth": max(depths) if depths else 0,
        "seldepth": max((int(info.get("seldepth", 0) or 0) for info in infos), default=0),
        "multipv": len(lines),
        "nodes": max(nodes) if nodes else 0,
        "time_ms": int(max(times) * 1000) if times else 0,
        "cached": cached,
        "model": "lichess-logistic",
        "model_version": WINPROB_VERSION,
        "updated_at": _utc_now(),
        "score_cp": best.score_cp,
        "mate": best.mate,
        "score_text": best.score_text,
        "win_prob_white": round(best.win_prob_white, 2),
        "best_move": copy.deepcopy(best.best_move),
        "pv": list(best.pv_uci),
        "pv_san": list(best.pv_san),
        "lines": [line.to_dict() for line in lines],
        "terminal": None,
    }
    if token is not None:
        payload["token"] = token.to_dict()
    return payload


def terminal_evaluation(
    board: chess.Board,
    *,
    token: EngineToken | None = None,
    engine_name: str = "Stockfish",
    engine_version: str | None = None,
) -> dict[str, Any]:
    """An evaluation for a position where no search should run."""

    if board.is_checkmate():
        white_wins = board.turn == chess.BLACK
        cp = MATE_CP if white_wins else -MATE_CP
        probability = 100.0 if white_wins else 0.0
        terminal = "checkmate"
        score_text = "M0"
        mate: int | None = 0
    else:
        cp = 0
        probability = 50.0
        terminal = "draw"
        score_text = "0.0"
        mate = None

    payload: dict[str, Any] = {
        "available": True,
        "engine": engine_name,
        "engine_version": engine_version,
        "depth": 0,
        "seldepth": 0,
        "multipv": 0,
        "nodes": 0,
        "time_ms": 0,
        "cached": False,
        "model": "lichess-logistic",
        "model_version": WINPROB_VERSION,
        "updated_at": _utc_now(),
        "score_cp": cp,
        "mate": mate,
        "score_text": score_text,
        "win_prob_white": probability,
        "best_move": None,
        "pv": [],
        "pv_san": [],
        "lines": [],
        "terminal": terminal,
    }
    if token is not None:
        payload["token"] = token.to_dict()
    return payload


# ---------------------------------------------------------------------------
# Weighted move selection for sub-1320 bots
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BotChoice:
    move: chess.Move
    weights: list[float]
    win_probs: list[float]
    chosen: int
    candidates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "move": self.move.uci(),
            "candidates": list(self.candidates),
            "weights": [round(value, 4) for value in self.weights],
            "win_probs": [round(value, 2) for value in self.win_probs],
            "chosen": self.chosen,
        }


def _mover_win_probability(board: chess.Board, info: Mapping[str, Any]) -> float | None:
    score = info.get("score")
    if score is None:
        return None
    mover = score.pov(board.turn)
    if mover.is_mate():
        mate = mover.mate() or 0
        return 100.0 if mate > 0 else 0.0
    return win_probability(mover.score(mate_score=MATE_CP))


def select_bot_move(
    infos: Sequence[Mapping[str, Any]],
    board: chess.Board,
    elo: int,
    *,
    rng: random.Random | None = None,
) -> BotChoice | None:
    """Pick one of the top three MultiPV moves with human-like noise.

    The selection is a softmax over *win-probability* deltas, not raw cp, so a
    lost pawn in a quiet position is not weighted the same as a lost pawn in a
    sharp one.  Temperature falls smoothly from 12 at 400 Elo to 2 at 1319.
    """

    randomiser = rng or random.Random()
    candidates: list[tuple[chess.Move, float]] = []
    seen: set[str] = set()
    for info in infos[:3]:
        pv = info.get("pv") or []
        if not pv:
            continue
        move = pv[0]
        if move.uci() in seen or move not in board.legal_moves:
            continue
        probability = _mover_win_probability(board, info)
        if probability is None:
            continue
        seen.add(move.uci())
        candidates.append((move, probability))
    if not candidates:
        return None

    best = max(probability for _, probability in candidates)
    ratio = max(0.0, min(1.0, (int(elo) - MIN_ELO) / (LIMIT_STRENGTH_MIN - MIN_ELO)))
    temperature = 2.0 + (1.0 - ratio) * 10.0
    weights = [math.exp(-max(0.0, best - probability) / temperature) for _, probability in candidates]
    chosen = randomiser.choices(range(len(candidates)), weights=weights, k=1)[0]
    return BotChoice(
        move=candidates[chosen][0],
        weights=weights,
        win_probs=[probability for _, probability in candidates],
        chosen=chosen,
        candidates=[move.uci() for move, _ in candidates],
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class AnalysisCache:
    """Small LRU keyed by ``(normalized_fen, depth)`` as approved."""

    def __init__(self, max_entries: int = 512) -> None:
        self._max_entries = max(16, int(max_entries))
        self._data: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, fen: str, depth: int, multipv: int = 1) -> dict[str, Any] | None:
        key = (normalize_fen(fen), int(depth))
        with self._lock:
            payload = self._data.get(key)
            if payload is None or int(payload.get("multipv", 0)) < int(multipv):
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return copy.deepcopy(payload)

    def get_at_least(self, fen: str, depth: int, multipv: int = 1) -> dict[str, Any] | None:
        """Return the shallowest cached search that is at least this deep.

        Live analysis reuses deeper results; a review never accepts a shallower
        entry than the depth it asked for, which keeps a depth-10 cache from
        leaking into a depth-14 review.
        """

        normalized = normalize_fen(fen)
        wanted_depth = int(depth)
        wanted_multipv = int(multipv)
        with self._lock:
            exact = self._data.get((normalized, wanted_depth))
            if exact is not None and int(exact.get("multipv", 0)) >= wanted_multipv:
                self._data.move_to_end((normalized, wanted_depth))
                self._hits += 1
                return copy.deepcopy(exact)
            candidates = [
                key[1]
                for key, payload in self._data.items()
                if key[0] == normalized
                and key[1] > wanted_depth
                and int(payload.get("multipv", 0)) >= wanted_multipv
            ]
            if not candidates:
                self._misses += 1
                return None
            key = (normalized, min(candidates))
            payload = self._data[key]
            self._data.move_to_end(key)
            self._hits += 1
            return copy.deepcopy(payload)

    def put(self, fen: str, depth: int, payload: Mapping[str, Any]) -> None:
        key = (normalize_fen(fen), int(depth))
        with self._lock:
            self._data[key] = copy.deepcopy(dict(payload))
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._data),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EngineJob:
    kind: str
    token: EngineToken
    start_fen: str
    moves: tuple[str, ...]
    depth: int
    multipv: int
    movetime: float | None = None
    nodes: int | None = None
    elo: int = DEFAULT_ELO
    use_cache: bool = True
    review: ReviewRequest | None = None
    seq: int = 0
    owner: str = "local"


EngineFactory = Callable[[str], Any]


class StockfishManager:
    """Owns the only Stockfish process and its owner thread."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        project_root: Path | None = None,
        on_analysis: Callable[[EngineToken, dict[str, Any]], None] | None = None,
        on_bot_move: Callable[[EngineToken, str, dict[str, Any]], None] | None = None,
        on_review_progress: Callable[[dict[str, Any]], None] | None = None,
        on_review_complete: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        engine_factory: EngineFactory | None = None,
        cache: AnalysisCache | None = None,
        auto_start: bool = False,
    ) -> None:
        self._project_root = project_root
        self._discovery = discover_engine(path, project_root=project_root)
        self._cache = cache or AnalysisCache()
        self._on_analysis = on_analysis
        self._on_bot_move = on_bot_move
        self._on_review_progress = on_review_progress
        self._on_review_complete = on_review_complete
        self._on_error = on_error
        self._engine_factory = engine_factory

        self._lock = threading.RLock()
        self._job_ready = threading.Condition(self._lock)
        self._jobs: dict[tuple[str, str], EngineJob] = {}
        self._latest_seq: dict[tuple[str, str], int] = {}
        self._active_tokens: dict[str, EngineToken] = {}
        # One listener per coordinator lets several web sessions share this worker.
        self._listeners: dict[str, Any] = {}
        self._current_job: EngineJob | None = None
        self._current_analysis: Any = None
        self._review_cancel: dict[str, threading.Event] = {}
        self._review_run_ids: dict[str, int | None] = {}
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._seq = 0

        self._engine: Any = None
        self._engine_name = "Stockfish"
        self._engine_version: str | None = None
        self._status = "ready" if self._discovery.available else "missing"
        self._message = self._discovery.error
        self._last_error: str | None = None

        if auto_start:
            self.ensure_started()

    # -- properties ------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._discovery.available

    @property
    def path(self) -> Path | None:
        return self._discovery.path

    @property
    def name(self) -> str:
        return self._engine_name

    @property
    def version(self) -> str | None:
        return self._engine_version

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def cache(self) -> AnalysisCache:
        return self._cache

    def review_run_id(self, owner: str = "local") -> int | None:
        with self._lock:
            return self._review_run_ids.get(str(owner))

    def set_callbacks(
        self,
        *,
        on_analysis: Callable[[EngineToken, dict[str, Any]], None] | None = None,
        on_bot_move: Callable[[EngineToken, str, dict[str, Any]], None] | None = None,
        on_review_progress: Callable[[dict[str, Any]], None] | None = None,
        on_review_complete: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_analysis = on_analysis
        self._on_bot_move = on_bot_move
        self._on_review_progress = on_review_progress
        self._on_review_complete = on_review_complete
        self._on_error = on_error

    # -- per-coordinator listeners ---------------------------------------
    def set_listener(self, owner: str, listener: Any) -> None:
        """Route this coordinator's callbacks to ``listener``.

        The listener is any object exposing the coordinator callback names,
        e.g. ``_on_analysis`` and ``_on_bot_move``.
        """

        with self._lock:
            self._listeners[str(owner)] = listener

    def release_owner(self, owner: str) -> None:
        """Forget a coordinator's listener and drop its queued or active work."""

        key_owner = str(owner)
        stop_analysis = None
        with self._lock:
            self._listeners.pop(key_owner, None)
            self._active_tokens.pop(key_owner, None)
            self._review_cancel.pop(key_owner, None)
            self._review_run_ids.pop(key_owner, None)
            for key in [item for item in self._jobs if item[0] == key_owner]:
                self._jobs.pop(key, None)
                self._latest_seq.pop(key, None)
            current = self._current_job
            if current is not None and current.owner == key_owner and current.kind != "review":
                stop_analysis = self._current_analysis
            self._job_ready.notify_all()
        if stop_analysis is not None:
            try:
                stop_analysis.stop()
            except Exception:  # pragma: no cover - engine may already be gone
                logger.debug("Could not cancel a released owner's search", exc_info=True)

    def _listener_for(self, owner: str | None) -> Any:
        if not owner:
            return None
        with self._lock:
            return self._listeners.get(str(owner))

    # -- lifecycle -------------------------------------------------------
    def ensure_started(self) -> bool:
        if not self.available:
            return False
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop_event.clear()
            with self._lock:
                for event in self._review_cancel.values():
                    event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="chess-master-stockfish",
                daemon=True,
            )
            self._thread.start()
        return True

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        if not self.available:
            return False
        self.ensure_started()
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                if self._status in {"ready", "error", "missing"}:
                    return self._status == "ready"
            time.sleep(0.01)
        return False

    def wait_until_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            while self._current_job is not None or self._jobs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._job_ready.wait(remaining)
            return True

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            self._stop_event.set()
            for event in self._review_cancel.values():
                event.set()
            self._jobs.clear()
            self._latest_seq.clear()
            analysis = self._current_analysis
            self._current_analysis = None
            self._job_ready.notify_all()
        if analysis is not None:
            try:
                analysis.stop()
            except Exception:  # pragma: no cover - best effort
                logger.debug("Could not stop analysis during shutdown", exc_info=True)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        with self._lock:
            engine = self._engine
            self._engine = None
            self._status = "ready" if self.available else "missing"
        if engine is not None:
            # The worker may be stuck inside quit(); force the transport down.
            self._close_engine(engine)

    def configure_path(self, path: str | os.PathLike[str] | None) -> dict[str, Any]:
        """Replace the binary.  The next job lazily starts the new process."""

        self.stop()
        self._discovery = discover_engine(path, project_root=self._project_root)
        self._cache.clear()
        with self._lock:
            self._status = "ready" if self._discovery.available else "missing"
            self._message = self._discovery.error
            self._last_error = None
            self._engine_name = "Stockfish"
            self._engine_version = None
        if self.available:
            self.ensure_started()
        return self.status_payload()

    # -- scheduling ------------------------------------------------------
    def set_active_token(self, token: EngineToken | None, owner: str = "local") -> None:
        key_owner = str(owner)
        stop_analysis = None
        with self._lock:
            previous = self._active_tokens.get(key_owner)
            if token is None:
                changed = previous is not None
                self._active_tokens.pop(key_owner, None)
            else:
                changed = previous != token
                self._active_tokens[key_owner] = token
            if changed:
                # A review belongs to the game line, not to the current view, so
                # a new revision never drops it.
                for key in [
                    item
                    for item in self._jobs
                    if item[0] == key_owner and item[1] != "review"
                ]:
                    if self._jobs[key].token != token:
                        self._jobs.pop(key, None)
                        self._latest_seq.pop(key, None)
                current = self._current_job
                if (
                    current is not None
                    and current.owner == key_owner
                    and current.kind != "review"
                    and current.token != token
                ):
                    stop_analysis = self._current_analysis
            self._job_ready.notify_all()
        if stop_analysis is not None:
            try:
                stop_analysis.stop()
            except Exception:  # pragma: no cover - engine may already be gone
                logger.debug("Could not cancel a stale search", exc_info=True)

    def submit_analysis(
        self,
        token: EngineToken,
        *,
        start_fen: str,
        moves: Sequence[str],
        depth: int = DEFAULT_DEPTH,
        multipv: int = DEFAULT_MULTIPV,
        movetime: float | None = None,
        use_cache: bool = True,
        owner: str = "local",
    ) -> str:
        if not self.available:
            return "unavailable"

        if use_cache:
            cached = self._cache.get(token.fen, depth, multipv)
            if cached is not None:
                payload = self._decorate(cached, token)
                self._emit_analysis(str(owner), token, payload)
                return "cache"

        job = self._make_job(
            "analysis",
            token,
            owner=str(owner),
            start_fen=start_fen,
            moves=moves,
            depth=depth,
            multipv=multipv,
            movetime=movetime,
            use_cache=use_cache,
        )
        self._enqueue(job)
        return "queued"

    def submit_bot(
        self,
        token: EngineToken,
        *,
        start_fen: str,
        moves: Sequence[str],
        elo: int,
        owner: str = "local",
    ) -> str:
        if not self.available:
            return "unavailable"
        profile = elo_profile(elo)
        job = self._make_job(
            "bot",
            token,
            owner=str(owner),
            start_fen=start_fen,
            moves=moves,
            depth=profile.depth,
            multipv=profile.multipv,
            movetime=profile.movetime,
            nodes=profile.nodes,
            elo=elo,
            use_cache=False,
        )
        self._enqueue(job)
        return "queued"

    def submit_review(self, request: ReviewRequest) -> str:
        """Queue one two-pass game review on the owner thread."""

        if not self.available:
            return "unavailable"
        if not isinstance(request, ReviewRequest):
            raise TypeError("submit_review expects a ReviewRequest")
        owner = str(request.owner)
        # A second review from the same coordinator supersedes the first.
        self.cancel_review(owner)
        with self._lock:
            self._review_cancel.setdefault(owner, threading.Event()).clear()
        token = EngineToken(request.game_id, 0, 0, request.start_fen)
        job = self._make_job(
            "review",
            token,
            owner=owner,
            start_fen=request.start_fen,
            moves=tuple(request.moves),
            depth=request.base_depth,
            multipv=REVIEW_MULTIPV,
            use_cache=True,
            review=request,
        )
        with self._lock:
            self._review_run_ids[owner] = job.seq
        self._enqueue(job)
        return "queued"

    def cancel_review(self, owner: str = "local") -> bool:
        """Cancel a queued or running review without touching other jobs."""

        key_owner = str(owner)
        analysis = None
        cancelled = False
        with self._lock:
            key = (key_owner, "review")
            queued = self._jobs.pop(key, None) is not None
            running = (
                self._current_job is not None
                and self._current_job.kind == "review"
                and self._current_job.owner == key_owner
            )
            if queued or running:
                self._review_cancel.setdefault(key_owner, threading.Event()).set()
                cancelled = True
            self._latest_seq.pop(key, None)
            if running:
                analysis = self._current_analysis
            self._job_ready.notify_all()
        if analysis is not None:
            try:
                analysis.stop()
            except Exception:  # pragma: no cover - engine may already be gone
                logger.debug("Could not stop a reviewed search", exc_info=True)
        return cancelled

    def _make_job(self, kind: str, token: EngineToken, **kwargs: Any) -> EngineJob:
        with self._lock:
            self._seq += 1
            return EngineJob(kind=kind, token=token, **kwargs, seq=self._seq)

    @staticmethod
    def _job_key(owner: str, kind: str) -> tuple[str, str]:
        # One coalesced job per coordinator and kind. Different coordinators
        # never overwrite each other's queued work.
        return (str(owner), str(kind))

    def _enqueue(self, job: EngineJob) -> None:
        key = self._job_key(job.owner, job.kind)
        with self._lock:
            self._jobs[key] = job
            self._latest_seq[key] = job.seq
            self._job_ready.notify_all()
        self.ensure_started()

    # -- worker loop -----------------------------------------------------
    def _worker_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    while not self._jobs and not self._stop_event.is_set():
                        self._job_ready.wait(0.5)
                    if self._stop_event.is_set():
                        break
                    # Bots first so a human never waits on a background review;
                    # everything else is FIFO across coordinators.
                    bots = [key for key in self._jobs if key[1] == "bot"]
                    candidates = bots or list(self._jobs)
                    key = min(candidates, key=lambda item: self._jobs[item].seq)
                    job = self._jobs.pop(key)
                    self._current_job = job
                    self._job_ready.notify_all()

                try:
                    if not self._job_is_current(job):
                        continue
                    engine = self._ensure_engine()
                    if engine is None:
                        continue
                    if job.kind == "analysis":
                        self._run_analysis(engine, job)
                    elif job.kind == "review":
                        self._run_review(engine, job)
                    else:
                        self._run_bot(engine, job)
                except Exception as exc:  # engine crash / bad position
                    self._handle_failure(exc)
                finally:
                    with self._lock:
                        self._current_job = None
                        self._current_analysis = None
                        self._job_ready.notify_all()
        finally:
            with self._lock:
                engine = self._engine
                self._engine = None
                self._current_job = None
                self._current_analysis = None
                self._job_ready.notify_all()
            if engine is not None:
                try:
                    engine.quit()
                except Exception:
                    logger.debug("Stockfish did not quit cleanly", exc_info=True)

    def _ensure_engine(self) -> Any:
        with self._lock:
            if self._engine is not None:
                return self._engine
            self._status = "starting"
            path = self._discovery.path
        if path is None:
            with self._lock:
                self._status = "missing"
            return None
        try:
            factory = self._engine_factory or self._default_engine_factory
            engine = factory(str(path))
            options = {
                "Threads": WORKER_THREADS,
                "Hash": WORKER_HASH_MB,
            }
            if "UCI_ShowWDL" in getattr(engine, "options", {}):
                options["UCI_ShowWDL"] = True
            engine.configure(options)
            identity = getattr(engine, "id", {}) or {}
            name = str(identity.get("name") or "Stockfish")
            version = name.split()[-1] if name.split() else None
        except Exception as exc:
            with self._lock:
                self._engine = None
                self._status = "error"
                self._message = f"Stockfish failed to start: {exc}"
                self._last_error = str(exc)
            self._emit_error(self._message)
            return None
        with self._lock:
            self._engine = engine
            self._engine_name = name
            self._engine_version = version
            self._status = "ready"
            self._message = None
        return engine

    @staticmethod
    def _default_engine_factory(path: str) -> Any:
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            # The windowed executable has no console; without this flag every
            # spawned Stockfish flashes a black window.
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return chess.engine.SimpleEngine.popen_uci(path, timeout=10.0, **popen_kwargs)

    @staticmethod
    def _close_engine(engine: Any, timeout: float = 2.0) -> None:
        """Quit an engine, force-closing its transport if quit fails or hangs."""

        if engine is None:
            return

        quit_ok = False

        def quit_engine() -> None:
            nonlocal quit_ok
            try:
                engine.quit()
                quit_ok = True
            except Exception:
                logger.debug("Stockfish did not quit cleanly", exc_info=True)

        quitter = threading.Thread(
            target=quit_engine, name="chess-master-engine-quit", daemon=True
        )
        quitter.start()
        quitter.join(max(0.0, timeout))
        if quit_ok:
            return

        logger.warning("Stockfish did not quit; terminating its transport")
        protocol = getattr(engine, "protocol", None)
        targets = [
            getattr(protocol, "transport", None),
            getattr(engine, "process", None),
        ]
        if all(target is None for target in targets):
            # No raw transport to kill; fall back to the engine's own close.
            targets.append(getattr(engine, "close", None))
        for target in targets:
            if target is None:
                continue
            try:
                if callable(target):
                    target()
                else:
                    for action in ("close", "kill", "terminate"):
                        method = getattr(target, action, None)
                        if callable(method):
                            method()
                            break
            except Exception:
                logger.debug("Forced engine shutdown step failed", exc_info=True)

    def _handle_failure(self, exc: Exception) -> None:
        logger.warning("Stockfish job failed: %s", exc, exc_info=True)
        with self._lock:
            self._status = "error"
            self._message = f"Stockfish search failed: {exc}"
            self._last_error = str(exc)
            engine = self._engine
            self._engine = None
            self._current_analysis = None
            self._current_job = None
        self._close_engine(engine)
        self._emit_error(self._message)

    def _job_is_current(self, job: EngineJob) -> bool:
        with self._lock:
            if self._stop_event.is_set():
                return False
            if self._latest_seq.get(self._job_key(job.owner, job.kind)) != job.seq:
                return False
            if job.kind == "review":
                # A review belongs to the game line, not to the current view:
                # navigating through history must never cancel it.
                event = self._review_cancel.get(job.owner)
                return event is not None and not event.is_set()
            return self._active_tokens.get(job.owner) == job.token

    def _build_board(self, job: EngineJob) -> chess.Board:
        board = chess.Board(job.start_fen)
        for uci in job.moves:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError(f"Illegal move in engine history: {uci}")
            board.push(move)
        return board

    def _configure_analysis(self, engine: Any, multipv: int) -> None:
        options: dict[str, Any] = {
            "Threads": WORKER_THREADS,
            "Hash": WORKER_HASH_MB,
            "UCI_LimitStrength": False,
            "Skill Level": 20,
        }
        if "UCI_ShowWDL" in getattr(engine, "options", {}):
            options["UCI_ShowWDL"] = True
        engine.configure(options)

    def _search_position(
        self,
        engine: Any,
        board: chess.Board,
        *,
        depth: int,
        multipv: int,
        movetime: float | None = None,
        nodes: int | None = None,
        is_current: Callable[[], bool],
    ) -> list[dict[str, Any]] | None:
        limit = chess.engine.Limit(
            depth=max(1, int(depth)),
            time=movetime,
            nodes=nodes,
        )
        cancelled = False
        with engine.analysis(board, limit, multipv=max(1, int(multipv))) as analysis:
            with self._lock:
                self._current_analysis = analysis
            try:
                for _info in analysis:
                    if not is_current():
                        cancelled = True
                        analysis.stop()
                        break
            finally:
                with self._lock:
                    if self._current_analysis is analysis:
                        self._current_analysis = None
            infos = [] if cancelled else [dict(info) for info in analysis.multipv]
        if cancelled or not is_current():
            return None
        return infos

    def _search(self, engine: Any, job: EngineJob, board: chess.Board) -> list[dict[str, Any]] | None:
        return self._search_position(
            engine,
            board,
            depth=job.depth,
            multipv=job.multipv,
            movetime=job.movetime,
            nodes=job.nodes,
            is_current=lambda: self._job_is_current(job),
        )

    def _run_analysis(self, engine: Any, job: EngineJob) -> None:
        board = self._build_board(job)
        if board.fen() != job.token.fen:
            logger.info("Discarding analysis for a mismatched FEN")
            return
        if job.use_cache:
            cached = self._cache.get(job.token.fen, job.depth, job.multipv)
            if cached is not None:
                self._emit_analysis(job.owner, job.token, self._decorate(cached, job.token))
                return
        if board.is_checkmate() or board.is_stalemate() or board.is_insufficient_material():
            payload = terminal_evaluation(
                board,
                token=job.token,
                engine_name=self.name,
                engine_version=self.version,
            )
            if job.use_cache:
                self._cache.put(job.token.fen, job.depth, payload)
            self._emit_analysis(job.owner, job.token, payload)
            return

        self._configure_analysis(engine, job.multipv)
        infos = self._search(engine, job, board)
        if infos is None:
            return
        payload = analysis_payload_from_infos(
            board,
            infos,
            token=job.token,
            engine_name=self.name,
            engine_version=self.version,
        )
        if payload is None:
            return
        if job.use_cache:
            self._cache.put(job.token.fen, job.depth, payload)
        if not self._job_is_current(job):
            return
        self._emit_analysis(job.owner, job.token, payload)

    def _run_bot(self, engine: Any, job: EngineJob) -> None:
        board = self._build_board(job)
        if board.fen() != job.token.fen:
            logger.info("Discarding bot search for a mismatched FEN")
            return
        profile = elo_profile(job.elo)
        options = {
            "Threads": WORKER_THREADS,
            "Hash": WORKER_HASH_MB,
            "UCI_LimitStrength": profile.limit_strength,
            "Skill Level": profile.skill_level,
        }
        if profile.uci_elo is not None:
            options["UCI_Elo"] = profile.uci_elo
        if "UCI_ShowWDL" in getattr(engine, "options", {}):
            options["UCI_ShowWDL"] = True
        engine.configure(options)
        infos = self._search(engine, job, board)
        if infos is None:
            return

        payload = analysis_payload_from_infos(
            board,
            infos,
            token=job.token,
            engine_name=self.name,
            engine_version=self.version,
        )
        if profile.mode == "skill_weighted":
            choice = select_bot_move(infos, board, job.elo)
            move = choice.move if choice is not None else self._first_pv_move(infos, board)
        else:
            move = self._first_pv_move(infos, board)
        if move is None:
            return
        if not self._job_is_current(job):
            return
        self._emit_bot_move(job.owner, job.token, move.uci(), payload or {})

    @staticmethod
    def _first_pv_move(infos: Sequence[Mapping[str, Any]], board: chess.Board) -> chess.Move | None:
        for info in infos:
            pv = info.get("pv") or []
            if pv and pv[0] in board.legal_moves:
                return pv[0]
        return None

    # -- game review -----------------------------------------------------
    def _configure_review(self, engine: Any, multipv: int) -> None:
        options: dict[str, Any] = {
            "Threads": WORKER_THREADS,
            "Hash": WORKER_HASH_MB,
            "UCI_LimitStrength": False,
            "Skill Level": 20,
        }
        if "UCI_ShowWDL" in getattr(engine, "options", {}):
            options["UCI_ShowWDL"] = True
        engine.configure(options)

    def _review_search(self, engine: Any, job: EngineJob) -> Any:
        def search(board: chess.Board, depth: int, multipv: int) -> ReviewSearchResult | None:
            cached = self._cache.get_at_least(board.fen(), depth, multipv)
            if cached is not None:
                return ReviewSearchResult(
                    lines=list(cached.get("lines") or []),
                    depth=int(cached.get("depth") or depth),
                    multipv=int(cached.get("multipv") or multipv),
                    cached=True,
                    nodes=int(cached.get("nodes") or 0),
                    time_ms=int(cached.get("time_ms") or 0),
                )
            if not self._job_is_current(job):
                return None
            self._configure_review(engine, multipv)
            infos = self._search_position(
                engine,
                board,
                depth=depth,
                multipv=multipv,
                is_current=lambda: self._job_is_current(job),
            )
            if infos is None:
                return None
            payload = analysis_payload_from_infos(
                board,
                infos,
                engine_name=self.name,
                engine_version=self.version,
            )
            if payload is None:
                return None
            self._cache.put(board.fen(), depth, payload)
            return ReviewSearchResult(
                lines=list(payload["lines"]),
                depth=int(payload["depth"]),
                multipv=int(payload["multipv"]),
                nodes=int(payload["nodes"]),
                time_ms=int(payload["time_ms"]),
            )

        return search

    def _run_review(self, engine: Any, job: EngineJob) -> None:
        request = job.review
        if request is None:
            return
        run_id = job.seq
        base = {
            "run_id": run_id,
            "game_id": request.game_id,
            "signature": request.signature,
            "preset": request.preset,
        }

        def progress(payload: dict[str, Any]) -> None:
            if not self._job_is_current(job):
                return
            self._emit_review_progress(
                job.owner, {**base, **payload, "status": "running"}
            )

        try:
            report = run_game_review(
                request,
                self._review_search(engine, job),
                should_cancel=lambda: not self._job_is_current(job),
                on_progress=progress,
                engine_name=self.name,
                engine_version=self.version,
            )
        except ReviewCancelled:
            self._emit_review_complete(
                job.owner, {**base, "available": True, "status": "cancelled"}
            )
            return
        except chess.engine.EngineError:
            raise
        except Exception as exc:  # malformed line / engine failure inside a search
            logger.exception("Game review failed")
            self._emit_review_complete(
                job.owner,
                {**base, "available": False, "status": "failed", "error": str(exc)},
            )
            return

        if not self._job_is_current(job):
            self._emit_review_complete(
                job.owner, {**base, "available": True, "status": "cancelled"}
            )
            return
        report["run_id"] = run_id
        self._emit_review_complete(job.owner, report)

    # -- payloads --------------------------------------------------------
    def _decorate(self, payload: Mapping[str, Any], token: EngineToken) -> dict[str, Any]:
        decorated = copy.deepcopy(dict(payload))
        decorated.update(
            {
                "token": token.to_dict(),
                "cached": True,
                "updated_at": _utc_now(),
                "engine": self.name,
                "engine_version": self.version,
            }
        )
        return decorated

    def _route(self, owner: str | None, name: str) -> Callable[..., Any] | None:
        """The coordinator listener's callback, else the process-wide default."""

        listener = self._listener_for(owner)
        if listener is not None:
            callback = getattr(listener, name, None)
            if callback is not None:
                return callback
        return getattr(self, name, None)

    def _emit_analysis(
        self, owner: str, token: EngineToken, payload: dict[str, Any]
    ) -> None:
        callback = self._route(owner, "_on_analysis")
        if callback is None:
            return
        try:
            callback(token, payload)
        except Exception:  # pragma: no cover - callback isolation
            logger.exception("Analysis callback failed")

    def _emit_bot_move(
        self, owner: str, token: EngineToken, move: str, payload: dict[str, Any]
    ) -> None:
        callback = self._route(owner, "_on_bot_move")
        if callback is None:
            return
        try:
            callback(token, move, payload)
        except Exception:  # pragma: no cover - callback isolation
            logger.exception("Bot callback failed")

    def _emit_error(self, message: str) -> None:
        # The process died for every session: tell the bound listeners too.
        with self._lock:
            listeners = list(self._listeners.values())
        callbacks = [getattr(listener, "_on_error", None) for listener in listeners]
        callbacks.append(self._on_error)
        for callback in callbacks:
            if callback is None:
                continue
            try:
                callback(message)
            except Exception:  # pragma: no cover - callback isolation
                logger.exception("Engine error callback failed")

    def _emit_review_progress(self, owner: str, payload: dict[str, Any]) -> None:
        callback = self._route(owner, "_on_review_progress")
        if callback is None:
            return
        try:
            callback(copy.deepcopy(payload))
        except Exception:  # pragma: no cover - callback isolation
            logger.exception("Review progress callback failed")

    def _emit_review_complete(self, owner: str, payload: dict[str, Any]) -> None:
        callback = self._route(owner, "_on_review_complete")
        if callback is None:
            return
        try:
            callback(copy.deepcopy(payload))
        except Exception:  # pragma: no cover - callback isolation
            logger.exception("Review completion callback failed")

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            status = self._status
            status_label = {
                "ready": "Engine ready",
                "starting": "Starting engine",
                "missing": "Engine missing",
                "error": "Engine error",
            }.get(status, status.title())
            jobs = {
                "queued": len(self._jobs),
                "running": self._current_job.kind if self._current_job else None,
            }
            return {
                "name": self._engine_name,
                "version": self._engine_version,
                "path": str(self._discovery.path) if self._discovery.path else None,
                "source": self._discovery.source,
                "available": self.available,
                "status": status,
                "status_label": status_label,
                "message": self._message or self._discovery.error,
                "last_error": self._last_error,
                "threads": WORKER_THREADS,
                "hash_mb": WORKER_HASH_MB,
                "jobs": jobs,
                "cache": self._cache.stats(),
            }
