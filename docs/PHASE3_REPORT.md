# Chess Master — Phase 3 Engineering Report

**Version:** 0.3.0  
**Phase:** 3 — Game Review, classification, accuracy and estimated Elo  
**Date:** 2026-10-21  
**Status:** Complete. Phase 4 was not started.

---

## 1. Scope and outcome

Phase 3 adds a Chess.com-style Game Review to the existing Pass & Play and
Stockfish application:

- Bundled ECO opening detection from 3,865 named lines covering all 500 ECO
  codes (A00–E99), with an optional Polyglot extension.
- Two-pass Stockfish review. Pass 1 grades every non-book ply at MultiPV 3;
  pass 2 re-searches the positions around Brilliant/Great candidates two plies
  deeper (capped at 18) and re-grades the line.
- All ten canonical classifications plus forced-move metadata. Book and forced
  moves are excluded from accuracy/ACPL; forced moves roll into `best` in the
  summary.
- Canonical Lichess win probability, review accuracy curve, volatility-weighted
  game accuracy, trimmed ACPL, phase splits and estimated Elo.
- Three bridge methods (`start_game_review`, `cancel_game_review`,
  `get_game_review`) and a compact review block in game state and engine polls.
- A functional Review tab with preset selection, progress, summary, opening,
  classification counts, a graded move list and board navigation.
- 179 passing pytest checks, a passing smoke test, real-engine benchmarks and
  interactive browser QA.

Phase 3 deliberately leaves deeper product work — shared review history,
opening explorer, cloud sync, accounts, advanced training — to a later phase.

---

## 2. Directory tree and module map

```text
Chess Master/
|-- src/chess_master/
|   |-- __init__.py                 Package version: 0.3.0
|   |-- analysis/
|   |   |-- __init__.py
|   |   |-- winprob.py              Lichess logistic, legacy and review accuracy curves
|   |   |-- openings.py             ECO trie, EPD lookup, optional Polyglot
|   |   |-- openings_data.tsv       3,865 CC0 opening lines, 500 ECO codes
|   |   |-- classifier.py           Ten labels, forced metadata, Brilliant/Great gates
|   |   |-- metrics.py              Volatility, accuracy, ACPL, phases, estimated Elo
|   |   `-- review.py               Two-pass pipeline and report contract
|   |-- bridge/
|   |   `-- api.py                  Review methods, capabilities, state/poll integration
|   |-- engine/
|   |   |-- stockfish.py            Review job, MultiPV search, cache, cancellation
|   |   |-- coordinator.py          Review lifecycle, signature invalidation, payloads
|   |   `-- game.py                 Phase 3 idle review block and state version
|   `-- web/
|       |-- index.html              Functional Review-tab markup
|       |-- src/styles.css          Review panel visual system
|       `-- assets/
|           |-- css/app.css         Compiled stylesheet
|           `-- js/
|               |-- app.js          Review polling fingerprint and API exposure
|               `-- reviewPanel.js  Presets, progress, summary and graded move list
|-- tests/
|   |-- test_openings.py            Dataset, trie, EPD and deviation tests
|   |-- test_metrics.py             Volatility, accuracy, ACPL, phases and Elo tests
|   |-- test_classifier.py          Labels, forced roll-up and gate tests
|   `-- test_review_pipeline.py     Two-pass, book, terminal, cache and cancellation
|-- docs/PHASE3_REPORT.md           This report
|-- package.json                    UI version 0.3.0
|-- package-lock.json               UI lockfile 0.3.0
`-- pyproject.toml                  Package metadata 0.3.0, analysis/*.tsv package data
```

### New or substantially changed modules

| Module | Responsibility |
| --- | --- |
| `analysis/openings.py` | Loads the bundled ECO TSV, builds a UCI-prefix trie, resolves EPD transpositions, and optionally extends `last_book_ply` from Polyglot. |
| `analysis/classifier.py` | Deterministic ten-label classifier, forced roll-up, material sacrifice detection, six Brilliant gates and Great gates. |
| `analysis/metrics.py` | Volatility windows, weighted/harmonic accuracy blend, trimmed ACPL, phase splits and estimated Elo. |
| `analysis/review.py` | Validates the line, chooses searched positions, runs pass 1/pass 2, builds records, summaries, timing and the versioned report. |
| `analysis/winprob.py` | Adds exact review constants, `review_win_probability`, `move_accuracy_review` and the canonical clamp/prior while freezing Phase 1/2 values. |
| `engine/stockfish.py` | `submit_review`, review search callback, `get_at_least` cache reuse, terminal synthesis, run-id and cancellation events. |
| `engine/coordinator.py` | Review start/cancel/payload, progress/completion callbacks, signature invalidation, bot-turn cancellation and review-aware scheduling. |
| `bridge/api.py` | Three review methods, validation, `capabilities.game_review`, method/phase metadata and compact review in state/poll. |
| `web/assets/js/reviewPanel.js` | Review UI lifecycle, presets, polling updates, report fetch, stats, opening, counts, move list and navigation. |
| `web/assets/js/app.js` | Review panel construction, state fingerprint, board navigation and `window.chessMaster` API exposure. |

---

## 3. Opening detection

`analysis/openings_data.tsv` is derived from the CC0
`lichess-org/chess-openings` dataset:

- 3,865 named lines.
- 500 distinct ECO codes (A00–E99).
- Each row stores ECO, English name, legal UCI line and final EPD.
- The default book is loaded once with an in-memory UCI prefix trie.
- Detection follows the line until the first move that leaves named theory,
  returning the deepest named opening and `last_book_ply`.
- EPD lookup handles transpositions.
- A local Polyglot `.bin` can extend `last_book_ply`; ECO names still come from
  the bundled dataset.
- Custom start positions and `use_book: false` skip book grading.

A full replay test validates every bundled UCI line against python-chess.

---

## 4. Two-pass review pipeline

### 4.1 Identity and invalidation

A review signature is derived from:

```text
(game_id, start_fen, move_ucis)
```

Navigation inside the same game line does **not** change the signature, so
browsing the move list or clicking another ply never cancels a running review.
Adding/removing a move, loading another PGN/FEN, resetting or starting a bot
turn invalidates the review. A dedicated cancellation event plus the engine's
`analysis.stop()` stops the worker, and a monotonically increasing run id makes
stale callbacks harmless.

### 4.2 Presets

| Preset | Pass 1 | Pass 2 | MultiPV |
| --- | ---: | ---: | ---: |
| Fast | 10 | 12 | 3 |
| Standard | 14 | 16 | 3 |
| Deep | 16 | 18 | 3 |

Custom depths are accepted from 6 to 24. Confirmation is
`min(base_depth + 2, 18)`. If the base depth is already 18, candidates cannot
be confirmed and remain visibly `confirmed: false` rather than pretending.

### 4.3 Position selection

- The opening detector produces `last_book_ply`.
- Only positions from `last_book_ply` through the final position need searches.
  Each searched position is the "before" or "after" position of a graded move.
- A completely book game runs zero Stockfish searches.
- Checkmate, stalemate, insufficient material, seventy-five-move and fivefold
  repetition positions are synthesized without a search.
- Pass 2 re-searches `{ply - 1, ply}` for every pending Brilliant/Great
  candidate, up to three rounds, then re-grades the entire line.

### 4.4 Progress and reporting

The compact review block is included in every game state and engine poll:

```
available, status, signature, run_id, game_id, preset, preset_label,
depth, confirm_depth, started_at, finished_at, move_count,
progress, summary, opening, engine, timing, error, reason
```

`start_game_review` returns the compact block plus the current live
evaluation. `get_game_review(full=true)` returns the compact block plus the
single full report. The report is versioned `schema_version: 1`, `phase: 3`.

---

## 5. Canonical formulas

The complete contract is in `.agents/skills/chess-game-review/SKILL.md`.
Implementation highlights:

- Win probability: `100 / (1 + exp(-0.00368208 * cp))`, ordinary review scores
  clamped to ±1000 cp. Mate maps to 0/100.
- Standard-start prior: +15 cp, or about 51.3828% for White.
- Review accuracy:
  `103.1668100711649 * exp(-0.04354415386753951 * loss) - 3.166924740191411 + 1.0`,
  clamped to 0–100, with exactly 100 at zero loss.
- The Phase 1/2 rounded accuracy curve is frozen and was not changed.
- Game accuracy: 50/50 blend of volatility-weighted mean and harmonic mean.
  Window `L = clamp(floor(N / 10), 2, 8)`, boundary padding from the initial
  window, population standard deviation, weights clamped to `[0.5, 12]`.
- ACPL: raw mean plus a 10% high-loss trim at 8+ moves, per-move cap 1000.
- Estimated Elo: documented unanchored blend and optional baseline-anchored
  delta, clamped 400–3000 and rounded to the nearest 50.
- Best-move rule: if the played move is the engine's #1 line, `cp_loss` and
  `win_loss` are forced to zero. This prevents shallow eval drift from
  penalizing the engine's own choice.

### Classification order

```
book -> forced (as best) -> brilliant -> great -> best -> excellent ->
good -> inaccuracy -> miss -> blunder -> mistake
```

- Book: inside the detected opening prefix; no cp/win grading.
- Forced: exactly one legal move; `is_forced: true`, rolled into `best`, excluded
  from accuracy and ACPL aggregation.
- Brilliant: six gates (optimality, real sacrifice, legal capture, not a
  trapped piece, win floor/ceiling, structural) plus pass-2 confirmation.
- Great: optimal or near-optimal, MultiPV cliff >= 10 win%, not a trivial
  capture, win floor/ceiling, plus pass-2 confirmation.
- Best: engine rank 1 or zero win loss.
- Excellent `< 2`, Good `< 5`, Inaccuracy `5–10`, Mistake `10–20`,
  Blunder `>= 20`.
- Miss overrides Blunder when the opponent's previous move was a mistake or
  blunder, the current move loses at least 10 win% or missed a forced mate,
  and the mover still holds within 5 win% of the pre-error position.
- Blunder also fires for walking into mate or dropping a forced mate.

Pass 2 can downgrade a shallow Brilliant/Great candidate. Tests cover both
confirmation and rejection.

---

## 6. Bridge and frontend

### API

`get_api_info()` now reports:

- `phase: 3`
- `capabilities.game_review: true`
- `capabilities.review_presets: ["fast", "standard", "deep"]`
- the three review methods in `methods`
- the canonical model metadata in `analysis`

`start_game_review` validates preset, depth, `use_book` and the game line. An
empty game returns `invalid_input`. A completed review is reused only when the
same line, preset and depth are requested; explicit `force: true` starts a new
run. This lets the UI's "Review again" re-grade at a different preset without
silently returning the old report.

### Review tab

The Review tab has:

- Fast / Standard / Deep preset cards with depth labels.
- Run/Cancel/Review-again state machine.
- Scan and confirmation progress with phase and done/total counts.
- Accuracy, ACPL and estimated Elo for both colors.
- ECO badge, opening name and "book ends after N plies".
- Count chips for every classification present, in canonical order.
- A scrollable move list with grade, win% loss and forced/book styling.
- Clicking a move navigates the board without cancelling a review.

The move list is loaded only after completion; polling stays compact.

---

## 7. Verification

### 7.1 Pytest

Baseline before Phase 3: 138 passing checks. Final:

```text
179 passed in 9.23s
```

New suites:

- `tests/test_openings.py` — dataset size, all 500 ECOs, every bundled UCI line
  legal, named-line detection, deviation, empty line, custom trie/EPD and
  Polyglot validation.
- `tests/test_metrics.py` — window sizes, padding/clamps, accuracy blend,
  trimmed ACPL, phases, review/legacy curves, Elo and forced/book exclusion.
- `tests/test_classifier.py` — ten labels, forced roll-up, book precedence,
  thresholds, Miss, mate overrides, Brilliant and Great confirmation gates,
  SEE/sacrifice metadata.
- `tests/test_review_pipeline.py` — full report contract, per-color ACPL,
  best-move zero loss, book-only zero searches, pass-2 Brilliant confirmation
  and rejection, Great confirmation, terminal mate, cancellation.
- `tests/test_api.py` — capabilities/methods, empty review validation, idle
  review read/cancel and JSON serialization.

### 7.2 Smoke test

```text
python -m chess_master --smoke-test
```

Result: hidden PyWebView window created, local asset server started, window
closed after three seconds, exit code 0.

### 7.3 Real-engine benchmark

A temporary harness (not shipped with the app) loaded The Opera Game through
`ChessMasterApi` and polled `get_game_review(full=true)` until completion. Each
preset used a fresh API instance and a cold review cache. The benchmark used
the bundled Stockfish binary and the production `StockfishManager` worker.

Fixture: The Opera Game (Morphy, 33 plies, 7 book plies, 27 searched positions,
30 searches including pass 2, 4 confirmed positions).

| Preset | Wall | Pipeline | Searches | Cache | Confirmed | White acc | Black acc | White ACPL | Black ACPL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fast (10/12) | 1.36 s | 0.98 s | 30 | 0 | 4 | 89.88 | 81.89 | 57.08 | 87.75 |
| Standard (14/16) | 10.08 s | 9.73 s | 30 | 0 | 4 | 97.84 | 86.95 | 6.46 | 71.25 |
| Deep (16/18) | 35.68 s | 35.28 s | 30 | 0 | 4 | 98.26 | 87.57 | 3.77 | 69.75 |

Immediate Fast rerun on the same worker:

| Run | Wall | Pipeline | Searches | Cache hits |
| --- | ---: | ---: | ---: | ---: |
| Fast cold | 1.36 s | 0.98 s | 30 | 0 |
| Fast warm | 0.03 s | 0.017 s | 30 | 29 |

Classification stayed stable where it should and moved with depth where the
search gained information:

| Preset | Brilliant | Great | Best | Excellent | Good | Book | Inaccuracy | Mistake | Miss | Blunder |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fast | 1 | 1 | 13 | 0 | 4 | 7 | 4 | 3 | 0 | 0 |
| Standard | 1 | 1 | 14 | 2 | 5 | 7 | 1 | 2 | 0 | 0 |
| Deep | 1 | 1 | 14 | 4 | 2 | 7 | 2 | 2 | 0 | 0 |

### 7.4 Browser QA

- Imported The Opera Game through the normal Import tab.
- Ran Fast and Deep reviews in the UI; Standard was exercised through the
  bridge benchmark.
- Verified the completed summary, ECO C41 Philidor Defense, 7 book plies,
  accuracy, ACPL, estimated Elo and classification count chips.
- Verified the graded move list, including Brilliant/Great labels, Best and book
  styling, and forced-move handling.
- Clicked graded moves and confirmed board navigation without cancelling the
  review.
- Verified preset changes update the depth/confirm label.
- Cancellation and stale-result handling are covered by the pipeline and
  coordinator tests.

Observed UI completion times on a warm desktop process: Fast about 0.7 s and
Deep about 28 s. The isolated cold benchmark above is the reproducible
measurement for all three presets.

---

## 8. Known limitations and deliberate choices

- **Two-pass cap:** confirmation never exceeds depth 18. A custom review already
  at 18 shows candidates as unconfirmed rather than spending unbounded time.
- **Estimated Elo is a heuristic:** it is a single-game mapping from accuracy,
  ACPL and blunder/mistake rates, not a rating calculation. Anchoring is
  available when a player rating is supplied.
- **Bundled book, optional Polyglot:** the shipped app has full ECO coverage but
  no Polyglot file. A local `.bin` can extend book depth; it does not supply ECO
  names.
- **Best-move drift:** the engine's top choice is always awarded zero loss even
  when a shallow after-search produces a different eval. This is intentional:
  grading the engine's own choice against itself would be circular.
- **Cache reuse:** `get_at_least` never returns a shallower result than the
  requested depth. Immediate repeats are nearly free; a deeper preset still
  pays for the deeper searches.
- **No Phase 4 work:** no accounts, cloud history, opening explorer, training
  plans or social features were added.

---

## 9. Final checklist

- [x] ECO detection with 3,865 lines / 500 ECO codes
- [x] Two-pass review at Fast 10, Standard 14, Deep 16; confirm +2 capped 18
- [x] MultiPV 3 on every engine search
- [x] Ten canonical labels plus forced metadata
- [x] Brilliant/Great pass-2 confirmation and downgrade path
- [x] Review accuracy, volatility-weighted game accuracy, trimmed ACPL
- [x] Phase splits and estimated Elo
- [x] Start/cancel/get review bridge methods
- [x] Compact review in state and poll; full report on demand
- [x] Review-tab UI and graded move navigation
- [x] 179 pytest checks passing
- [x] `python -m chess_master --smoke-test` passing
- [x] Real-engine benchmarks recorded
- [x] Canonical formulas documented in the game-review skill
- [x] Phase 4 not started
