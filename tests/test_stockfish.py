"""Stockfish discovery, score normalization, Elo mapping and move selection."""

from __future__ import annotations

import random

import chess
import chess.engine
import pytest

from chess_master.analysis.winprob import MATE_CP
from chess_master.engine.stockfish import (
    ELO_PRESETS,
    AnalysisCache,
    EngineToken,
    analysis_payload_from_infos,
    discover_engine,
    elo_profile,
    format_score,
    normalize_fen,
    score_white,
    select_bot_move,
    strength_label,
    terminal_evaluation,
)


def info(
    *,
    score: chess.engine.PovScore | None = None,
    pv: tuple[str, ...] = ("e2e4", "e7e5"),
    multipv: int = 1,
    depth: int = 14,
    nodes: int = 10_000,
) -> dict:
    payload = {
        "multipv": multipv,
        "depth": depth,
        "seldepth": depth + 2,
        "nodes": nodes,
        "nps": nodes * 100,
        "time": 0.05,
        "pv": [chess.Move.from_uci(uci) for uci in pv],
        "score": score if score is not None else chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
    }
    return payload


def test_score_white_normalizes_both_sides_to_move() -> None:
    board = chess.Board()
    mate, cp = score_white(chess.engine.PovScore(chess.engine.Cp(120), chess.WHITE))
    assert mate is None
    assert cp == 120

    after_e4 = chess.Board()
    after_e4.push_uci("e2e4")
    mate, cp = score_white(chess.engine.PovScore(chess.engine.Cp(120), chess.BLACK))
    assert mate is None
    assert cp == -120

    mate, cp = score_white(chess.engine.PovScore(chess.engine.Cp(-80), chess.BLACK))
    assert mate is None
    assert cp == 80
    assert board.turn == chess.WHITE


def test_mate_scores_keep_sign_and_distance() -> None:
    mate, cp = score_white(chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE))
    assert mate == 3
    assert cp == MATE_CP - 300
    assert format_score(cp, mate) == "M3"

    mate, cp = score_white(chess.engine.PovScore(chess.engine.Mate(-2), chess.WHITE))
    assert mate == -2
    assert cp == -(MATE_CP - 200)
    assert format_score(cp, mate) == "-M2"
    assert format_score(140, None) == "+1.4"
    assert format_score(-80, None) == "-0.8"
    assert format_score(0, None) == "0.0"


def test_payload_is_white_pov_when_black_is_to_move() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    payload = analysis_payload_from_infos(
        board,
        [info(score=chess.engine.PovScore(chess.engine.Cp(150), chess.BLACK), pv=("e7e5",))],
    )
    assert payload is not None
    assert payload["score_cp"] == -150
    assert payload["score_text"] == "-1.5"
    assert payload["win_prob_white"] < 50
    assert payload["best_move"]["uci"] == "e7e5"


def test_multipv_payload_orders_lines_and_builds_san() -> None:
    board = chess.Board()
    infos = [
        info(score=chess.engine.PovScore(chess.engine.Cp(120), chess.WHITE), pv=("e2e4", "e7e5"), multipv=1),
        info(score=chess.engine.PovScore(chess.engine.Cp(80), chess.WHITE), pv=("d2d4", "d7d5"), multipv=2),
        info(score=chess.engine.PovScore(chess.engine.Mate(4), chess.WHITE), pv=("g1f3",), multipv=3),
    ]
    payload = analysis_payload_from_infos(board, infos)
    assert payload is not None
    assert payload["multipv"] == 3
    assert [line["rank"] for line in payload["lines"]] == [1, 2, 3]
    assert payload["lines"][0]["best_move"]["san"] == "e4"
    assert payload["lines"][2]["score_text"] == "M4"
    assert payload["win_prob_white"] == pytest.approx(60.87, abs=0.02)
    assert payload["pv_san"][:2] == ["e4", "e5"]


def test_terminal_checkmate_and_stalemate() -> None:
    white_mates = chess.Board("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
    assert white_mates.is_checkmate()
    payload = terminal_evaluation(white_mates)
    assert payload["terminal"] == "checkmate"
    assert payload["win_prob_white"] == 100.0
    assert payload["score_cp"] == MATE_CP
    assert payload["mate"] == 0
    assert payload["score_text"] == "M0"
    assert payload["best_move"] is None

    black_mates = chess.Board()
    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
        black_mates.push_uci(uci)
    assert black_mates.is_checkmate()
    payload = terminal_evaluation(black_mates)
    assert payload["win_prob_white"] == 0.0
    assert payload["score_cp"] == -MATE_CP

    stalemate = chess.Board("k7/2K5/1Q6/8/8/8/8/8 b - - 0 1")
    assert stalemate.is_stalemate()
    payload = terminal_evaluation(stalemate)
    assert payload["terminal"] == "draw"
    assert payload["win_prob_white"] == 50.0
    assert payload["score_text"] == "0.0"


@pytest.mark.parametrize(
    ("elo", "mode", "limit", "skill", "multipv", "depth"),
    [
        (400, "skill_weighted", False, 0, 3, 3),
        (600, "skill_weighted", False, 2, 3, 5),
        (1000, "skill_weighted", False, 7, 3, 8),
        (1319, "skill_weighted", False, 10, 3, 10),
        (1320, "limit_strength", True, 20, 1, 10),
        (1800, "limit_strength", True, 20, 1, 12),
        (2999, "limit_strength", True, 20, 1, 16),
        (3000, "full", False, 20, 1, 18),
    ],
)
def test_elo_profile_mapping(elo: int, mode: str, limit: bool, skill: int, multipv: int, depth: int) -> None:
    profile = elo_profile(elo)
    assert profile.elo == elo
    assert profile.mode == mode
    assert profile.limit_strength is limit
    assert profile.skill_level == skill
    assert profile.multipv == multipv
    assert profile.depth == depth
    if 1320 <= elo < 3000:
        assert profile.uci_elo == elo
    else:
        assert profile.uci_elo is None


def test_elo_presets_and_labels() -> None:
    assert [preset["elo"] for preset in ELO_PRESETS] == [600, 1000, 1400, 1800, 2200, 2600, 3000]
    assert strength_label(600) == "Beginner"
    assert strength_label(1400) == "Club"
    assert strength_label(2200) == "Master"
    assert strength_label(2600) == "Grandmaster"
    assert strength_label(3000) == "Maximum"


def test_uci_elo_is_clamped_to_the_engine_range() -> None:
    assert elo_profile(1400, uci_elo_range=(2000, 3000)).uci_elo == 2000
    assert elo_profile(2900, uci_elo_range=(1320, 2500)).uci_elo == 2500
    with pytest.raises(ValueError):
        elo_profile(399)
    with pytest.raises(ValueError):
        elo_profile(3001)


def _choice_infos(board: chess.Board) -> list[dict]:
    return [
        info(score=chess.engine.PovScore(chess.engine.Cp(100), board.turn), pv=("e2e4",), multipv=1),
        info(score=chess.engine.PovScore(chess.engine.Cp(20), board.turn), pv=("d2d4",), multipv=2),
        info(score=chess.engine.PovScore(chess.engine.Cp(-100), board.turn), pv=("g1f3",), multipv=3),
    ]


def test_weighted_selection_prefers_the_best_move_but_allows_inaccuracy() -> None:
    board = chess.Board()
    infos = _choice_infos(board)
    choice = select_bot_move(infos, board, 600, rng=random.Random(7))
    assert choice is not None
    assert choice.candidates == ["e2e4", "d2d4", "g1f3"]
    assert choice.weights[0] == max(choice.weights)
    assert choice.move in board.legal_moves

    picks = {0: 0, 1: 0, 2: 0}
    for seed in range(400):
        picked = select_bot_move(infos, board, 400, rng=random.Random(seed))
        assert picked is not None
        picks[picked.chosen] += 1
    assert all(count > 0 for count in picks.values())

    strong = {0: 0, 1: 0, 2: 0}
    for seed in range(400):
        picked = select_bot_move(infos, board, 1319, rng=random.Random(seed))
        assert picked is not None
        strong[picked.chosen] += 1
    assert strong[0] >= 370


def test_weighted_selection_uses_mover_pov_for_black() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    assert board.turn == chess.BLACK
    infos = [
        info(score=chess.engine.PovScore(chess.engine.Cp(120), chess.BLACK), pv=("e7e5",), multipv=1),
        info(score=chess.engine.PovScore(chess.engine.Cp(30), chess.BLACK), pv=("c7c5",), multipv=2),
        info(score=chess.engine.PovScore(chess.engine.Cp(-90), chess.BLACK), pv=("b8c6",), multipv=3),
    ]
    choice = select_bot_move(infos, board, 800, rng=random.Random(11))
    assert choice is not None
    assert choice.win_probs[0] > 50
    assert choice.move in board.legal_moves


def test_cache_normalizes_counters_and_respects_multipv() -> None:
    cache = AnalysisCache(max_entries=4)
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    payload = {"depth": 14, "multipv": 3, "score_cp": 21}
    cache.put(fen, 14, payload)
    hit = cache.get("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 12 8", 14, 3)
    assert hit is not None
    assert hit["score_cp"] == 21
    assert cache.get(fen, 14, 4) is None
    assert cache.get(fen, 16, 3) is None
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 2
    assert normalize_fen(fen) == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"


def test_discovery_priority_and_missing_state(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    local = project / "bin" / "stockfish"
    local.mkdir(parents=True)
    fake = local / "stockfish-test.exe"
    fake.write_bytes(b"test")
    found = discover_engine(project_root=project)
    assert found.available
    assert found.path == fake.resolve()
    assert found.source.startswith("project:")

    other = tmp_path / "stockfish-other.exe"
    other.write_bytes(b"test")
    monkeypatch.setenv("STOCKFISH_PATH", str(other))
    explicit = discover_engine(fake, project_root=project)
    assert explicit.path == fake.resolve()
    from_env = discover_engine(project_root=tmp_path / "empty")
    assert from_env.path == other.resolve()
    assert from_env.source == "env"

    missing = discover_engine(tmp_path / "nope.exe", project_root=project)
    assert missing.available is False
    assert missing.source == "config"
    assert missing.error


def test_token_round_trip() -> None:
    token = EngineToken("g1", 4, 2, chess.STARTING_FEN)
    assert EngineToken.from_mapping(token.to_dict()) == token
    assert token.to_dict()["revision"] == 4
