---
name: chess-game-review
description: Domain knowledge for building a Chess.com-style Game Review in a PyWebView + python-chess + Stockfish desktop app. Use when implementing or reviewing Stockfish UCI analysis, evaluation normalization, win-probability and accuracy math, move classification (Brilliant/Great/Miss/Blunder), ACPL, estimated Elo, or the analysis pipeline and its JSON contract with the Chessground frontend.
---

# Chess Game Review (Stockfish + Chess.com-style scoring)

Single source of truth for the review engine. Keep formulas here, not scattered
through Python or JavaScript. When a number changes, change it here first, then
update the implementation and its tests.

Phase 3 implementation lives in:

- `src/chess_master/analysis/review.py` — two-pass pipeline and report
- `src/chess_master/analysis/classifier.py` — ten labels and Brilliant/Great gates
- `src/chess_master/analysis/metrics.py` — accuracy, ACPL, phases, estimated Elo
- `src/chess_master/analysis/openings.py` — bundled ECO trie and Polyglot extension
- `src/chess_master/analysis/winprob.py` — Lichess logistic and accuracy curves
- `src/chess_master/engine/stockfish.py` — worker, MultiPV searches and cache
- `src/chess_master/engine/coordinator.py` — review lifecycle and invalidation

## When to Use

- Wiring Stockfish over UCI with `python-chess`
- Deciding engine depth / MultiPV / thread and hash settings
- Converting centipawn and mate scores into win probability
- Computing centipawn loss, win% loss, accuracy, ACPL
- Classifying moves (Best, Excellent, Good, Book, Inaccuracy, Mistake,
  Blunder, Miss, Great, Brilliant; Forced is metadata, not an eleventh label)
- Estimating player Elo or building the review summary
- Defining the Python ↔ JavaScript payload for the frontend
- Debugging "the review says I blundered a winning position"

## Non-negotiable rules

1. **One engine process, one owner thread.** A `chess.engine` object is not
   thread-safe. Own it from a single worker; feed it jobs through a queue.
2. **Never block the UI thread.** The JS bridge call returns immediately with a
   job/review id; progress is polled through the compact state block.
3. **All comparisons use the same point of view.** Store every score in White
   POV. Convert to the mover's POV only for win%, cp loss and display.
4. **Keep the engine's best move separate from the played move.** MultiPV 3
   lets us identify the played move's rank even when it is outside the top
   line. The reference is always the engine's #1 line.
5. **Mate scores are not centipawns.** Convert mates to win% 0/100 and to a
   bounded pseudo-cp value; never feed 100000 into an exponential formula.
6. **Book and forced positions are special.** A book move is never graded on
   cp loss. A forced move is labelled `best`, carries `is_forced: true`, and is
   excluded from accuracy/ACPL aggregation.
7. **Brilliant and Great are two-pass labels.** Pass 1 may flag a candidate;
   pass 2 must re-search its surrounding positions two plies deeper before the
   label is final. Pass 2 may downgrade a candidate.

## Pipeline

```
PGN / move list
  -> validate and replay with python-chess (boards[0..N])
  -> detect ECO / Polyglot book prefix -> last_book_ply
  -> determine forced moves and phase for each ply
  -> needed positions = last_book_ply .. N (skip if the whole game is book)
  -> PASS 1: MultiPV=3 at the preset depth
  -> initial grading -> flag Brilliant/Great candidates
  -> PASS 2: re-search {ply-1, ply} per candidate at min(depth+2, 18)
             up to three rounds, then re-grade
  -> win sequence -> accuracy / ACPL / phase splits / estimated Elo
  -> versioned review report + compact progress block
```

Pass 1 analyses every position that is the "before" or "after" position of a
non-book ply. The final position is included when the last move is non-book.
Terminal positions are synthesized without starting Stockfish.

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

`UCI_ShowWDL` is optional diagnostics; the review math uses the Lichess
logistic model below.

### Presets

| Preset | Pass 1 depth | Pass 2 depth | MultiPV |
|--------|--------------|--------------|---------|
| fast | 10 | 12 | 3 |
| standard | 14 | min(16, 18) = 16 | 3 |
| deep | 16 | min(18, 18) = 18 | 3 |

A custom `depth` is allowed only from 6 to 24. Its confirmation depth is
`min(depth + 2, 18)`. If the base depth is already 18 or higher, pass 2 cannot
add depth and candidates remain visibly unconfirmed rather than pretending.

### Position and search

```
position startpos moves e2e4 e7e5 g1f3
position fen <FEN> moves <uci moves>
go depth 14
stop
quit
```

Depth, not movetime, for review: identical depth gives reproducible grades.
Use `movetime` only for a fast "instant review" mode, and never mix its results
with a fixed-depth review.

### Parsing `info`

```
info depth 16 seldepth 21 multipv 1 score cp 34 nodes ... pv e2e4 e7e5 ...
info depth 16 multipv 2 score mate -3 ... pv ...
info depth 16 multipv 3 score cp -120 ... pv d7d5 ...
bestmove e2e4 ponder e7e5
```

- `multipv 1` is the engine's best line; lower indices are better.
- `score cp` and `score mate` are relative to the side to move.
- `score mate 0` means the side to move is already checkmated.
- The last `info` before `bestmove` for a given `multipv` is the deepest.
- Ignore `info currmove`, `info string`, and lines without `pv` for grading.

### python-chess specifics

```python
import chess, chess.engine

engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
engine.configure({"Threads": 2, "Hash": 256, "MultiPV": 3})

infos = engine.analyse(board, chess.engine.Limit(depth=14), multipv=3)
best = infos[0]
score = best["score"].pov(chess.WHITE)   # PovScore -> White POV
```

- `SimpleEngine` is fine when one worker owns the loop.
- `UCIEngine` is needed for streaming `info` updates during a search.
- `engine.quit()` in `finally`, and guard against a dead process.
- Cache key must include depth, multipv, engine version and model version.

### Terminal positions

Do not start a search for:

- checkmate: synthetic line with White win probability 100 or 0
- stalemate / insufficient material / seventy-five moves / fivefold repetition:
  synthetic draw with 50% and terminal CP 0

The pipeline counts these as `timing.terminal_positions`.

## Score normalization

```python
MATE_CP = 10_000

def to_white_cp(score: chess.engine.PovScore) -> int:
    white = score.pov(chess.WHITE)
    if white.is_mate():
        mate = white.mate()              # >0 White mates, <0 Black mates
        if mate == 0:
            return -MATE_CP
        sign = 1 if mate > 0 else -1
        return sign * (MATE_CP - abs(mate) * 100)
    return white.score(mate_score=MATE_CP)
```

Rules:

- Store **White POV cp** in the review data.
- For grading Black, negate the cp delta.
- Ordinary Game Review scores are clamped to **±1000 cp** before win%
  conversion. This is `REVIEW_CP_CEILING`.
- The Phase 1/2 helpers keep the older **±1500 cp** ceiling and the rounded
  accuracy curve; do not silently change stored Phase 1 values.
- Mate distance matters: mate in 1 is stronger than mate in 8 because the
  pseudo-cp magnitude decreases with distance.

## Win probability

The canonical Game Review model is the Lichess logistic:

```
W(cp) = 100 / (1 + exp(-0.00368208 * cp))
```

with `cp` clamped to `[-1000, +1000]`. Result is a percentage in `[0, 100]`.
`W(0) = 50`.

Mate endpoints:

```
mate for White -> 100
mate for Black -> 0
```

The standard-start prior is **+15 cp for White**:

```
W_prior = W(15) = 51.382...%
```

This prior is only used for position 0 of the standard start. A custom start
uses its first real evaluation, or 50% if no position can be searched.

For display, round to one decimal. For math, keep full float precision and
round only at the end.

### Win% loss

Work in White POV, then flip both values to the mover's POV:

```
W_mover_before = W_white_before            if mover == WHITE else 100 - W_white_before
W_mover_after  = W_white_after             if mover == WHITE else 100 - W_white_after
loss_win       = max(0, W_mover_before - W_mover_after)
```

`W_before` is the engine's #1 line in the position before the move.
`W_after` is the engine's #1 line in the position after the played move.

### Centipawn loss

```
delta_white = clamp1000(cp_before_white) - clamp1000(cp_after_white)
cp_loss     = max(0, min(1000, delta_white if mover == WHITE else -delta_white))
```

If the played move is the engine's #1 line, `cp_loss` and `win_loss` are
**forced to 0**. Shallow eval drift between the before/after searches must not
charge the engine's own top choice a loss. Book moves store `cp_loss: null`
and are excluded from aggregation.

## Accuracy

### Per-move accuracy (canonical review curve)

For `loss_win <= 0`, accuracy is exactly `100.0`. Otherwise:

```
accuracy_move = 103.1668100711649 * exp(-0.04354415386753951 * loss_win)
                - 3.166924740191411
                + 1.0
accuracy_move = clamp(accuracy_move, 0, 100)
```

The `+1.0` is the canonical uncertainty bonus. `loss_win` is in **percentage
points** (0–100), not a fraction.

The legacy Phase 1/2 curve is frozen at the rounded community constants and
has **no** bonus:

```
accuracy_legacy = clamp(
    103.1668 * exp(-0.04354 * loss_win) - 3.1669,
    0, 100,
)
```

Do not replace the legacy helper with the review helper. Stored Phase 1
payloads and their tests depend on the exact rounded values.

### Game accuracy

Do **not** use a plain arithmetic mean. The canonical aggregate is a 50/50
blend of the volatility-weighted mean and the harmonic mean over counted
moves (non-book, non-forced).

For `N` counted moves, the window length is:

```
L = clamp(floor(N / 10), 2, 8)
```

`win_probs_white` is the sequence of positions `0..N`. Build boundary padding
by prefixing `(L - 2)` copies of the initial window `W[0:L]`. For move `j`
(0-based), the weight is the **population** standard deviation of
`padded[j : j + L]`, clamped to `[0.5, 12.0]` percentage points.

```
A_weighted = sum(a_i * w_i) / sum(w_i)
A_harmonic = |M| / sum(1 / max(a_i, 1.0))
accuracy   = clamp((A_weighted + A_harmonic) / 2, 0, 100)
```

If there are no counted moves, accuracy is `null`. The report also exposes
`weighted_accuracy` and `harmonic_accuracy` separately for debugging.

### ACPL

```
ACPL_raw = mean(cp_loss for counted moves with a cp loss)
```

For 8 or more scored moves, trim the highest `ceil(0.10 * n)` losses before
taking the mean. With fewer than 8, use the raw mean. Per-move `cp_loss` is
capped at 1000. Forced and book moves are excluded.

`ACPL_raw` and `ACPL_trimmed` are both reported. Accuracy and ACPL are not
interchangeable: ACPL is linear in eval, accuracy is nonlinear in win%.

### Estimated Elo

Single-game heuristic, clamped to `[400, 3000]` and rounded to the nearest 50.

Unanchored:

```
acpl_component     = 3100 * exp(-0.01 * acpl_trim)
accuracy_component = 400 + 2600 / (1 + exp(-0.10 * (accuracy - 75)))
blend              = 0.55 * acpl_component + 0.45 * accuracy_component
penalty            = 700 * blunder_rate
                   + 350 * mistake_rate
                   + 3 * max(0, acpl_raw - acpl_trim)
unanchored         = clamp(blend - penalty, 400, 3000)
```

Where the rates use counted moves as the denominator (the `graded_moves`
argument passed by `player_metrics` is the counted-move count).

Anchored around a supplied baseline rating:

```
expected_accuracy = 55 + 45 / (1 + exp(-(baseline - 1400) / 600))
expected_acpl     = clamp(95 - 0.02 * baseline, 15, 95)
delta_R           = 20 * (accuracy - expected_accuracy)
                  - 4 * (acpl_trim - expected_acpl)
anchored          = clamp(baseline + delta_R, 400, 3000)
```

If a baseline is supplied, the anchored value is displayed. Otherwise use the
unanchored value. Publish it as an estimate, not a rating calculation.

## Move classification

Exactly ten public labels. **Forced is metadata**, not an eleventh label:

```
brilliant, great, best, excellent, good, book, inaccuracy, mistake, miss, blunder
```

Precedence in `classify_move`:

1. **Book** — position is inside the detected ECO/Polyglot prefix. Never graded.
2. **Forced** — exactly one legal move. Returns `best` with `is_forced: true`.
3. **Brilliant** — all six gates plus pass-2 confirmation.
4. **Great** — all Great gates plus pass-2 confirmation.
5. **Best** — played rank is 1, or `win_loss <= 1e-9`.
6. **Excellent** — `win_loss < 2`.
7. **Good** — `win_loss < 5`.
8. **Inaccuracy** — `5 <= win_loss < 10`.
9. **Miss** — opponent's previous move was a mistake/blunder, the current move
   loses at least 10 win% (or missed a forced mate), and the mover still holds
   within 5 win% of the pre-error position. Miss is checked before Blunder.
10. **Blunder** — `win_loss >= 20`, or walked into mate, or dropped a forced mate.
11. **Mistake** — remaining `10 <= win_loss < 20`.

The exact boundary of 20 is Blunder; 10 through 19.999 is a Mistake unless
Miss applies. A forced move never becomes a Blunder.

### Brilliant gates

Using the pass-2 confirmation lines when available:

1. **Engine optimality**: not book and (rank 1, or rank <= 3 with
   `win_loss < 2`).
2. **Board exchange**: the move leaves a friendly non-pawn piece genuinely en
   prise. The moved piece counts, and another non-pawn counts only if the move
   newly exposes it. Captured material must be strictly less than the
   sacrificed piece's value.
3. **Legal capture**: the opponent actually has a legal capture on the
   sacrificed piece (pinned attackers do not count).
4. **Not a trapped piece**: there is a non-sacrificial alternative within the
   MultiPV lines, or the piece was not already unavoidably lost.
5. **Floor and ceiling**: `win_after_mover >= 50` and
   `win_before_mover <= 90`. Do not call a move Brilliant when the position was
   already completely winning.
6. **Structural**: not already in check, not a queen promotion, and at least
   two legal moves existed.

`is_brilliant = book == false && gate1 && gate2 && gate3 && gate4 && gate5 && gate6`

Pass 1 sets `needs_confirmation: true` and `confirmed: false`. Pass 2
re-searches the position before and after the move at `min(depth + 2, 18)` and
re-runs the classifier. Only then may `confirmed` become true.

### Great gates

```
not book
and legal_move_count >= 2
and (played_rank == 1 or win_loss <= 1.0)
and multi_pv_cliff >= 10.0
and win_before_mover <= 90.0
and win_after_mover >= 50.0
and not a trivial capture
```

`multi_pv_cliff` is the win% difference between the mover's #1 and #2 lines.
A trivial capture is a capture on a square with no defenders. Great candidates
also require pass-2 confirmation.

### Miss gates

```
previous_move_classification in {mistake, blunder}
and (win_loss >= 10 or missed_forced_mate)
and (win_pre_opponent_error is None
     or win_after_mover >= win_pre_opponent_error - 5)
```

`missed_forced_mate` is true when the mover had a forced mate before the move
and no longer has one after it. Miss is evaluated before Blunder, so a missed
mate is not merely reported as a generic blunder.

### Blunder overrides

Blunder also fires when:

- the mover was not losing (`win_before >= 50`) and the resulting position has
  a forced mate for the opponent (`walked_into_mate`), or
- the mover had a forced mate and `win_after < 50` (`dropped_forced_mate`).

### Guard rails

- A forced move is never Blunder, even in a lost position.
- A book move is never graded on raw cp loss.
- A normal capture/recapture in an already >90% winning position is not
  Brilliant.
- A sacrifice must be a real, legally capturable material loss after the best
  reply, not merely a tactical offer the engine refutes.
- A "best move" always gets zero loss, even if the eval drifts between two
  shallow searches.

## Phases

`phase_for_position(board, ply, last_book_ply)`:

- **endgame**: non-pawn material on both sides is `<= 13` points
  (N/B/R/Q, values 3/3/5/9). Material wins precedence, so a quick queenless
  ending is not called "opening".
- **opening**: otherwise, `ply <= max(20, last_book_ply)`.
- **middlegame**: everything else.

Phase metrics count non-book, non-forced moves. `endgame` is never assigned
merely because the game is short.

## Web / bridge contract

The API is versioned separately from the review report:

- Game state `schema_version`: 1
- Review report `schema_version`: 1, `phase`: 3
- API metadata `schema_version`: 3

### Review methods

```
start_game_review(preset="standard", options=None)
cancel_game_review()
get_game_review(full=True)
```

`preset` is `fast`, `standard` or `deep`. Options:

- `depth`: optional integer 6–24 (confirmation is `depth + 2`, capped 18)
- `use_book`: default true; false grades the opening too (tests/debugging)
- `force`: default false; true starts a fresh run even if the same line and
  preset already completed

`start_game_review` returns:

```json
{
  "review": { "status": "queued", "signature": "...", "progress": {...} },
  "evaluation": {...}
}
```

`get_game_review(full=true)` returns:

```json
{
  "review": { "status": "complete", "summary": {...}, "opening": {...} },
  "report": { "schema_version": 1, "phase": 3, "moves": [...] }
}
```

The compact `review` block is also embedded in every game state and engine
poll, so the frontend can render progress without a second call. Its stable
fields are:

```
available, status, signature, run_id, game_id, preset, preset_label,
depth, confirm_depth, started_at, finished_at, move_count,
progress, summary, opening, error
```

On completion it also carries `engine` and `timing`; cancellation may add
`reason`; the full form adds `report_available` and `report`. There is no
top-level `multipv` (read `engine.multipv`) and the compact block never includes
the move list. That is the point of the separate full request.

Status values: `idle`, `queued`, `running`, `complete`, `cancelled`, `failed`.
On `complete`, the full report is available through `get_game_review`.

### Full report shape

```json
{
  "available": true,
  "schema_version": 1,
  "phase": 3,
  "status": "complete",
  "game_id": "...",
  "signature": "...",
  "preset": "standard",
  "preset_label": "Standard",
  "engine": {
    "name": "Stockfish",
    "version": "17",
    "depth": 14,
    "confirm_depth": 16,
    "multipv": 3
  },
  "model": {
    "winprob": "lichess-logistic",
    "winprob_version": 2,
    "accuracy": "cc-103.1668",
    "accuracy_review": "cc-103.1668100711649+1",
    "eval_clamp_cp": 1500,
    "review_clamp_cp": 1000,
    "initial_cp": 15,
    "initial_win_prob_white": 51.3828,
    "mate_cp": 10000,
    "uncertainty_bonus": 1.0
  },
  "players": { "white": "Alice", "black": "Bob" },
  "result": "1-0",
  "opening": {
    "eco": "C50",
    "name": "Italian Game",
    "variation": null,
    "last_book_ply": 5,
    "source": "eco",
    "full_name": "Italian Game"
  },
  "totals": {
    "plies": 40,
    "book_moves": 5,
    "forced_moves": 1,
    "graded_moves": 35,
    "searched_positions": 35,
    "confirmed_positions": 4,
    "counts": {
      "brilliant": 1, "great": 1, "best": 20, "excellent": 5,
      "good": 4, "book": 5, "inaccuracy": 2, "mistake": 1,
      "miss": 0, "blunder": 1
    }
  },
  "summary": {
    "white": {
      "color": "white",
      "accuracy": 88.1,
      "weighted_accuracy": 89.0,
      "harmonic_accuracy": 87.2,
      "acpl": 24.0,
      "acpl_trimmed": 22.5,
      "moves": 20,
      "graded_moves": 17,
      "counted_moves": 16,
      "scored_moves": 16,
      "book_moves": 3,
      "forced_moves": 1,
      "counts": { "...": 0 },
      "estimated_elo": {
        "elo": 1800, "unanchored": 1800, "anchored": null,
        "baseline": null, "method": "unanchored", "delta_r": null,
        "components": { "...": 0 }
      },
      "phases": {
        "opening": { "accuracy": 95.0, "acpl": 10.0, "moves": 8 },
        "middlegame": { "accuracy": 84.0, "acpl": 30.0, "moves": 8 },
        "endgame": { "accuracy": null, "acpl": null, "moves": 0 }
      },
      "blunder_rate": 0.0,
      "mistake_rate": 0.0
    },
    "black": { "...": "same shape" },
    "accuracy": { "white": 88.1, "black": 84.2 },
    "acpl": { "white": 24.0, "black": 31.0 },
    "acpl_trimmed": { "white": 22.5, "black": 29.0 },
    "estimated_elo": { "white": 1800, "black": 1600 },
    "counts": { "...": 0 }
  },
  "progress": { "done": 40, "total": 40, "phase": "complete", "percent": 100.0 },
  "timing": {
    "started_at": "2026-10-21T00:00:00.000Z",
    "finished_at": "2026-10-21T00:00:05.000Z",
    "elapsed_ms": 5000,
    "search_time_ms": 4200,
    "searches": 36,
    "cache_hits": 0,
    "terminal_positions": 1,
    "avg_search_ms": 116.7
  },
  "moves": []
}
```

### Move record

Each move has:

```
ply, move_number, color, san, uci, from, to, fen_before, fen_after,
is_book, is_forced, legal_moves, phase, opening_eco, opening_name,
eval_before, eval_after, win_before_white, win_after_white,
win_before, win_after, mate_before, mate_after,
best_uci, best_san, best_eval, pv, multi_pv, depth,
cp_loss, win_loss, accuracy,
classification, symbol, label, explanation,
sacrifice_info, brilliant_gates, multi_pv_cliff,
played_rank, needs_confirmation, confirmed
```

Book moves keep `classification: "book"`, `is_book: true`, null eval fields,
`cp_loss: null`, `win_loss: null`, `accuracy: null`. Forced moves keep
`is_forced: true` and classification `best`; their accuracy/ACPL values still
exist on the record but aggregation excludes them.

`multi_pv` entries expose `rank`, `uci`, `san`, `win_prob_white`,
`score_cp`, and `mate`. `pv` is the best line's UCI list.

## Caching and invalidation

Key: `sha256(fen + depth + multipv + engine_version + model_version)`.
Store `{best_cp, best_uci, pv, played_cp, wdl, depth}`. A lookup for depth
`D` may return a cached result with depth `>= D`; it must **never** return a
shallower result.

Review identity is a signature over `(game_id, start_fen, move_ucis)`.
Navigation inside the game does **not** invalidate or cancel the review.
Adding/removing a move, loading a new PGN/FEN, resetting, or starting a bot
turn does invalidate it. Cancellation uses a dedicated event plus
`engine.stop()` and a run id; stale worker callbacks are ignored.

## Failure modes to guard against

- **Sign errors**: grading Black with White POV cp turns good Black moves into
  blunders. The cp delta is negated for Black; test both colors.
- **Best move drift**: never charge the engine's own #1 move a loss; set
  `cp_loss = 0` and `win_loss = 0`.
- **Engine not ready**: `analyse` before `isready` can hang. Always handshake.
- **Stale search**: after `stop`, discard `info` lines from the cancelled job;
  tag each job with a run id and ignore mismatches.
- **Dead engine**: a crashed Stockfish returns no `bestmove`. Timeout, restart,
  and surface a retryable error to the UI.
- **Mate conversion**: `mate 0` and `mate -0` are not "no mate"; handle 0.
- **Missing MultiPV**: grading a played move outside the PV needs its own
  search or MultiPV 3. Never reuse the best line's eval for the played move.
- **Book detection**: do not treat "first 10 plies" as book; use the bundled
  ECO trie (3,865 lines, all 500 ECO codes) and optional Polyglot extension.
- **Depth drift**: a cache from depth 10 may be used for depth 10 or shallower,
  never for a deeper request. Pass 2 must request the higher depth explicitly.
- **Formula drift**: the review curve and the legacy curve are different. Do
  not edit one to match the other without versioning the model.
- **Book-only game**: no engine searches, `accuracy: null`, `acpl: 0`, and a
  complete progress block at 100%.

## Testing checklist

- Win% at `cp = 0, ±100, ±400, ±1000`; review clamp at ±1000; mate ±1, ±5.
- Initial prior exactly `review_win_probability(15)` on the standard start.
- Review accuracy at `loss_win = 0, 10, 20, 30` against the exact constants;
  legacy accuracy remains frozen at the rounded constants.
- Property: `cp_loss >= 0`, `0 <= accuracy <= 100` for random inputs.
- Symmetry: a position and its colour-flipped twin produce identical accuracy
  after POV conversion.
- Volatility window `L = clamp(floor(N/10), 2, 8)`, boundary padding, population
  standard deviation, clamp `[0.5, 12]`.
- Accuracy blend equals the 50/50 weighted/harmonic formula.
- ACPL trim only at 8+ moves; forced and book excluded.
- Estimated Elo unanchored, anchored, rounded and clamped.
- All ten labels at their boundaries; forced rolls into best and is marked.
- Brilliant gates: each gate can reject; pass-1 pending and pass-2 confirmed.
- Great: cliff >= 10, non-trivial capture, pass-2 confirmed.
- Miss: previous error + held position + missed mate.
- Blunder overrides: walked into mate, dropped forced mate.
- Two-pass pipeline: base depth, MultiPV 3, confirm positions `{ply-1, ply}`,
  confirm depth +2, up to 3 rounds, pass 2 can downgrade.
- Terminal checkmate/draw positions do not search.
- Cancellation stops before the next search and never completes a report.
- Book-only game: zero searches, book records, null accuracy.
- Signature invalidation on move change; navigation does not cancel.
- Golden payload keys for a 4-ply game, including `cp_loss`, `win_loss`,
  `accuracy`, `classification`, `confirmed`, totals and timing.
- Full pytest suite and `python -m chess_master --smoke-test`.

## References

- Stockfish UCI docs: <https://official-stockfish.github.io/docs/stockfish-wiki/UCI-chess-engine.html>
- python-chess engine API: <https://python-chess.readthedocs.io/en/latest/engine.html>
- Lichess win% model: <https://github.com/lichess-org/lila>
- Lichess accuracy model: `AccuracyPercent.scala`, PR #11148
- Chess.com accuracy discussion (community reverse engineering): search
  "chess.com accuracy formula 103.1668"
- Chess.com Game Review announcement: <https://www.chess.com/news/view/game-review>
- Bundled openings: `lichess-org/chess-openings` (CC0), 3,865 named lines

Update this section with the exact engine build and any calibration datasets
used, so a future change can be reproduced.
