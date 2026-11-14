"""Two-pass Stockfish Game Review pipeline and report aggregation.

The pipeline is engine-agnostic: :func:`run_game_review` receives a ``search``
callable, which the Stockfish worker implements.  That keeps every formula in
this module testable with synthetic evaluations and keeps the one-process /
one-owner-thread guarantee in :mod:`chess_master.engine.stockfish`.

Pass 1 analyses the top of the game and grades every non-book ply at the
selected preset (``fast`` 10, ``standard`` 14, ``deep`` 16, MultiPV 3).  Pass 2
re-searches the positions around any Brilliant/Great candidate two plies deeper
(capped at 18) and re-grades the whole line, so a shallow tactical illusion
cannot survive into the final report.

The payload is documented in ``.agents/skills/chess-game-review/SKILL.md``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import chess

from chess_master.analysis.classifier import (
    CLASSIFICATION_LABELS,
    CLASSIFICATION_SYMBOLS,
    MoveContext,
    classify_move,
)
from chess_master.analysis.metrics import (
    CLASSIFICATION_KEYS,
    phase_for_position,
    player_metrics,
)
from chess_master.analysis.openings import OpeningBook, get_default_book
from chess_master.analysis.winprob import (
    MATE_CP,
    clamp_review_cp,
    initial_win_probability,
    model_info,
    move_accuracy_review,
    review_win_probability,
    white_cp_from_mate,
)

__all__ = [
    "CONFIRM_DEPTH_CAP",
    "CONFIRM_DEPTH_BONUS",
    "REVIEW_MULTIPV",
    "REVIEW_PRESETS",
    "REVIEW_SCHEMA_VERSION",
    "ReviewCancelled",
    "ReviewRequest",
    "ReviewSearchResult",
    "line_signature",
    "run_game_review",
]

REVIEW_SCHEMA_VERSION = 2
REVIEW_MULTIPV = 3
CONFIRM_DEPTH_BONUS = 2
CONFIRM_DEPTH_CAP = 18
MAX_CPL = 1000
MAX_CONFIRM_ROUNDS = 3
KEY_MOMENT_CLASSES = ("brilliant", "great", "inaccuracy", "mistake", "miss", "blunder")

REVIEW_PRESETS: dict[str, dict[str, Any]] = {
    "fast": {"label": "Fast", "depth": 10, "confirm_depth": 12, "description": "Quick scan"},
    "standard": {
        "label": "Standard",
        "depth": 14,
        "confirm_depth": 16,
        "description": "Balanced club-level review",
    },
    "deep": {"label": "Deep", "depth": 16, "confirm_depth": 18, "description": "Deep verification"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def line_signature(game_id: str, start_fen: str, moves: Sequence[str]) -> str:
    """Stable identity of the game line (navigation does not change it)."""

    raw = json.dumps(
        [str(game_id), str(start_fen), [str(move) for move in moves]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


class ReviewCancelled(Exception):
    """Raised inside a review search callback when the user cancels."""


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Everything a review needs that is not supplied by the engine."""

    game_id: str
    start_fen: str
    moves: tuple[str, ...]
    preset: str = "standard"
    depth: int | None = None
    white_name: str = "White"
    black_name: str = "Black"
    result: str = "*"
    baseline_white: int | None = None
    baseline_black: int | None = None
    use_book: bool = True
    owner: str = "local"

    def __post_init__(self) -> None:
        if self.preset not in REVIEW_PRESETS:
            raise ValueError(f"Unknown review preset: {self.preset}")
        if not self.moves:
            raise ValueError("A review needs at least one move.")
        if self.depth is not None:
            depth = int(self.depth)
            if depth < 6 or depth > CONFIRM_DEPTH_CAP:
                raise ValueError(f"Review depth must be between 6 and {CONFIRM_DEPTH_CAP}.")
        for value in (self.baseline_white, self.baseline_black):
            if value is not None and (int(value) < 400 or int(value) > 3000):
                raise ValueError("Estimated-Elo baselines must be between 400 and 3000.")
        # Validate the line once, up front, so the worker never starts a
        # multi-minute search on an illegal game.
        board = chess.Board(self.start_fen)
        for uci in self.moves:
            move = chess.Move.from_uci(str(uci))
            if move not in board.legal_moves:
                raise ValueError(f"Illegal move in review line: {uci}")
            board.push(move)

    @property
    def base_depth(self) -> int:
        return int(self.depth) if self.depth is not None else int(REVIEW_PRESETS[self.preset]["depth"])

    @property
    def confirm_depth(self) -> int:
        if self.depth is None:
            return int(REVIEW_PRESETS[self.preset]["confirm_depth"])
        return min(self.base_depth + CONFIRM_DEPTH_BONUS, CONFIRM_DEPTH_CAP)

    @property
    def signature(self) -> str:
        return line_signature(self.game_id, self.start_fen, self.moves)


@dataclass(slots=True)
class ReviewSearchResult:
    """Normalized MultiPV lines for one position, White POV."""

    lines: list[dict[str, Any]]
    depth: int
    multipv: int = REVIEW_MULTIPV
    cached: bool = False
    nodes: int = 0
    time_ms: int = 0
    terminal: str | None = None

    def best(self) -> Mapping[str, Any] | None:
        return self.lines[0] if self.lines else None


SearchCallback = Callable[[chess.Board, int, int], "ReviewSearchResult | None"]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


# ---------------------------------------------------------------------------
# Small line helpers
# ---------------------------------------------------------------------------


def _line_cp_white(line: Mapping[str, Any]) -> int | None:
    score = line.get("score_cp")
    if score is not None:
        return int(score)
    mate = line.get("mate")
    if mate is not None:
        return white_cp_from_mate(int(mate))
    return None


def _line_win_white(line: Mapping[str, Any] | None) -> float | None:
    if line is None:
        return None
    mate = line.get("mate")
    if mate is not None:
        value = int(mate)
        if value > 0:
            return 100.0
        if value < 0:
            return 0.0
        score = line.get("score_cp")
        if score is not None:
            return 100.0 if float(score) > 0 else 0.0 if float(score) < 0 else 50.0
        return 50.0
    win = line.get("win_prob_white")
    if win is not None:
        return _clamp(float(win), 0.0, 100.0)
    score = line.get("score_cp")
    if score is not None:
        return review_win_probability(float(score))
    return None


def _line_mate(line: Mapping[str, Any] | None) -> int | None:
    if line is None or line.get("mate") is None:
        return None
    return int(line["mate"])


def _line_uci(line: Mapping[str, Any] | None) -> str | None:
    if line is None:
        return None
    if line.get("uci"):
        return str(line["uci"])
    best = line.get("best_move")
    if isinstance(best, Mapping) and best.get("uci"):
        return str(best["uci"])
    pv = line.get("pv_uci") or []
    return str(pv[0]) if pv else None


def _line_san(line: Mapping[str, Any] | None, board: chess.Board, uci: str | None) -> str | None:
    if line is None:
        return None
    best = line.get("best_move")
    if isinstance(best, Mapping) and best.get("san"):
        return str(best["san"])
    if line.get("san"):
        return str(line["san"])
    if uci:
        try:
            move = chess.Move.from_uci(uci)
            if move in board.legal_moves:
                return board.san(move)
        except ValueError:
            return None
    return None


def _move_descriptor(board: chess.Board, uci: str | None) -> dict[str, Any] | None:
    """Describe a legal UCI move in the position, or ``None`` when it is not."""

    if not uci:
        return None
    try:
        move = chess.Move.from_uci(str(uci))
    except ValueError:
        return None
    if move not in board.legal_moves:
        return None
    return {
        "uci": move.uci(),
        "san": board.san(move),
        "from": chess.square_name(move.from_square),
        "to": chess.square_name(move.to_square),
        "promotion": chess.piece_symbol(move.promotion) if move.promotion else None,
    }


def _line_pv(line: Mapping[str, Any] | None) -> list[str]:
    """Return the principal variation as a clean list of legal-looking UCI strings."""

    if line is None:
        return []
    pv = line.get("pv_uci") or []
    if not isinstance(pv, Sequence) or isinstance(pv, (str, bytes)):
        return []
    return [str(value) for value in pv if value]


def _followup_payload(
    board_after: chess.Board,
    line: Mapping[str, Any] | None,
    base_ply: int,
) -> dict[str, Any] | None:
    """Turn the engine's continuation after the played move into a playable line.

    The payload carries both SAN and UCI plus every intermediate FEN, so the
    frontend can step through the line without any engine calls or chess rules.
    """

    pv = _line_pv(line)
    if not pv:
        return None

    start_fen = board_after.fen()
    board = board_after.copy(stack=False)
    moves: list[dict[str, Any]] = []
    for offset, uci in enumerate(pv[:8]):
        descriptor = _move_descriptor(board, uci)
        if descriptor is None:
            break
        before = board.fen()
        move_number = board.fullmove_number
        color = "white" if board.turn == chess.WHITE else "black"
        board.push(chess.Move.from_uci(descriptor["uci"]))
        moves.append(
            {
                **descriptor,
                "ply": int(base_ply) + offset + 1,
                "move_number": move_number,
                "color": color,
                "fen_before": before,
                "fen_after": board.fen(),
            }
        )
    if not moves:
        return None

    return {
        "start_fen": start_fen,
        "uci": [move["uci"] for move in moves],
        "san": [move["san"] for move in moves],
        "fens": [start_fen, *[move["fen_after"] for move in moves]],
        "moves": moves,
        "depth": int(line.get("depth") or 0) if line else 0,
        "source": "after_position",
    }


def _terminal_result(board: chess.Board) -> ReviewSearchResult | None:
    """A synthetic top line for a position where Stockfish must not search."""

    if board.is_checkmate():
        white_wins = board.turn == chess.BLACK
        score = MATE_CP if white_wins else -MATE_CP
        line = {
            "rank": 1,
            "score_cp": score,
            "mate": 0,
            "win_prob_white": 100.0 if white_wins else 0.0,
            "uci": None,
            "san": None,
            "pv_uci": [],
            "pv_san": [],
            "best_move": None,
        }
        return ReviewSearchResult(lines=[line], depth=0, terminal="checkmate")
    if (
        board.is_stalemate()
        or board.is_insufficient_material()
        or board.is_seventyfive_moves()
        or board.is_fivefold_repetition()
    ):
        line = {
            "rank": 1,
            "score_cp": 0,
            "mate": None,
            "win_prob_white": 50.0,
            "uci": None,
            "san": None,
            "pv_uci": [],
            "pv_san": [],
            "best_move": None,
        }
        return ReviewSearchResult(lines=[line], depth=0, terminal="draw")
    return None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(float(value), digits) if value is not None else None


def _blank_eval() -> dict[str, Any]:
    return {
        "eval_before": None,
        "eval_after": None,
        "win_before_white": None,
        "win_after_white": None,
        "win_before": None,
        "win_after": None,
        "mate_before": None,
        "mate_after": None,
        "best_uci": None,
        "best_san": None,
        "best_eval": None,
        "best_move": None,
        "pv_followup": None,
        "pv": [],
        "multi_pv": [],
        "depth": 0,
        "cp_loss": None,
        "win_loss": None,
        "accuracy": None,
    }


# ---------------------------------------------------------------------------
# Board and request preparation
# ---------------------------------------------------------------------------


def _replay(request: ReviewRequest) -> list[chess.Board]:
    board = chess.Board(request.start_fen)
    boards = [board.copy(stack=True)]
    for uci in request.moves:
        board.push(chess.Move.from_uci(uci))
        boards.append(board.copy(stack=True))
    return boards


def _is_standard_start(request: ReviewRequest) -> bool:
    return request.start_fen.strip() == chess.STARTING_FEN


def _detect_opening(
    request: ReviewRequest, book: OpeningBook | None
) -> tuple[Any, int]:
    if not request.use_book or not _is_standard_start(request):
        from chess_master.analysis.openings import OpeningInfo

        return OpeningInfo(None, "Unknown opening" if not _is_standard_start(request) else "Starting position", None, 0, "none"), 0
    source = book or get_default_book()
    info = source.detect(request.moves)
    return info, int(info.last_book_ply)


# ---------------------------------------------------------------------------
# Evaluation storage
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _PositionEval:
    result: ReviewSearchResult
    confirmed: bool = False

    @property
    def best(self) -> Mapping[str, Any] | None:
        return self.result.best()


def _store_eval(
    evals: dict[int, _PositionEval],
    index: int,
    result: ReviewSearchResult,
    *,
    confirmed: bool = False,
) -> None:
    existing = evals.get(index)
    if existing is not None and int(existing.result.depth) > int(result.depth):
        return
    if existing is not None and existing.result.depth == result.depth and existing.confirmed:
        return
    evals[index] = _PositionEval(result=result, confirmed=confirmed)


def _win_white(evals: Mapping[int, _PositionEval], index: int) -> float | None:
    entry = evals.get(index)
    if entry is None:
        return None
    return _line_win_white(entry.best)


# ---------------------------------------------------------------------------
# Move records and classification
# ---------------------------------------------------------------------------


def _base_record(
    ply: int,
    boards: Sequence[chess.Board],
    request: ReviewRequest,
    opening: Any,
    last_book_ply: int,
) -> dict[str, Any]:
    board_before = boards[ply - 1]
    board_after = boards[ply]
    move = chess.Move.from_uci(request.moves[ply - 1])
    is_book = ply <= last_book_ply
    record: dict[str, Any] = {
        "ply": ply,
        "move_number": board_before.fullmove_number,
        "color": "white" if board_before.turn == chess.WHITE else "black",
        "san": board_before.san(move),
        "uci": move.uci(),
        "from": chess.square_name(move.from_square),
        "to": chess.square_name(move.to_square),
        "fen_before": board_before.fen(),
        "fen_after": board_after.fen(),
        "is_book": bool(is_book),
        "is_forced": board_before.legal_moves.count() == 1,
        "legal_moves": board_before.legal_moves.count(),
        "phase": phase_for_position(board_before, ply, last_book_ply),
        "opening_eco": opening.eco if is_book else None,
        "opening_name": opening.name if is_book else None,
    }
    record.update(_blank_eval())
    return record


def _apply_evals(
    record: dict[str, Any],
    boards: Sequence[chess.Board],
    evals: Mapping[int, _PositionEval],
) -> None:
    ply = int(record["ply"])
    before = evals.get(ply - 1)
    after = evals.get(ply)
    if before is None or after is None:
        return
    board_before = boards[ply - 1]

    best_before = before.best
    best_after = after.best
    cp_before = _line_cp_white(best_before) if best_before else None
    cp_after = _line_cp_white(best_after) if best_after else None
    win_white_before = _line_win_white(best_before)
    win_white_after = _line_win_white(best_after)
    if win_white_before is None or win_white_after is None:
        return

    mover_white = record["color"] == "white"
    win_before = win_white_before if mover_white else 100.0 - win_white_before
    win_after = win_white_after if mover_white else 100.0 - win_white_after
    win_loss = max(0.0, win_before - win_after)
    cp_loss = None
    if cp_before is not None and cp_after is not None:
        delta_white = clamp_review_cp(cp_before) - clamp_review_cp(cp_after)
        cp_loss = max(
            0.0,
            min(MAX_CPL, delta_white if mover_white else -delta_white),
        )
    # The engine's top choice is the reference move by definition. Shallow
    # eval drift between the before/after searches must not charge it a loss.
    if _line_uci(best_before) == record["uci"]:
        win_loss = 0.0
        cp_loss = 0.0

    best_uci = _line_uci(best_before)
    best_move = _move_descriptor(board_before, best_uci)
    if best_move is not None:
        best_move.update(
            {
                "score_cp": clamp_review_cp(cp_before) if cp_before is not None else None,
                "mate": _line_mate(best_before),
                "win_prob_white": round(win_white_before, 2),
                "is_played": best_uci == record["uci"],
                "rank": 1,
            }
        )
    board_after = boards[ply]
    record.update(
        {
            "eval_before": clamp_review_cp(cp_before) if cp_before is not None else None,
            "eval_after": clamp_review_cp(cp_after) if cp_after is not None else None,
            "win_before_white": round(win_white_before, 2),
            "win_after_white": round(win_white_after, 2),
            "win_before": round(win_before, 2),
            "win_after": round(win_after, 2),
            "mate_before": _line_mate(best_before),
            "mate_after": _line_mate(best_after),
            "best_uci": best_uci,
            "best_san": _line_san(best_before, board_before, best_uci),
            "best_eval": clamp_review_cp(cp_before) if cp_before is not None else None,
            "best_move": best_move,
            "pv_followup": _followup_payload(board_after, best_after, ply),
            "pv": list(best_before.get("pv_uci") or []) if best_before else [],
            "multi_pv": [
                {
                    "rank": line.get("rank", index + 1),
                    "uci": _line_uci(line),
                    "san": _line_san(line, board_before, _line_uci(line)),
                    "win_prob_white": round(float(line["win_prob_white"]), 2)
                    if line.get("win_prob_white") is not None
                    else _round(_line_win_white(line)),
                    "score_cp": _line_cp_white(line),
                    "mate": _line_mate(line),
                }
                for index, line in enumerate(before.result.lines[:REVIEW_MULTIPV])
            ],
            "depth": int(before.result.depth),
            "cp_loss": round(cp_loss, 2) if cp_loss is not None else None,
            "win_loss": round(win_loss, 2),
            "accuracy": round(move_accuracy_review(win_loss), 2),
        }
    )


def _book_record(record: dict[str, Any]) -> None:
    opening = record.get("opening_name") or "opening theory"
    eco = f" ({record['opening_eco']})" if record.get("opening_eco") else ""
    record.update(
        {
            "classification": "book",
            "symbol": CLASSIFICATION_SYMBOLS["book"],
            "label": CLASSIFICATION_LABELS["book"],
            "explanation": f"Book move — follows {opening}{eco}.",
            "sacrifice_info": None,
            "brilliant_gates": {},
            "multi_pv_cliff": None,
            "played_rank": None,
            "needs_confirmation": False,
            "confirmed": True,
        }
    )


def _classify_record(
    record: dict[str, Any],
    boards: Sequence[chess.Board],
    evals: Mapping[int, _PositionEval],
    previous: Mapping[str, Any] | None,
    before_previous: Mapping[str, Any] | None,
    *,
    confirm_position: bool,
) -> None:
    ply = int(record["ply"])
    board_before = boards[ply - 1]
    move = chess.Move.from_uci(record["uci"])
    win_before = record.get("win_before")
    win_after = record.get("win_after")
    if win_before is None or win_after is None:
        raise ValueError(f"No evaluation for non-book ply {ply}")

    entry = evals.get(ply - 1)
    lines = entry.result.lines if entry is not None else []
    confirmation = lines if confirm_position and entry is not None else None

    previous_classification = None
    previous_move_number = None
    pre_error = None
    if previous is not None:
        previous_classification = previous.get("classification")
        previous_move_number = previous.get("move_number")
        if (
            previous_classification in {"mistake", "blunder"}
            and before_previous is not None
            and before_previous.get("win_after") is not None
        ):
            pre_error = float(before_previous["win_after"])

    context = MoveContext(
        board_before=board_before.copy(stack=False),
        move=move,
        win_before_mover=float(win_before),
        win_after_mover=float(win_after),
        lines_before=lines,
        is_book=bool(record.get("is_book")),
        legal_move_count=int(record.get("legal_moves") or 0),
        confirmation_lines=confirmation,
        previous_move_classification=previous_classification,
        previous_move_number=previous_move_number,
        win_pre_opponent_error=pre_error,
        mate_before=record.get("mate_before"),
        mate_after=record.get("mate_after"),
        opening_name=record.get("opening_name"),
        opening_eco=record.get("opening_eco"),
    )
    result = classify_move(context)
    record.update(result.to_dict())


def _classify_all(
    records: list[dict[str, Any]],
    boards: Sequence[chess.Board],
    evals: Mapping[int, _PositionEval],
    confirmed_positions: set[int],
) -> None:
    for index, record in enumerate(records):
        if record["is_book"]:
            _book_record(record)
            continue
        _apply_evals(record, boards, evals)
        previous = records[index - 1] if index > 0 else None
        before_previous = records[index - 2] if index > 1 else None
        _classify_record(
            record,
            boards,
            evals,
            previous,
            before_previous,
            confirm_position=(int(record["ply"]) - 1) in confirmed_positions,
        )


# ---------------------------------------------------------------------------
# Progress and timing
# ---------------------------------------------------------------------------


class _Progress:
    def __init__(self, total: int, callback: ProgressCallback | None) -> None:
        self.total = max(1, int(total))
        self.done = 0
        self.phase = "scan"
        self.callback = callback

    def add_total(self, extra: int) -> None:
        self.total += max(0, int(extra))

    def advance(self, phase: str | None = None) -> None:
        self.done += 1
        if phase:
            self.phase = phase
        self.emit()

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.emit()

    def emit(self) -> None:
        if self.callback is None:
            return
        payload = {
            "done": self.done,
            "total": max(self.total, self.done),
            "phase": self.phase,
            "percent": round(min(100.0, 100.0 * self.done / max(1, self.total)), 2),
        }
        try:
            self.callback(payload)
        except Exception:  # pragma: no cover - progress must never kill a review
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_game_review(
    request: ReviewRequest,
    search: SearchCallback,
    *,
    opening_book: OpeningBook | None = None,
    should_cancel: CancelCallback | None = None,
    on_progress: ProgressCallback | None = None,
    engine_name: str = "Stockfish",
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Run the two-pass review and return the versioned report payload."""

    if not isinstance(request, ReviewRequest):
        raise TypeError("run_game_review expects a ReviewRequest")

    started = time.perf_counter()
    started_at = _utc_now()
    boards = _replay(request)
    total_plies = len(request.moves)
    opening, last_book_ply = _detect_opening(request, opening_book)
    graded_plies = [ply for ply in range(1, total_plies + 1) if ply > last_book_ply]

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def check_cancelled() -> None:
        if cancelled():
            raise ReviewCancelled("Review cancelled")

    # Only positions from the end of the book through the final position need
    # an engine search: every one of them is the "before" or "after" of a
    # graded move.
    needed_positions = list(range(last_book_ply, total_plies + 1)) if graded_plies else []
    progress = _Progress(len(needed_positions) or 1, on_progress)
    if not needed_positions:
        # A 100% book game has nothing to search; report completion at once.
        progress.done = progress.total = 1
        progress.phase = "complete"
    progress.emit()

    evals: dict[int, _PositionEval] = {}
    confirmed_positions: set[int] = set()
    searches = 0
    cache_hits = 0
    search_time_ms = 0
    terminal_positions = 0

    base_depth = request.base_depth
    confirm_depth = request.confirm_depth

    # -- pass 1 ---------------------------------------------------------
    for index in needed_positions:
        check_cancelled()
        terminal = _terminal_result(boards[index])
        if terminal is not None:
            terminal_positions += 1
            _store_eval(evals, index, terminal)
            progress.advance("scan")
            continue
        result = search(boards[index], base_depth, REVIEW_MULTIPV)
        if result is None:
            raise ReviewCancelled("Review cancelled")
        searches += 1
        search_time_ms += max(0, int(result.time_ms))
        if result.cached:
            cache_hits += 1
        _store_eval(evals, index, result)
        progress.advance("scan")

    # -- initial grading -------------------------------------------------
    records = [_base_record(ply, boards, request, opening, last_book_ply) for ply in range(1, total_plies + 1)]
    _classify_all(records, boards, evals, confirmed_positions)

    # -- pass 2: confirm Brilliant/Great candidates ----------------------
    if confirm_depth > base_depth:
        for _round in range(MAX_CONFIRM_ROUNDS):
            check_cancelled()
            pending = [
                record
                for record in records
                if record.get("needs_confirmation") and not record.get("is_book")
            ]
            if not pending:
                break
            wanted: set[int] = set()
            for record in pending:
                ply = int(record["ply"])
                wanted.add(ply - 1)
                wanted.add(ply)
            wanted -= confirmed_positions
            wanted = {index for index in wanted if 0 <= index <= total_plies}
            if not wanted:
                break
            progress.add_total(len(wanted))
            progress.set_phase("confirm")
            for index in sorted(wanted):
                check_cancelled()
                terminal = _terminal_result(boards[index])
                if terminal is not None:
                    terminal_positions += 1
                    _store_eval(evals, index, terminal, confirmed=True)
                    confirmed_positions.add(index)
                    progress.advance("confirm")
                    continue
                result = search(boards[index], confirm_depth, REVIEW_MULTIPV)
                if result is None:
                    raise ReviewCancelled("Review cancelled")
                searches += 1
                search_time_ms += max(0, int(result.time_ms))
                if result.cached:
                    cache_hits += 1
                _store_eval(evals, index, result, confirmed=True)
                confirmed_positions.add(index)
                progress.advance("confirm")
            _classify_all(records, boards, evals, confirmed_positions)

    # A candidate that could not be re-searched (custom depth already at the
    # cap) stays visibly unconfirmed rather than silently pretending.
    for record in records:
        if record.get("needs_confirmation"):
            record["confirmed"] = False

    # -- win sequence and summaries --------------------------------------
    win_probs_white = _win_sequence(request, total_plies, evals)
    white = player_metrics(
        "white",
        records,
        win_probs_white,
        baseline_elo=request.baseline_white,
    )
    black = player_metrics(
        "black",
        records,
        win_probs_white,
        baseline_elo=request.baseline_black,
    )

    elapsed_ms = int(max(0.0, time.perf_counter() - started) * 1000)
    progress.phase = "complete"
    progress.done = progress.total
    progress.emit()

    counts = {key: 0 for key in CLASSIFICATION_KEYS}
    for record in records:
        key = record.get("classification")
        if key in counts:
            counts[key] += 1

    report: dict[str, Any] = {
        "available": True,
        "schema_version": REVIEW_SCHEMA_VERSION,
        "phase": 4,
        "status": "complete",
        "game_id": request.game_id,
        "signature": request.signature,
        "preset": request.preset,
        "preset_label": REVIEW_PRESETS[request.preset]["label"],
        "engine": {
            "name": engine_name,
            "version": engine_version,
            "depth": base_depth,
            "confirm_depth": confirm_depth,
            "multipv": REVIEW_MULTIPV,
        },
        "model": model_info(),
        "players": {"white": request.white_name, "black": request.black_name},
        "result": request.result,
        "opening": opening.to_dict(),
        "totals": {
            "plies": total_plies,
            "book_moves": sum(1 for record in records if record["is_book"]),
            "forced_moves": sum(1 for record in records if record["is_forced"]),
            "graded_moves": sum(1 for record in records if not record["is_book"]),
            "searched_positions": len(needed_positions),
            "confirmed_positions": len(confirmed_positions),
            "counts": counts,
        },
        "summary": {
            "white": white,
            "black": black,
            "accuracy": {"white": white["accuracy"], "black": black["accuracy"]},
            "acpl": {"white": white["acpl"], "black": black["acpl"]},
            "acpl_trimmed": {"white": white["acpl_trimmed"], "black": black["acpl_trimmed"]},
            "estimated_elo": {
                "white": white["estimated_elo"]["elo"],
                "black": black["estimated_elo"]["elo"],
            },
            "counts": counts,
        },
        "eval_chart": _chart_payload(request, records, win_probs_white, evals),
        "key_moments": _key_moments(records),
        "progress": {
            "done": progress.done,
            "total": progress.total,
            "phase": "complete",
            "percent": 100.0,
        },
        "timing": {
            "started_at": started_at,
            "finished_at": _utc_now(),
            "elapsed_ms": elapsed_ms,
            "search_time_ms": search_time_ms,
            "searches": searches,
            "cache_hits": cache_hits,
            "terminal_positions": terminal_positions,
            "avg_search_ms": round(search_time_ms / searches, 1) if searches else 0.0,
        },
        "moves": records,
    }
    return report


def _win_sequence(
    request: ReviewRequest,
    total_plies: int,
    evals: Mapping[int, _PositionEval],
) -> list[float]:
    """White-POV win percentages for positions ``0..N``.

    Position 0 uses the canonical ``+15 cp`` prior on the standard start.  A
    book prefix has no searches; those positions carry the initial prior (or
    the first available evaluation for a custom position) so the volatility
    window stays continuous.
    """

    values: list[float | None] = [None] * (total_plies + 1)
    if _is_standard_start(request):
        values[0] = initial_win_probability()
    else:
        first = _win_white(evals, 0)
        values[0] = first if first is not None else 50.0

    for index in range(1, total_plies + 1):
        known = _win_white(evals, index)
        if known is not None:
            values[index] = known
        else:
            values[index] = values[index - 1]

    # A custom start whose first searched position is later than 0: back-fill
    # the book prefix from the first real evaluation.
    first_known = next((value for value in values if value is not None), 50.0)
    return [float(value if value is not None else first_known) for value in values]


def _chart_payload(
    request: ReviewRequest,
    records: Sequence[Mapping[str, Any]],
    win_probs_white: Sequence[float],
    evals: Mapping[int, _PositionEval],
) -> dict[str, Any]:
    """Position-by-position advantage curve for the interactive SVG chart."""

    if _is_standard_start(request):
        start_cp: int | None = 15
        start_mate: int | None = None
    else:
        entry = evals.get(0)
        start_cp = _line_cp_white(entry.best) if entry and entry.best else None
        start_mate = _line_mate(entry.best) if entry else None

    points: list[dict[str, Any]] = [
        {
            "ply": 0,
            "win_prob_white": round(float(win_probs_white[0]), 2),
            "eval_cp": clamp_review_cp(start_cp) if start_cp is not None else None,
            "mate": start_mate,
            "classification": None,
            "san": None,
            "uci": None,
            "move_number": None,
            "color": None,
            "is_book": False,
            "accuracy": None,
            "win_loss": None,
        }
    ]
    last_cp = start_cp
    last_mate = start_mate
    for record in records:
        if record.get("eval_after") is not None:
            last_cp = int(record["eval_after"])
            last_mate = record.get("mate_after")
        ply = int(record["ply"])
        points.append(
            {
                "ply": ply,
                "win_prob_white": round(float(win_probs_white[ply]), 2),
                "eval_cp": last_cp,
                "mate": last_mate,
                "classification": record.get("classification"),
                "san": record.get("san"),
                "uci": record.get("uci"),
                "move_number": record.get("move_number"),
                "color": record.get("color"),
                "is_book": bool(record.get("is_book")),
                "accuracy": record.get("accuracy"),
                "win_loss": record.get("win_loss"),
            }
        )
    return {"total_plies": len(records), "midline": 50.0, "points": points}


def _key_moments(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turning points the review panel can jump between with one key."""

    moments: list[dict[str, Any]] = []
    for record in records:
        classification = str(record.get("classification") or "")
        if classification not in KEY_MOMENT_CLASSES:
            continue
        moments.append(
            {
                "ply": int(record["ply"]),
                "move_number": record.get("move_number"),
                "color": record.get("color"),
                "san": record.get("san"),
                "uci": record.get("uci"),
                "classification": classification,
                "symbol": record.get("symbol"),
                "label": record.get("label"),
                "win_loss": record.get("win_loss"),
                "win_before": record.get("win_before"),
                "win_after": record.get("win_after"),
                "eval_before": record.get("eval_before"),
                "eval_after": record.get("eval_after"),
                "accuracy": record.get("accuracy"),
            }
        )
    return moments
