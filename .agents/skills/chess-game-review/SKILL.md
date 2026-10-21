---
name: chess-game-review
description: Domain knowledge for building a Chess.com-style Game Review in a PyWebView + python-chess + Stockfish desktop app. Use when implementing or reviewing Stockfish UCI analysis, evaluation normalization, win-probability and accuracy math, move classification (Brilliant/Great/Miss/Blunder), ACPL, estimated Elo, or the analysis pipeline and its JSON contract with the Chessground frontend.
---

# Chess Game Review (Stockfish + Chess.com-style scoring)

Single source of truth for the review engine. Keep formulas here, not scattered
through Python or JavaScript. When a number changes, change it here first, then
update the implementation and its tests.

## When to Use

- Wiring Stockfish over UCI with `python-chess`
- Deciding engine depth / MultiPV / thread and hash settings
- Converting centipawn and mate scores into win probability
- Computing centipawn loss, win% loss, accuracy, ACPL
- Classifying moves (Best, Excellent, Good, Book, Inaccuracy, Mistake,
  Blunder, Miss, Great, Brilliant, Forced)
- Estimating player Elo or building the review summary
- Defining the Python ↔ JavaScript payload for the frontend
- Debugging "the review says I blundered a winning position"

## Non-negotiable rules

1. **One engine process, one owner thread.** A `chess.engine` object is not
   thread-safe. Own it from a single worker; feed it jobs through a queue.
2. **Never block the UI thread.** PyWebView's JS bridge call must return
   immediately with a job id; stream progress through `evaluate_js` events.
3. **All comparisons use the same point of view.** Normalize every score to
   White POV for storage, then convert to the mover's POV only for display.
4. **Keep the engine's best move separate from the played move.** MultiPV >= 2
   lets you score the played move even when it is not in the top line, but the
   POV loss must always be measured against the engine's #1 line.
5. **Mate scores are not centipawns.** Convert mates to win% 0/100 and to a
   bounded pseudo-cp value; never feed 100000 into an exponential formula.
6. **Flag forced and book positions before classifying.** A move with one legal
   reply cannot be a blunder. A book move should not be graded on raw cp loss.

## Pipeline

```
PGN / move list
  → validate & replay with python-chess (board.fen(), board.move_stack)
  → detect opening book + forced moves + game phase
  → per ply: engine analyse position with MultiPV=2+ (fixed depth)
  → store best eval, played-move eval, best move, PV, depth
  → normalize scores to White POV
  → win% before/after → loss → move accuracy
  → classify → aggregate (ACPL, game accuracy, estimated Elo, counts)
  → cache per (fen, depth, multipv, engine version)
  → emit ReviewPayload to the frontend
```

Analyze each position **once**, at the same depth, with the same engine build.
Mixed depths make accuracy numbers meaningless and non-reproducible.

## Stockfish UCI contract

### Handshake and configuration

```
uci
setoption name Threads value 2
setoption name Hash value 256
setoption name MultiPV value 3
setoption name UCI_ShowWDL value true
isready
ucinewgame
```

`UCI_ShowWDL` is optional but useful: Stockfish's own WDL model is a second,
better-calibrated opinion than raw cp, and it exposes draw probability, which
plain win% hides.

### Position and search

```
position startpos moves e2e4 e7e5 g1f3
position fen <FEN> moves <uci moves>
go depth 18
go movetime 1500
go nodes 2000000
stop
quit
```

Depth, not movetime, for review: identical depth gives reproducible grades.
Use `movetime` only for a fast "instant review" mode and label it as such.

### Parsing `info`

```
info depth 16 seldepth 21 multipv 1 score cp 34 nodes ... nps ... pv e2e4 e7e5 ...
info depth 16 multipv 2 score mate -3 ... pv ...
info depth 16 multipv 3 score cp -120 ... pv d7d5 ...
bestmove e2e4 ponder e7e5
```

- `multipv 1` is the engine's best line; lower `multipv` indices are better.
- `score cp` and `score mate` are relative to the **side to move**.
- `score mate 0` means the side to move is already checkmated (or will be).
- The last `info` before `bestmove` for a given `multipv` is the deepest.
- Ignore `info currmove`, `info string`, and lines without `pv` for grading.

### python-chess specifics

```python
import chess, chess.engine

engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
engine.configure({"Threads": 2, "Hash": 256, "MultiPV": 3})

infos = engine.analyse(
    board,
    chess.engine.Limit(depth=18),
    multipv=3,
)
# infos is a list, one dict per PV; [0] is best
best = infos[0]
score = best["score"].pov(chess.WHITE)   # PovScore → White POV
```

- `SimpleEngine` is fine when you drive one analysis loop. `UCIEngine` is
  needed for `engine.analysis()` streaming `info` updates while a search runs.
- `engine.ping()` is a cheap health check; call it after startup.
- `engine.quit()` in a `finally` block, and guard against a dead process.
- `chess.engine.Limit(depth=18)` must stay in sync with the cache key.
- `board.fen()` loses the move history; use `board.move_stack` / `position
  startpos moves ...` when repetition and fifty-move context matter.

### Score normalization

```python
MATE_CP = 10_000

def to_white_cp(score: chess.engine.PovScore) -> int:
    white = score.pov(chess.WHITE)
    if white.is_mate():
        mate = white.mate()              # >0 White mates, <0 Black mates
        if mate == 0:
            return -MATE_CP if white.turn is None else 0
        sign = 1 if mate > 0 else -1
        return sign * (MATE_CP - abs(mate) * 100)
    return white.score(mate_score=MATE_CP)
```

Rules:

- Store **White POV cp** in the review data.
- For grading a move by White, use `cp`; for Black, negate it.
- Cap ordinary cp at `±1500` (or `±1000`) before win% conversion. Without a cap
  a +30 pawn eval saturates accuracy for the rest of the game.
- Mate distance matters: mate in 1 is stronger than mate in 8. The
  `MATE_CP - 100*n` encoding preserves that ordering.

## Win probability

Two accepted models. Pick one per project and version the choice in the cache
key, because swapping models changes every accuracy score.

### Model A — Elo expectation (simple, transparent)

```
W(cp) = 1 / (1 + 10 ** (-cp / 400))
```

`W` is White's expected score in [0, 1]. Symmetric, easy to explain, but too
steep near 0 and too flat at large advantages.

### Model B — Lichess logistic (recommended)

```
W(cp) = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)
```

Result is a percentage in [0, 100]. This is the public Lichess model and is
well behaved across the whole eval range.

```
mate for White  → W = 100
mate for Black  → W = 0
|cp| > 1500     → clamp before evaluating
```

For display, round to one decimal. For math, keep full float precision and
round only at the end.

### Win% loss

```
loss_win = max(0, W_before - W_after)
```

- `W_before` = win% of the position **before** the move, from the engine's best
  line (the position is the same for both players).
- `W_after` = win% of the position **after** the played move, from the mover's
  POV.
- Both must be from the same player's perspective. Compute in White POV, then
  flip both to the mover's POV, or compute in White POV and use the mover's
  sign consistently.

```
W_mover_before = W_white_before            if mover == WHITE else 100 - W_white_before
W_mover_after  = W_white_after             if mover == WHITE else 100 - W_white_after
loss_win       = max(0, W_mover_before - W_mover_after)
```

### Centipawn loss

```
cp_loss = max(0, cp_best_for_mover - cp_played_for_mover)
```

For the played move, the "eval after" is the engine's eval of the resulting
position, **not** the eval of the move itself. If the played move is the best
move, `cp_loss = 0` by definition.

Clamp `cp_loss` at `1000` for aggregation so one lost position cannot dominate
a game average.

## Accuracy

### Per-move accuracy (Chess.com-style curve)

```
accuracy_move = 103.1668 * exp(-0.04354 * loss_win) - 3.1669
accuracy_move = clamp(accuracy_move, 0, 100)
```

`loss_win` is in **percentage points** (0–100), not a fraction. This is the
widely used community reverse-engineering of Chess.com's curve; treat it as a
calibration target, not an official spec.

Properties to preserve when you refactor:

- `loss_win = 0` → ~100%
- `loss_win = 10` → ~68%
- `loss_win = 20` → ~44%
- `loss_win = 30` → ~29%
- Monotonically decreasing, asymptotic to ~-3.17 before clamping.

A useful sanity check: a single 30-point win% swing should not drop a 40-move
game below ~90% game accuracy. If it does, you are averaging wrong.

### Game accuracy

Do **not** use a plain arithmetic mean: it lets one blunder be diluted by 30
easy moves, and it over-rewards a game with no decisions.

Recommended aggregate:

```
game_accuracy = weighted_harmonic_mean(move_accuracies, weights=difficulty)
```

with a fallback to the arithmetic mean when fewer than 5 non-forced,
non-book moves exist. Chess.com emphasizes difficult positions; a practical
weight is:

```
difficulty = 1 + (loss_win_possible / 100)   # or volatility of the position
```

Simpler, still defensible: arithmetic mean over non-forced, non-book moves,
then apply a penalty proportional to the blunder rate. Document whichever is
chosen and keep it stable across releases.

### ACPL

```
ACPL = mean(cp_loss for graded moves)     # exclude forced and book moves
```

Report ACPL separately for White and Black, and cap per-move loss at 1000.
ACPL and accuracy are not interchangeable: ACPL is linear in eval, accuracy is
nonlinear in win%.

### Estimated Elo

Estimated Elo is a heuristic mapping from accuracy + ACPL, not a rating
calculation. Publish it as an estimate and calibrate against real games:

| Accuracy | Rough Elo band |
|----------|----------------|
| 95–100   | 2200+          |
| 90–94    | 1900–2200      |
| 85–89    | 1600–1900      |
| 80–84    | 1400–1600      |
| 70–79    | 1100–1400      |
| 60–69    | 800–1100       |
| < 60     | < 800          |

Prefer a small regression on (accuracy, ACPL, game length) fitted to labelled
games over a hard-coded table. Store coefficients here when fitted.

## Move classification

Thresholds are **starting points**, not truth. Tune against a labelled fixture
set and record the version in the payload.

| Label | Rule (win% loss unless stated) | Notes |
|-------|-------------------------------|-------|
| Book | Position found in opening book | Never grade on cp loss |
| Forced | Exactly one legal move | Exempt from accuracy/ACPL |
| Best | Played move == engine #1 | Always Best, even if eval is bad |
| Excellent | `loss_win <= 2` | Near-perfect practical move |
| Good | `loss_win <= 5` | Sound, small concession |
| Inaccuracy | `5 < loss_win <= 10` | |
| Mistake | `10 < loss_win <= 20` | |
| Blunder | `loss_win > 20` | |
| Miss | Blunder/mistake **and** a forced mate or winning tactic was available and missed | Overrides Blunder when mate was on the board |
| Great | Only-move that holds the position, or the unique move that refutes the opponent's threat | Requires MultiPV evidence |
| Brilliant | Best/Excellent move that is a genuine material sacrifice, not already completely winning, and not book | Rarest label; require all conditions |

Guard rails that prevent absurd grades:

- If the mover was already winning by > 90% win% and stays > 90%, do not label
  a normal capture/recapture a Brilliant.
- A move that is forced (only legal move, or all moves transpose) is Forced,
  never Blunder — even if the position is lost.
- If the game is already decided (|win%| > 95 for 3+ plies), suppress
  Brilliant/Great.
- `Miss` requires that the engine's best line contained mate or a decisive
  material win; "you missed a +2" is a Mistake, not a Miss.
- A sacrifice must actually lose material on the board after the best reply,
  not merely be a temporary tactical offer the engine refutes.

### Brilliant heuristic (explicit)

```
is_brilliant =
    label in {Best, Excellent}
    and not is_book
    and not is_forced
    and material_after_best_reply <= material_before - SACRIFICE_MIN   # e.g. 200cp
    and win_before < 90
    and played_move in engine_top_n                                    # e.g. top 2
```

Document `SACRIFICE_MIN` and `engine_top_n` here once chosen.

## Phases and context

- **Opening**: plies 1–20, or until the book is exhausted. Book moves get no
  accuracy score but still appear in the move list.
- **Middlegame**: default phase for grading.
- **Endgame**: material below a threshold (e.g. no queens and <= 13 total
  points) or `board.piece_map()` heuristics. Consider raising the depth here;
  endgame accuracy is more sensitive to depth.
- **Volatility**: positions where the top 3 moves span > 15 win% are "sharp".
  Weight these more in game accuracy and use them to decide Great/Miss.
- **Time pressure / clock**: if PGN has clocks, keep it for UI only; do not
  mix it into engine math.

## Review payload (Python → JS)

Keep the wire format stable and versioned. `eval` is always White POV cp.

```json
{
  "schema": 1,
  "engine": { "name": "Stockfish", "version": "17", "depth": 18, "multipv": 3 },
  "model": { "winprob": "lichess-logistic", "accuracy": "cc-103.1668" },
  "players": { "white": "Alice", "black": "Bob" },
  "moves": [
    {
      "ply": 1,
      "san": "e4",
      "uci": "e2e4",
      "fen_before": "...",
      "eval_before": 32,
      "eval_after": 28,
      "best_uci": "e2e4",
      "best_san": "e4",
      "pv": ["e2e4", "e7e5"],
      "mate": null,
      "win_before": 54.2,
      "win_after": 53.6,
      "cp_loss": 4,
      "win_loss": 0.6,
      "accuracy": 97.4,
      "classification": "best",
      "is_book": false,
      "is_forced": false
    }
  ],
  "summary": {
    "white": { "accuracy": 88.1, "acpl": 24, "blunders": 1, "mistakes": 2,
               "inaccuracies": 3, "best": 21, "estimated_elo": 1780 },
    "black": { "accuracy": 91.4, "acpl": 18, "blunders": 0, "mistakes": 1,
               "inaccuracies": 2, "best": 24, "estimated_elo": 1920 }
  },
  "progress": { "done": 40, "total": 40 }
}
```

Frontend rules that follow from this:

- Color evals from White POV; flip for the board orientation, not for storage.
- Use `win_before`/`win_after` for the eval bar, not raw cp.
- Classification colors must be defined once in the Tailwind theme, not inline.
- Show `depth` and `multipv` somewhere in the review UI; it manages expectations.
- A review with `is_forced` moves should visibly skip them in accuracy math.

## Caching

Key: `sha256(fen + depth + multipv + engine_version + model_version)`.
Store `{best_cp, best_uci, pv, played_cp, wdl}`. Cache per position, not per
PGN, so transpositions and repeated reviews are cheap. Invalidate the whole
cache when the Stockfish binary or classification thresholds change.

## Failure modes to guard against

- **Sign errors**: grading Black with White POV cp turns every good Black move
  into a blunder. Assert `cp_loss >= 0` and test both colors.
- **Engine not ready**: `analyse` before `isready` can hang. Always handshake.
- **Stale search**: after `stop`, discard `info` lines from the cancelled job;
  tag each job with an id and ignore mismatches.
- **Dead engine**: a crashed Stockfish returns no `bestmove`. Timeout the job,
  restart the process, and surface a retryable error to the UI.
- **Mate conversion**: `mate 0` and `mate -0` are not "no mate"; handle them.
- **Missing MultiPV**: grading a played move that is outside the PV needs its
  own search or a higher MultiPV. Do not reuse the best line's eval for the
  played move.
- **Book detection**: do not treat "first 10 plies" as book; use a real opening
  book or an ECO/PGN database.
- **Depth drift**: a cache from depth 12 must never be mixed into a depth 18
  review.

## Testing checklist

- Unit-test win% at `cp = 0, ±100, ±400, ±1000`, and mates ±1, ±5.
- Unit-test accuracy at `loss_win = 0, 10, 20, 30` against the curve table.
- Property: `cp_loss >= 0`, `0 <= accuracy <= 100` for 10k random inputs.
- Symmetry: a position and its color-flipped twin produce identical accuracy
  after POV conversion.
- Fixture PGNs: one clean game (~95%), one blunderfest (~60%), one forced-move
  game (100% or excluded), one book-only game.
- Golden test the full payload for a 20-move game; review diffs by hand when a
  formula changes.
- Cross-check 5 positions against the Stockfish CLI or a second engine build.

## References

- Stockfish UCI docs: <https://official-stockfish.github.io/docs/stockfish-wiki/UCI-chess-engine.html>
- python-chess engine API: <https://python-chess.readthedocs.io/en/latest/engine.html>
- Lichess win% model: <https://github.com/lichess-org/lila>
- Chess.com accuracy discussion (community reverse engineering): search
  "chess.com accuracy formula 103.1668"
- Chess.com Game Review announcement: <https://www.chess.com/news/view/game-review>

Update this section with the exact engine build and any calibration datasets
used, so a future change can be reproduced.
