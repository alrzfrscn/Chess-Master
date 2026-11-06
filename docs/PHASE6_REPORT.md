# Chess Master — Phase 6 Engineering Report

**Release:** v1.0.0  
**Phase:** 6 — Retry Mistake and the local Game History dashboard  
**Status:** Complete  
**Date:** 6 November 2026  
**Environment:** Windows 11, Python 3.11+, PyWebView 6, WebView2, Stockfish 19,
Chessground 9.2.1, chess.js 1.4.0, Tailwind CSS v4

---

## 1. Executive summary

Phase 6 turns Game Review from a report you read into a lesson you can practise,
and gives the local archive a face. It adds two connected features:

- **Retry Mistake** — a frontend-only sandbox that resets the board to the
  position before an inaccuracy, mistake, miss or blunder and asks the player to
  find the engine's move. Hint, Show Solution, wrong-move snap-back, accepted
  alternatives and a green `★ Best` result are included.
- **History / Archive** — a dashboard with KPIs, an accuracy/Elo trend, search,
  mode filters, per-game actions and database controls, all reading the SQLite
  archive introduced in Phase 5.

The retry sandbox never mutates the Python `GameSession`. The archive needed no
new tables or columns: the existing store gained aggregate stats, a trend query,
ECO-aware search and a metrics-preservation rule. The public `phase` remains `4`
for compatibility, while `feature_phase` reports `6` and `schema_version` stays
at `4`.

Verification: **217 pytest tests pass**, `npm run build` succeeds,
`python -m chess_master --smoke-test` opens and closes the hidden window, and the
headless browser QA finished with `PHASE6_QA_OK` across retry, 3+2 clocks,
archive actions, search, PGN export, delete and reset.

---

## 2. Scope and constraints

The brief required:

1. A `↻ Retry` action for graded mistakes, with keyboard `R`.
2. The board set to the position before the bad move, with the best arrow and
   badge hidden and the played move shown as a ghost/amber marker.
3. Hint, Show Solution, Cancel (`Esc`) and Continue Review.
4. Exact-best acceptance with `retry_correct` and a green `★ Best`; strong
   MultiPV alternatives within a 2.0% win-probability loss accepted; wrong moves
   rejected with `retry_wrong` and a snap-back after roughly 450 ms.
5. A History sidebar entry (`H`), a `HISTORY` tab with a count badge, KPI strip,
   accuracy/Elo SVG trend, search, mode pills and per-game actions.
6. A database card with the real path, size, copy path, full PGN export and a
   confirmed archive reset.
7. Extended tests, a production build, a smoke test and this report.

Constraints that shaped the implementation:

- **The backend game line is sacred.** A retry is a view state; the archived
  moves, review report and session clocks never change.
- **Additive data model.** Phase 5's schema already stores every field the
  dashboard needs, so the phase avoids a migration.
- **No new runtime dependencies.** The dashboard and retry use the existing
  Chessground, chess.js. Tailwind and the standard `sqlite3`.
- **Review remains the source of truth.** Retry eligibility and alternatives
  come from the report's `best_move` and `multi_pv`, never from a second engine
  search.

---

## 3. User experience

### 3.1 Retry Mistake

The Review coach shows `↻ Retry` for any non-book ply classified as
`inaccuracy`, `mistake`, `miss` or `blunder`, provided the engine's best move
differs from the move played. `R` starts the same action.

When retry starts:

- the board is rebuilt from `move.fen_before` through chess.js;
- the best-move arrow and the played-move badge disappear;
- the played move stays visible as an amber ghost arrow and tint;
- the coach switches to a retry prompt with `Hint`, `Show Solution` and
  `Cancel`;
- piece dragging, click-to-move and promotion are routed to the sandbox only.

Feedback:

| Event | Result |
| --- | --- |
| Exact engine move | `retry_correct`, green `★ Best`, `Correct!` |
| Alternative MultiPV line within 2.0% win-probability loss | `retry_correct`, `Correct!`, alternative explained |
| Other legal move | `retry_wrong`, `Not quite`, amber ghost, snap-back after 450 ms |
| Hint | Ring on the best move's origin square |
| Show Solution | Plays the best move in the sandbox and marks it `★ Best` |
| Cancel / `Esc` | Returns to the frozen review position |
| Continue Review | Exits the sandbox and resumes at the next key moment, or the next ply |

The retry sandbox does not touch `GameSession`: navigating, closing the panel or
finishing the game leaves the archived line exactly as it was.

### 3.2 History / Archive

A new `History / Archive` sidebar item (`H`) and a `HISTORY` tab share one panel
and a live count badge. The panel contains:

- **KPI strip** — Total games, Win / Draw / Loss, average accuracy across
  reviewed games, peak estimated Elo.
- **Progress** — a two-series SVG trend of accuracy and estimated Elo over the
  last twelve reviewed games. Empty and single-point states are handled.
- **Search** — player names, event, opening name and ECO code.
- **Mode pills** — All, Stockfish, Pass & Play, Imported.
- **Game rows** — mode, time control, date, result/reason, both names, opening
  and ECO, accuracy, estimated Elo and brilliant count, plus `Open`, `Review`,
  `Export PGN`, `Copy FEN` and `Delete`.
- **Local database card** — exact path, size, `Copy path`, `Export all PGN` and
  `Reset archive` behind a destructive confirmation.

Opening a row restores the game and its stored review without replaying
Stockfish. Review jumps straight to the restored report. Delete removes one row
and its review; Reset clears the archive, vacuums SQLite and leaves the live
table untouched.

---

## 4. Architecture

### 4.1 Retry sandbox

Files: `reviewPanel.js`, `board.js`, `app.js`.

`ReviewPanel` owns a small state machine:

```text
awaiting ── hint ───────────────▶ awaiting (ring visible)
    │
    ├── wrong legal move ───────▶ wrong ── 450 ms ──▶ awaiting
    ├── best / alternative ─────▶ correct
    ├── Show Solution ──────────▶ solution
    └── Cancel / Esc ───────────▶ exited
```

Each phase produces a `boardScene()`:

- `awaiting` — amber ghost arrow, played-move tint, optional hint ring, no badge;
- `wrong` — miss arrow and tint on the attempted move;
- `correct` / `solution` — green best arrow, `★` badge on the destination;
- `alternative` — good arrow and tint on the accepted alternative.

`Board` gained an optional `sandbox` render path: FEN, orientation and
interaction come from the sandbox while the live game and evaluation overlays
are bypassed. Promotion uses the sandbox FEN and mover colour.

`app.js` routes board moves and promotion choices to `handleRetryMove()` when a
retry is active, re-renders on retry changes, exits retry when leaving Review,
and blocks navigation shortcuts while the sandbox is open.

Acceptance rules:

- same UCI as `best_move` → correct;
- otherwise a `multi_pv` line with rank > 1, the same UCI, and a mover
  win-probability loss below 2.0 percentage points → accepted alternative;
- anything else → wrong.

### 4.2 Archive queries

Files: `storage/db.py`, `bridge/api.py`.

`GameStore.stats()` now returns `avg_accuracy` (mean of both sides only when both
are present) and `peak_elo` (best of the two estimates). `history_trend(limit=12)`
returns reviewed games in chronological order with `accuracy` and
`estimated_elo`. Search matches `opening_eco` as well as names, event and opening
name.

`upsert_game()` uses `COALESCE(excluded.column, games.column)` for the
review-derived columns. Re-archiving a reviewed game — for example when opening
another game — no longer erases its accuracy, ACPL, estimated Elo,
brilliant/blunder counts or review signature with NULLs.

`clear_game_archive()` records the live session's review signature and terminal
key after clearing, so the next engine poll cannot immediately resurrect the
game that was just deleted. The live table is still available through
`save_current_game()`.

### 4.3 Archive API

Existing methods keep their contract and now return the dashboard payload:

| Method | Phase 6 addition |
| --- | --- |
| `list_archived_games(...)` | `stats.avg_accuracy`, `stats.peak_elo`, `trend`, `db_info` |
| `load_archived_game(game_id)` | unchanged restore semantics |
| `delete_archived_game(game_id)` | returns fresh `db_info` |
| `clear_game_archive()` | returns fresh stats and `db_info` |
| `export_archive_pgn(...)` | unchanged all/selected export |
| `get_api_info()` | `feature_phase: 6`, capabilities `retry_mistake`, `history_dashboard` |

### 4.4 Frontend history module

New module: `historyPanel.js`.

- `refresh()` loads one 200-row page (the server caps at 500) and renders KPIs,
  the SVG trend, filters and rows.
- `refreshCount()` uses a limit-1 query for the tab and sidebar badges.
- `renderTrend()` draws two polylines plus dots in SVG; a single reviewed game
  still renders its points.
- Actions call the bridge with the existing `{ok, data, error}` envelope and
  reuse the app's toast and confirmation dialogs.
- Rows are built with `textContent`, never interpolated HTML.

### 4.5 Web server reliability

Browser QA exposed a real dev-server defect: Chromium opens a burst of parallel
ES-module requests, and the standard-library HTTP server's default listen
backlog of 5 refused some of them (`ERR_CONNECTION_REFUSED`). The server now
uses a backlog of 128. It also swallows `ConnectionError` from responses
aborted by a page reload or a closed tab instead of printing tracebacks.

---

## 5. Tests

New and extended coverage:

- `tests/test_storage.py::test_archive_stats_trend_and_eco_search` — average
  accuracy, peak Elo, reviewed count, trend shape and ECO search.
- `tests/test_storage.py::test_rearchive_preserves_review_metrics` — a plain
  re-archive keeps accuracy, ACPL, estimated Elo, brilliant counts and the
  review signature.
- Existing archive, review, clock and API suites remain green.

Result:

```text
217 passed in 21.04s
```

Browser QA (`phase6-qa.mjs`, Playwright + headless Edge, 1440×900) covered:

- a fresh Opera Game import and fast review;
- retry activation, hint ring, wrong-move rejection and snap-back, best-move
  acceptance, Continue Review, `R` and `Esc`;
- a 3+2 Pass & Play game with running clocks, fast review, retry of `d6??` and
  acceptance of `Qh4#`;
- history KPIs, two-point trend, search for `Morphy`, Imported filter;
- row PGN download and full-archive PGN download;
- Open and Review restoration from the archive;
- delete with confirmation and full reset with confirmation;
- no page errors and no console errors.

Final line:

```text
PHASE6_QA_OK
```

Screenshots:

- `docs/phase6-clocks-retry-1440x900.png`
- `docs/phase6-history-1440x900.png`

---

## 6. Verification

Commands run from the repository root:

```powershell
npm run build
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m chess_master --smoke-test
node C:\Users\Aa\AppData\Local\Temp\opencode\qa-pw\phase6-qa.mjs
```

- `npm run build`: vendor sync and minified Tailwind CSS succeed.
- `pytest -q`: `217 passed`.
- Smoke test: hidden PyWebView window created and closed cleanly.
- Browser QA: `PHASE6_QA_OK` as described in section 5.
- HTTP bridge: archive APIs return `trend` and `db_info`; `feature_phase` is `6`.

---

## 7. Known limits and follow-ups

1. **Retry is view state.** Reloading the window or leaving Review discards the
   sandbox. That is intentional: retry must not mutate the game or the archive.
2. **Review depth affects classifications.** At fast depth the Opera Game's
   `15.Bxd7+` can be graded either as the best move or as a blunder with
   `Bxf6` suggested, depending on the search. Retry always trusts the report it
   is given. A deeper preset is the way to settle a disputed position.
3. **History loads one 200-row page.** There is no paging control yet; the
   server supports 500. The row limit is marked with a `shortcut:` comment.
4. **Reset archive suppresses immediate re-save of the live session.** The table
   remains playable and can be archived again with `Save current game` or by
   changing the line/review. Without this guard the next poll recreated the
   deleted row.
5. **Trend uses reviewed games only.** Games without a completed review still
   appear in the list and KPIs, but not in accuracy/Elo history.
