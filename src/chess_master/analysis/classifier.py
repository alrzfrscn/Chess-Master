"""Deterministic 10-tier move classifier and six-gate Brilliant verifier.

The public labels are the ten canonical Chess.com keys. ``is_forced`` is
metadata, not an eleventh label: a position with exactly one legal move rolls
up into ``best`` in the summary while remaining visually distinct in the move
list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import chess

from chess_master.analysis.winprob import review_win_probability

__all__ = [
    "CLASSIFICATION_LABELS",
    "CLASSIFICATION_ORDER",
    "CLASSIFICATION_SYMBOLS",
    "MoveContext",
    "ClassificationResult",
    "classify_move",
    "find_sacrifice",
    "static_exchange_evaluation",
]

CLASSIFICATION_ORDER = (
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "book",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
)

CLASSIFICATION_SYMBOLS = {
    "brilliant": "!!",
    "great": "!",
    "best": "★",
    "excellent": "✓",
    "good": "○",
    "book": "📖",
    "inaccuracy": "?!",
    "mistake": "?",
    "miss": "✕",
    "blunder": "??",
}

CLASSIFICATION_LABELS = {
    "brilliant": "Brilliant",
    "great": "Great",
    "best": "Best",
    "excellent": "Excellent",
    "good": "Good",
    "book": "Book",
    "inaccuracy": "Inaccuracy",
    "mistake": "Mistake",
    "miss": "Miss",
    "blunder": "Blunder",
}

_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

_PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def _value(piece: chess.Piece | None) -> int:
    return 0 if piece is None else _PIECE_VALUES[piece.piece_type]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _line_uci(line: Mapping[str, Any]) -> str | None:
    if line.get("uci"):
        return str(line["uci"])
    best = line.get("best_move")
    if isinstance(best, Mapping) and best.get("uci"):
        return str(best["uci"])
    pv = line.get("pv_uci") or []
    if pv:
        return str(pv[0])
    return None


def _line_win_white(line: Mapping[str, Any]) -> float | None:
    mate = line.get("mate")
    if mate is not None:
        if int(mate) > 0:
            return 100.0
        if int(mate) < 0:
            return 0.0
        score = line.get("score_cp")
        if score is not None:
            return 100.0 if float(score) > 0 else 0.0
        return 50.0
    score = line.get("score_cp")
    if score is not None:
        return review_win_probability(float(score))
    if line.get("win_prob_white") is not None:
        return _clamp(float(line["win_prob_white"]), 0.0, 100.0)
    return None


def _line_mover_win(line: Mapping[str, Any], mover_color: chess.Color) -> float | None:
    white = _line_win_white(line)
    if white is None:
        return None
    return white if mover_color == chess.WHITE else 100.0 - white


def static_exchange_evaluation(board: chess.Board, square: int, side: chess.Color) -> int:
    """A small SEE for the material the side to move can win on ``square``.

    Only legal captures are considered. The result is in pawns and never
    negative because the side may decline the exchange.
    """

    attackers = board.attackers(side, square)
    target = board.piece_at(square)
    if not attackers or target is None:
        return 0
    from_square = min(
        attackers,
        key=lambda value: (_value(board.piece_at(value)), value),
    )
    piece = board.piece_at(from_square)
    if piece is None:
        return 0
    promotion = None
    if piece.piece_type == chess.PAWN and chess.square_rank(square) in (0, 7):
        promotion = chess.QUEEN
    move = chess.Move(from_square, square, promotion=promotion)
    if move not in board.legal_moves:
        return 0
    captured_value = _value(target)
    board.push(move)
    try:
        reply_gain = static_exchange_evaluation(board, square, not side)
    finally:
        board.pop()
    return max(0, captured_value - reply_gain)


def _captured_value_by_move(board: chess.Board, move: chess.Move) -> int:
    captured = board.piece_at(move.to_square)
    if captured is not None:
        return _value(captured)
    if board.is_en_passant(move):
        return _PIECE_VALUES[chess.PAWN]
    return 0


def _exposed_on(board: chess.Board, square: int, color: chess.Color) -> bool:
    piece = board.piece_at(square)
    if piece is None or piece.color != color or piece.piece_type in (chess.PAWN, chess.KING):
        return False
    opponent = not color
    attackers = board.attackers(opponent, square)
    if not attackers:
        return False
    defenders = board.attackers(color, square)
    if not defenders:
        return True
    lower_value_attacker = any(
        _value(board.piece_at(attacker_square)) < _value(piece) for attacker_square in attackers
    )
    return bool(lower_value_attacker and static_exchange_evaluation(board, square, opponent) > 0)


def find_sacrifice(
    board_before: chess.Board,
    move: chess.Move,
) -> dict[str, Any] | None:
    """Gate 2: does the move leave a friendly non-pawn piece genuinely en prise?

    The moved piece is considered first. Other friendly non-pawn pieces count
    only when the move newly exposes them. A candidate must be attacked, the
    material exchange must be negative for the mover, and the value captured by
    the move must be strictly less than the sacrificed piece. Gate 3 (the
    opponent's capture is legal) is reported separately so pinned attackers can
    reject a false Brilliant.
    """

    board_after = board_before.copy(stack=False)
    board_after.push(move)
    mover = board_before.turn
    opponent = not mover
    captured_value = _captured_value_by_move(board_before, move)

    candidates: list[tuple[int, chess.Piece, bool]] = []
    moved_piece = board_after.piece_at(move.to_square)
    if (
        moved_piece is not None
        and moved_piece.color == mover
        and moved_piece.piece_type not in (chess.PAWN, chess.KING)
    ):
        candidates.append((move.to_square, moved_piece, True))
    for square, piece in board_after.piece_map().items():
        if piece.color != mover or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        if square == move.to_square:
            continue
        if _exposed_on(board_after, square, mover) and not _exposed_on(board_before, square, mover):
            candidates.append((square, piece, False))

    candidates.sort(
        key=lambda item: (_PIECE_VALUES[item[1].piece_type], item[2]),
        reverse=True,
    )

    for square, piece, moved in candidates:
        if captured_value >= _PIECE_VALUES[piece.piece_type]:
            continue
        attackers = board_after.attackers(opponent, square)
        defenders = board_after.attackers(mover, square)
        if not attackers:
            continue
        lower_value_attackers = [
            attacker_square
            for attacker_square in attackers
            if _value(board_after.piece_at(attacker_square)) < _PIECE_VALUES[piece.piece_type]
        ]
        undefended = len(defenders) == 0
        negative_exchange = bool(
            lower_value_attackers
            and static_exchange_evaluation(board_after, square, opponent) > 0
        )
        if not (undefended or negative_exchange):
            continue

        legal_captures = [
            capture
            for capture in board_after.legal_moves
            if capture.to_square == square
            and board_after.piece_at(capture.from_square) is not None
            and board_after.piece_at(capture.from_square).color == opponent
        ]
        attacker_choices = [capture.from_square for capture in legal_captures] or list(attackers)
        attacker_square = min(
            attacker_choices,
            key=lambda value: (_value(board_after.piece_at(value)), value),
        )
        return {
            "is_sacrifice": True,
            "piece": _PIECE_NAMES[piece.piece_type],
            "piece_type": piece.piece_type,
            "piece_value": _PIECE_VALUES[piece.piece_type],
            "square": chess.square_name(square),
            "attacker_square": chess.square_name(attacker_square),
            "attacker_value": _value(board_after.piece_at(attacker_square)),
            "moved_piece": moved,
            "captured_value": captured_value,
            "legal_capture": bool(legal_captures),
            "undefended": undefended,
            "negative_exchange": negative_exchange,
        }
    return None


@dataclass(slots=True)
class MoveContext:
    """Everything the deterministic classifier is allowed to consult."""

    board_before: chess.Board
    move: chess.Move
    win_before_mover: float
    win_after_mover: float
    lines_before: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    played_rank: int | None = None
    legal_move_count: int = 0
    is_book: bool = False
    is_forced: bool = False
    confirmation_lines: Sequence[Mapping[str, Any]] | None = None
    previous_move_classification: str | None = None
    previous_move_number: int | None = None
    win_pre_opponent_error: float | None = None
    mate_before: int | None = None
    mate_after: int | None = None
    opening_name: str | None = None
    opening_eco: str | None = None

    def __post_init__(self) -> None:
        if self.legal_move_count <= 0:
            self.legal_move_count = self.board_before.legal_moves.count()
        if self.is_forced != (self.legal_move_count == 1):
            self.is_forced = self.legal_move_count == 1
        if self.played_rank is None:
            played = self.move.uci()
            for index, line in enumerate(self.lines_before, start=1):
                if _line_uci(line) == played:
                    self.played_rank = index
                    break

    @property
    def win_loss(self) -> float:
        return max(0.0, float(self.win_before_mover) - float(self.win_after_mover))

    @property
    def mover_color(self) -> chess.Color:
        return self.board_before.turn


@dataclass(slots=True)
class ClassificationResult:
    classification: str
    is_forced: bool
    explanation: str
    sacrifice_info: dict[str, Any] | None = None
    brilliant_gates: dict[str, bool] = field(default_factory=dict)
    multi_pv_cliff: float | None = None
    played_rank: int | None = None
    needs_confirmation: bool = False
    confirmed: bool = True

    @property
    def symbol(self) -> str:
        return CLASSIFICATION_SYMBOLS[self.classification]

    @property
    def label(self) -> str:
        return CLASSIFICATION_LABELS[self.classification]

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "symbol": self.symbol,
            "label": self.label,
            "is_forced": self.is_forced,
            "explanation": self.explanation,
            "sacrifice_info": self.sacrifice_info,
            "brilliant_gates": dict(self.brilliant_gates),
            "multi_pv_cliff": round(self.multi_pv_cliff, 2)
            if self.multi_pv_cliff is not None
            else None,
            "played_rank": self.played_rank,
            "needs_confirmation": self.needs_confirmation,
            "confirmed": self.confirmed,
        }


def _result(
    classification: str,
    context: MoveContext,
    explanation: str,
    *,
    sacrifice_info: dict[str, Any] | None = None,
    gates: dict[str, bool] | None = None,
    cliff: float | None = None,
    needs_confirmation: bool = False,
    confirmed: bool = True,
) -> ClassificationResult:
    return ClassificationResult(
        classification=classification,
        is_forced=bool(context.is_forced),
        explanation=explanation,
        sacrifice_info=sacrifice_info,
        brilliant_gates=gates or {},
        multi_pv_cliff=cliff,
        played_rank=context.played_rank,
        needs_confirmation=needs_confirmation,
        confirmed=confirmed,
    )


def _confirmation_lines(context: MoveContext) -> tuple[Sequence[Mapping[str, Any]], bool]:
    if context.confirmation_lines is None:
        return context.lines_before, False
    return context.confirmation_lines, True


def _rank_in(lines: Sequence[Mapping[str, Any]], move: chess.Move) -> int | None:
    wanted = move.uci()
    for index, line in enumerate(lines, start=1):
        if _line_uci(line) == wanted:
            return index
    return None


def _multi_pv_cliff(context: MoveContext, lines: Sequence[Mapping[str, Any]]) -> float | None:
    if len(lines) < 2:
        return None
    first = _line_mover_win(lines[0], context.mover_color)
    second = _line_mover_win(lines[1], context.mover_color)
    if first is None or second is None:
        return None
    return max(0.0, float(first) - float(second))


def _material_unavoidably_lost(board_before: chess.Board, sacrifice: Mapping[str, Any]) -> bool:
    """True when the sacrificed piece was already hanging and every move drops it."""

    square = chess.parse_square(str(sacrifice["square"]))
    piece = board_before.piece_at(square)
    already_exposed = bool(piece is not None and _exposed_on(board_before, square, board_before.turn))
    if not already_exposed:
        return False

    mover = board_before.turn
    opponent = not mover
    threshold = int(sacrifice.get("piece_value") or 1)
    for move in board_before.legal_moves:
        after = board_before.copy(stack=False)
        after.push(move)
        best_gain = 0
        for target_square, target_piece in after.piece_map().items():
            if target_piece.color != mover or target_piece.piece_type in (chess.PAWN, chess.KING):
                continue
            if after.attackers(opponent, target_square):
                best_gain = max(
                    best_gain,
                    static_exchange_evaluation(after, target_square, opponent),
                )
        if best_gain < threshold:
            return False
    return True


def _has_non_sacrificial_alternative(
    context: MoveContext,
    sacrifice: Mapping[str, Any],
) -> bool:
    """Gate 4 support: another MultiPV line avoids the sacrifice but scores worse."""

    if len(context.lines_before) < 2:
        return False
    played_win = float(context.win_after_mover)
    for line in context.lines_before[1:3]:
        uci = _line_uci(line)
        if not uci:
            continue
        try:
            alternative = chess.Move.from_uci(uci)
        except ValueError:
            continue
        if alternative not in context.board_before.legal_moves:
            continue
        other_sacrifice = find_sacrifice(context.board_before, alternative)
        line_win = _line_mover_win(line, context.mover_color)
        if other_sacrifice is None and line_win is not None and line_win <= played_win + 0.001:
            return True
    return False


def _is_trivial_capture(board: chess.Board, move: chess.Move) -> bool:
    captured = board.piece_at(move.to_square)
    if captured is None:
        return False
    defender_color = not board.turn
    return len(board.attackers(defender_color, move.to_square)) == 0


def _brilliant_gates(context: MoveContext) -> tuple[dict[str, bool], dict[str, Any] | None, float | None]:
    lines, confirmed = _confirmation_lines(context)
    rank = _rank_in(lines, context.move)
    gate1 = bool(
        not context.is_book
        and (
            rank == 1
            or (rank is not None and rank <= 3 and context.win_loss < 2.0)
        )
    )
    sacrifice = find_sacrifice(context.board_before, context.move)
    gate2 = bool(sacrifice is not None)
    gate3 = bool(sacrifice is not None and sacrifice.get("legal_capture"))
    gate4 = bool(
        sacrifice is not None
        and (
            _has_non_sacrificial_alternative(context, sacrifice)
            or not _material_unavoidably_lost(context.board_before, sacrifice)
        )
    )
    gate5 = bool(context.win_after_mover >= 50.0 and context.win_before_mover <= 90.0)
    gate6 = bool(
        not context.board_before.is_check()
        and context.move.promotion != chess.QUEEN
        and context.legal_move_count >= 2
    )
    gates = {
        "gate_1_engine_optimality": gate1,
        "gate_2_board_exchange": gate2,
        "gate_3_legal_capture": gate3,
        "gate_4_not_trapped_piece": gate4,
        "gate_5_floor_and_ceiling": gate5,
        "gate_6_structural": gate6,
        "pass_2_confirmed": confirmed,
    }
    return gates, sacrifice if gate2 and gate3 else None, None


def _great_gates(
    context: MoveContext,
) -> tuple[bool, float | None, bool]:
    lines, confirmed = _confirmation_lines(context)
    rank = _rank_in(lines, context.move)
    cliff = _multi_pv_cliff(context, lines)
    optimal = bool(rank == 1 or context.win_loss <= 1.0)
    cliff_ok = bool(cliff is not None and cliff >= 10.0)
    position_ok = bool(context.win_before_mover <= 90.0 and context.win_after_mover >= 50.0)
    non_trivial = not _is_trivial_capture(context.board_before, context.move)
    return (
        bool(
            not context.is_book
            and context.legal_move_count >= 2
            and optimal
            and cliff_ok
            and position_ok
            and non_trivial
        ),
        cliff,
        confirmed,
    )


def _missed_forced_mate(context: MoveContext) -> bool:
    mate = context.mate_before
    if mate is None or int(mate) == 0:
        return False
    mover_has_mate = (context.mover_color == chess.WHITE and int(mate) > 0) or (
        context.mover_color == chess.BLACK and int(mate) < 0
    )
    if not mover_has_mate:
        return False
    after = context.mate_after
    if after is None:
        return True
    return not (
        (context.mover_color == chess.WHITE and int(after) > 0)
        or (context.mover_color == chess.BLACK and int(after) < 0)
    )


def _walked_into_mate(context: MoveContext) -> bool:
    after = context.mate_after
    if after is None:
        return False
    opponent_has_mate = (context.mover_color == chess.WHITE and int(after) < 0) or (
        context.mover_color == chess.BLACK and int(after) > 0
    )
    return bool(opponent_has_mate and context.win_before_mover >= 50.0)


def _dropped_forced_mate(context: MoveContext) -> bool:
    before = context.mate_before
    if before is None or int(before) == 0:
        return False
    mover_has_mate = (context.mover_color == chess.WHITE and int(before) > 0) or (
        context.mover_color == chess.BLACK and int(before) < 0
    )
    return bool(mover_has_mate and context.win_after_mover < 50.0)


def _best_san(context: MoveContext) -> str | None:
    if not context.lines_before:
        return None
    line = context.lines_before[0]
    best = line.get("best_move")
    if isinstance(best, Mapping) and best.get("san"):
        return str(best["san"])
    san = line.get("san")
    return str(san) if san else None


def classify_move(context: MoveContext) -> ClassificationResult:
    """Apply the canonical order and return exactly one of the ten labels."""

    is_forced = bool(context.is_forced)
    if context.is_book:
        opening = context.opening_name or "opening theory"
        eco = f" ({context.opening_eco})" if context.opening_eco else ""
        return _result(
            "book",
            context,
            f"Book move — follows {opening}{eco}.",
        )

    if is_forced:
        return _result(
            "best",
            context,
            "Forced move — it is the only legal move in the position.",
        )

    gates, sacrifice, _ = _brilliant_gates(context)
    base_gate_keys = (
        "gate_1_engine_optimality",
        "gate_2_board_exchange",
        "gate_3_legal_capture",
        "gate_4_not_trapped_piece",
        "gate_5_floor_and_ceiling",
        "gate_6_structural",
    )
    confirmed = gates["pass_2_confirmed"]
    if all(gates[key] for key in base_gate_keys):
        piece = sacrifice["piece"] if sacrifice else "piece"
        square = sacrifice["square"] if sacrifice else chess.square_name(context.move.to_square)
        explanation = (
            f"Brilliant {piece} sacrifice on {square}! Leaves the {piece} en prise "
            "to seize the initiative."
        )
        return _result(
            "brilliant",
            context,
            explanation,
            sacrifice_info=sacrifice,
            gates=gates,
            needs_confirmation=not confirmed,
            confirmed=confirmed,
        )

    great_ok, cliff, confirmed = _great_gates(context)
    if great_ok:
        return _result(
            "great",
            context,
            f"Only move (+{cliff:.1f}% over the next best) that holds the position.",
            cliff=cliff,
            needs_confirmation=not confirmed,
            confirmed=confirmed,
        )

    if context.played_rank == 1 or context.win_loss <= 1e-9:
        return _result("best", context, "Best move — the engine's top choice.")

    if context.win_loss < 2.0:
        return _result(
            "excellent",
            context,
            f"Excellent — keeps the evaluation within {context.win_loss:.1f}% of the best move.",
        )

    if context.win_loss < 5.0:
        return _result(
            "good",
            context,
            f"Good — a sound move that concedes {context.win_loss:.1f}% win probability.",
        )

    if context.win_loss < 10.0:
        best = _best_san(context)
        suffix = f" {best} was stronger." if best else ""
        return _result(
            "inaccuracy",
            context,
            f"Inaccuracy — {context.move.uci()} gives up {context.win_loss:.1f}% win probability.{suffix}",
        )

    previous = context.previous_move_classification
    threshold = (
        float(context.win_pre_opponent_error) - 5.0
        if context.win_pre_opponent_error is not None
        else None
    )
    missed_mate = _missed_forced_mate(context)
    if (
        previous in {"mistake", "blunder"}
        and (context.win_loss >= 10.0 or missed_mate)
        and (threshold is None or context.win_after_mover >= threshold)
    ):
        opponent = "Black" if context.mover_color == chess.WHITE else "White"
        move_number = context.previous_move_number
        where = f" on move {move_number}" if move_number else ""
        action = "a forced mate" if missed_mate else "the tactical punishment"
        return _result(
            "miss",
            context,
            f"Missed {action} after {opponent}'s {previous}{where}.",
        )

    if context.win_loss >= 20.0 or _walked_into_mate(context) or _dropped_forced_mate(context):
        best = _best_san(context)
        suffix = f" {best} was best." if best else ""
        return _result(
            "blunder",
            context,
            f"Blunder — {context.move.uci()} drops {context.win_loss:.1f}% win probability.{suffix}",
        )

    if context.win_loss < 20.0:
        best = _best_san(context)
        suffix = f" {best} was better." if best else ""
        return _result(
            "mistake",
            context,
            f"Mistake — loses {context.win_loss:.1f}% win probability.{suffix}",
        )

    # Exhaustive fallback: the thresholds cover every non-negative loss.
    return _result(
        "blunder",
        context,
        f"Blunder — loses {context.win_loss:.1f}% win probability.",
    )
