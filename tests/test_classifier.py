"""Ten-label classifier, forced metadata and Brilliant/Great gates."""

from __future__ import annotations

import chess

from chess_master.analysis.classifier import (
    CLASSIFICATION_LABELS,
    CLASSIFICATION_ORDER,
    CLASSIFICATION_SYMBOLS,
    MoveContext,
    classify_move,
    find_sacrifice,
    static_exchange_evaluation,
)


def _context(
    *,
    fen: str = chess.STARTING_FEN,
    uci: str = "e2e4",
    win_before: float = 60.0,
    win_after: float | None = None,
    win_loss: float = 1.5,
    lines: list[dict] | None = None,
    confirmation_lines: list[dict] | None = None,
    **kwargs,
) -> MoveContext:
    board = chess.Board(fen)
    return MoveContext(
        board_before=board,
        move=chess.Move.from_uci(uci),
        win_before_mover=win_before,
        win_after_mover=win_before - win_loss if win_after is None else win_after,
        lines_before=lines if lines is not None else [],
        confirmation_lines=confirmation_lines,
        **kwargs,
    )


def test_ten_canonical_labels_exist_without_a_forced_label() -> None:
    assert len(CLASSIFICATION_ORDER) == 10
    assert set(CLASSIFICATION_ORDER) == set(CLASSIFICATION_LABELS)
    assert set(CLASSIFICATION_ORDER) == set(CLASSIFICATION_SYMBOLS)
    assert "forced" not in CLASSIFICATION_ORDER


def test_forced_move_rolls_up_as_best() -> None:
    context = _context(
        fen="8/8/8/8/8/6k1/1R6/r6K w - - 0 1",
        uci="b2b1",
        win_before=60.0,
        win_after=75.0,
    )
    assert context.is_forced is True
    result = classify_move(context)
    assert result.classification == "best"
    assert result.is_forced is True
    assert "forced" in result.explanation.lower()
    assert result.symbol == CLASSIFICATION_SYMBOLS["best"]


def test_book_precedes_forced_and_everything_else() -> None:
    context = _context(
        fen="8/8/8/8/8/6k1/1R6/r6K w - - 0 1",
        uci="b2b1",
        win_before=60.0,
        win_after=75.0,
        is_book=True,
        opening_name="Test Opening",
        opening_eco="Z99",
    )
    result = classify_move(context)
    assert result.classification == "book"
    assert result.is_forced is True
    assert "Z99" in result.explanation


def test_win_loss_thresholds_produce_the_expected_labels() -> None:
    cases = [
        (0.0, "best"),
        (1.5, "excellent"),
        (3.0, "good"),
        (7.0, "inaccuracy"),
        (15.0, "mistake"),
        (25.0, "blunder"),
    ]
    for loss, expected in cases:
        context = _context(
            win_loss=loss,
            win_before=60.0,
            lines=[{"uci": "g8f6", "score_cp": 50}],
        )
        assert classify_move(context).classification == expected, loss


def test_miss_requires_an_opponent_error_and_a_held_position() -> None:
    context = _context(
        win_before=85.0,
        win_loss=15.0,
        previous_move_classification="blunder",
        previous_move_number=12,
        win_pre_opponent_error=75.0,
    )
    result = classify_move(context)
    assert result.classification == "miss"
    assert "Black" in result.explanation

    not_held = _context(
        win_before=80.0,
        win_loss=15.0,
        previous_move_classification="blunder",
        previous_move_number=12,
        win_pre_opponent_error=90.0,
    )
    assert classify_move(not_held).classification != "miss"


def test_forced_mate_edges_upgrade_to_blunder() -> None:
    walked = _context(
        win_before=70.0,
        win_after=55.0,
        win_loss=15.0,
        mate_before=3,
        mate_after=-2,
    )
    assert classify_move(walked).classification == "blunder"

    dropped = _context(
        win_before=70.0,
        win_after=49.0,
        win_loss=5.0,
        mate_before=3,
        mate_after=None,
    )
    assert classify_move(dropped).classification == "blunder"


def test_brilliant_requires_pass_two_confirmation() -> None:
    fen = "6k1/8/6p1/8/8/8/8/3Q2K1 w - - 0 1"
    line = {"uci": "d1h5", "score_cp": 300}
    context = _context(
        fen=fen,
        uci="d1h5",
        win_before=61.6,
        win_after=56.1,
        lines=[line],
    )
    assert find_sacrifice(context.board_before, context.move) is not None
    pending = classify_move(context)
    assert pending.classification == "brilliant"
    assert pending.needs_confirmation is True
    assert pending.confirmed is False
    assert pending.sacrifice_info["piece"] == "queen"
    assert all(pending.brilliant_gates[key] for key in pending.brilliant_gates if key != "pass_2_confirmed")

    confirmed = _context(
        fen=fen,
        uci="d1h5",
        win_before=61.6,
        win_after=56.1,
        lines=[line],
        confirmation_lines=[line],
    )
    result = classify_move(confirmed)
    assert result.classification == "brilliant"
    assert result.needs_confirmation is False
    assert result.confirmed is True
    assert result.brilliant_gates["pass_2_confirmed"] is True


def test_great_needs_a_large_multi_pv_cliff_and_confirmation() -> None:
    lines = [
        {"uci": "e2e4", "score_cp": 100},
        {"uci": "d2d4", "score_cp": -100},
    ]
    context = _context(win_before=65.0, win_after=60.0, lines=lines)
    result = classify_move(context)
    assert result.classification == "great"
    assert result.needs_confirmation is True
    assert result.multi_pv_cliff is not None
    assert result.multi_pv_cliff >= 10.0

    confirmed = _context(
        win_before=65.0,
        win_after=60.0,
        lines=lines,
        confirmation_lines=lines,
    )
    result = classify_move(confirmed)
    assert result.classification == "great"
    assert result.needs_confirmation is False
    assert result.confirmed is True


def test_small_cliff_does_not_earn_great() -> None:
    lines = [
        {"uci": "e2e4", "score_cp": 30},
        {"uci": "d2d4", "score_cp": 20},
    ]
    result = classify_move(_context(win_before=60.0, win_after=57.0, lines=lines))
    assert result.classification != "great"


def test_static_exchange_evaluation_and_sacrifice_metadata() -> None:
    board = chess.Board("7k/8/8/8/8/8/r7/R6K w - - 0 1")
    assert static_exchange_evaluation(board, chess.A2, chess.WHITE) == 5

    queen_sac = chess.Board("6k1/8/6p1/8/8/8/8/3Q2K1 w - - 0 1")
    sacrifice = find_sacrifice(queen_sac, chess.Move.from_uci("d1h5"))
    assert sacrifice is not None
    assert sacrifice["is_sacrifice"] is True
    assert sacrifice["legal_capture"] is True
    assert sacrifice["undefended"] is True
    assert sacrifice["captured_value"] == 0
    assert sacrifice["piece_value"] == 9
