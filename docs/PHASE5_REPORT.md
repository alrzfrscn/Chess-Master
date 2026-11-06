# Chess Master — Phase 5 Engineering Report

**Release:** v1.0.0  
**Phase:** 5 — Clocks, sound and local game archive  
**Status:** Complete  
**Date:** 5 November 2026  
**Environment:** Windows 11, Python 3.11+, PyWebView 6, WebView2, Stockfish 19,
Chessground 9.2.1, chess.js 1.4.0, Tailwind CSS v4

---

## 1. Executive summary

Phase 5 adds the three pieces a desktop chess studio needs before it feels like
a real table: an authoritative chess clock, a synthesized sound engine, and a
local SQLite archive for finished games and their Game Review reports.

The phase keeps the existing four-layer architecture and the Phase 4 visual
language. All changes are additive:

- time controls are optional per game, so existing unlimited games behave
  exactly as before;
- the clock is owned by the Python session, never by the browser;
- sound is a dependency-free Web Audio module with no new packages;
- archive methods sit beside the existing bridge methods and use the same
  `{ok, data, error}` envelope;
- the public `phase` metadata stays at `4` for compatibility, while
  `feature_phase` reports `5` and `schema_version` moves to `4`.

Verification: **215 pytest tests pass**, `npm run build` succeeds, the HTTP
bridge round-trip passes an end-to-end clock/archive check, and
`python -m chess_master --smoke-test` starts and closes the hidden window.

---

## 2. Scope and constraints

The brief required:

1. Professional time controls with bullet/blitz/rapid presets, custom controls
   and an unlimited option.
2. A backend-authoritative clock that settles elapsed time on moves and polls,
   awards increment, detects flag fall and locks the game.
3. Chess.com-style clocks on both player banners with 100 ms client
   interpolation and an immediate poll at zero.
4. A time-control picker in the “Start a new game” dialog.
5. A zero-dependency Web Audio sound engine with mute persistence and cues for
   moves, navigation, previews and game termination.
6. SQLite persistence for games and reviews, plus save/list/load/delete/clear/
   export API methods.
7. New clock and storage tests, with every existing test still green.
8. This report, the production build and the smoke test.

Constraints that shaped the implementation:

- **Backend authority.** The browser interpolates but never decides remaining
  time. Only the Python session can flag a player.
- **Additive contracts.** `clocks` is a new payload field; existing fields and
  the public phase number do not change.
- **No new runtime dependencies.** Sound uses the platform `AudioContext`;
  storage uses the standard-library `sqlite3`.
- **Preserve archive fidelity.** Archived games replay their UCI moves so
  `fen_before`, captures and material strips are reconstructed exactly.

---

## 3. User experience

### 3.1 Player clocks

Each player banner gains a clock beside the captured-material strip:

- `M:SS` while at least twenty seconds remain;
- `0:SS.d` with tenths below twenty seconds;
- amber treatment below twenty seconds and crimson with a slow pulse below ten;
- the active side is highlighted, and the increment is shown as `+N`;
- unlimited games hide the clock entirely.

The browser repaints both clocks every 100 ms from a single anchor. The anchor
is refreshed by every backend poll, so drift is bounded by one poll interval
while the display stays smooth. When the locally interpolated clock reaches
zero, the UI calls `engine_poll` immediately instead of waiting for the next
240 ms tick. The backend then flags the player and returns the terminal state.

### 3.2 Time-control picker

The new-game dialog groups controls exactly as requested:

| Group | Choices |
| --- | --- |
| Unlimited | No clock |
| Bullet | `1+0`, `2+1` |
| Blitz | `3+0`, `3+2`, `5+0` |
| Rapid | `10+0`, `15+10` |

The selected control is passed as the `time_control` option for both Pass &
Play and Stockfish games. The picker mirrors the current game’s control when
the dialog opens.

### 3.3 Terminal behaviour

A flag fall produces `state: "timeout"`, `reason: "time_forfeit"`, a winner,
and a PGN result. The board locks because human move checks already require
`in_progress`; pending bot work is cleared so the engine cannot move after the
flag. Both banners keep their final times, with the flagged side shown at zero.

---

## 4. Architecture

### 4.1 Backend clock

New module: `src/chess_master/engine/clock.py`.

- `TIME_CONTROL_PRESETS` holds the seven presets with `initial_ms`,
  `increment_ms`, `label` and `category`.
- `ClockControl` is an immutable value object.
- `resolve_time_control(value)` accepts `None`, `"unlimited"`, a preset name,
  or a custom mapping. It rejects booleans, negative values, zero initial time
  and unknown categories with `InvalidInputError`.
- `GameClock` owns `white_ms`, `black_ms`, `active_color`, `running` and
  `last_tick_monotonic`. `tick()` deducts elapsed time from the side on move;
  `apply_move()` settles the mover, awards increment only when time remains,
  and switches the active side; `flagged_color()` reports a flag fall.

### 4.2 Session integration

`GameSession` owns the clock:

- `new_game()` reads `options["time_control"]` and constructs the clock from
  the root position’s side to move.
- `status()` first synchronizes the clock, then returns a stored timeout or the
  rule-based status. A timeout therefore takes precedence over mate/draw
  detection for the same instant.
- `make_move()` settles and increments through `apply_move()` after the move is
  validated, stops the clock on a terminal position, and raises `GameOverError`
  once timed out.
- `reset()` rebuilds a fresh clock from the same control.
- `to_state()` adds a `clocks` payload; unlimited sessions return
  `enabled: false` with null times.

The clock payload is:

```json
{
  "enabled": true,
  "control": "3+2",
  "label": "3+2",
  "category": "blitz",
  "initial_ms": 180000,
  "increment_ms": 2000,
  "white_ms": 178420,
  "black_ms": 182000,
  "active_color": "black",
  "running": true,
  "flagged": false
}
```

### 4.3 Coordinator

`EngineCoordinator.clear_pending_bot()` drops a ready or queued bot move without
touching the game line. `engine_poll` calls it when the session reports a
timeout. Bot scheduling also now checks `session.is_over`, so a clock forfeit
cannot be followed by an engine move.

### 4.4 Frontend clock

`players.js` exports:

- `anchorClock(game)` — stores server times plus a `performance.now()` anchor;
- `remainingClock(color)` — server time minus live elapsed time for the side
  on move;
- `formatClock(ms)` — the `M:SS` / `0:SS.d` rule;
- `renderClocks(game, orientation, refs)` — repaints both banners.

`app.js` anchors on every state application and re-anchors on every poll even
when the position fingerprint is unchanged, so a clock-only update does not
trigger a full re-render. A 100 ms interval renders the clocks and requests an
immediate poll once when the active side reaches zero. The zero-poll key is
`game_id:revision:ply`, which prevents a poll storm while still reacting to the
next revision.

The clock is deliberately excluded from `stateFingerprint()`: clock updates are
frequent and must not rebuild the move list, board or inspector.

---

## 5. Sound engine

New module: `src/chess_master/web/assets/js/sound.js`.

`SoundManager` synthesizes every cue with oscillators, gain envelopes and short
noise bursts; there are no audio files and no new dependencies.

| Cue | Used for |
| --- | --- |
| `move` | quiet move / navigation / PV preview |
| `capture` | capture |
| `castle` | castling |
| `promote` | promotion |
| `check` | check (delayed slightly after the move) |
| `brilliant` | available for review celebration |
| `retry_correct` / `retry_wrong` | available for review retry flows |
| `game_over` | checkmate and draw |
| `timeout` | flag fall buzzer |

Wiring:

- human move after `make_move`;
- bot move when `engine_poll` reports `moved`;
- ply navigation;
- PV preview when its index changes;
- terminal outcome via `announceOutcome`.

Autoplay policy is respected: the audio context is created or resumed on the
first pointer/key event, and every call is wrapped so audio can never break the
board. Mute is persisted as `localStorage["chessmaster.sound.muted"]`, toggled
by the toolbar button or `M`, and reflected by the volume / volume-off icon and
the “Sound” / “Muted” label.

---

## 6. Local archive

### 6.1 Location and connection

`paths.py` adds:

```text
STATE_ROOT      = <project>/state
DEFAULT_DB_PATH = <project>/state/chess_master.db
```

`state/` is created at import. `.gitignore` excludes `state/*.db*` and WAL/SHM
side files. `ChessMasterApi(db_path=...)` accepts a path or `:memory:`, which
keeps tests and tooling isolated from the user archive. `GameStore` enables
foreign keys, WAL for file databases, a 5 s timeout and an `RLock` around every
operation.

### 6.2 Schema

`games` stores identity and game metadata, both player names, engine Elo,
player color, time-control label, result, termination state/reason/winner, ply
count, start/final FEN, PGN, `moves_json`, `headers_json`, opening fields,
accuracy/ACPL/estimated-Elo columns, brilliant/blunder counts and the review
signature/preset.

`reviews` stores one report per game: `game_id` primary key, `signature`,
`preset`, `schema_version`, `completed_at` and `report_json`, with a cascading
foreign key back to `games`.

### 6.3 Auto-save lifecycle

The API saves automatically:

- when a game reaches `checkmate`, `draw` or `timeout` (once per
  `game_id + plies + state`);
- when a game with at least two plies is about to be replaced by `new_game`,
  `reset_game`, `load_pgn` or `load_fen`;
- after `load_pgn`, so an imported study is immediately in the archive;
- when a Game Review completes, which also writes accuracy, ACPL, estimated
  Elo, opening, brilliant/blunder counts and the full report JSON.

### 6.4 Archive API

| Method | Purpose |
| --- | --- |
| `save_current_game()` | write the live session, even mid-game |
| `list_archived_games(limit=50, offset=0, mode=None, query=None)` | paged rows, total, aggregate stats and DB info |
| `load_archived_game(game_id)` | rebuild the session and restore a stored review |
| `delete_archived_game(game_id)` | remove one game; its review cascades |
| `clear_game_archive()` | delete all rows, vacuum and reset save guards |
| `export_archive_pgn(game_ids=None)` | concatenate all or selected PGNs |

Listing supports mode filtering, name/event/opening search and paging. Export
returns the PGN text, block count and a dated filename.

### 6.5 Restore semantics

`GameSession.from_archive()` replays the stored UCI moves on the stored start
FEN. This rebuilds `fen_before`, captures, material and status without trusting
derived text fields. The restored clock is stopped; a timeout archive pins the
loser at zero. Custom controls are preserved through an `X-ClockMs` header
(`initial_ms+increment_ms`) written with the archived headers, while preset
labels such as `3+2` resolve directly.

When a review row exists, `EngineCoordinator.restore_review()` adopts the stored
report. The review dashboard opens with accuracy, ACPL, opening, counts and the
full report without re-running Stockfish.

---

## 7. Tests

New modules:

- `tests/test_clock.py` — 18 tests: preset table, custom validation, unlimited
  behaviour, tick/increment arithmetic, no increment after a flag, first-move
  activation, flag precedence, reset and archive round-trip.
- `tests/test_storage.py` — 10 tests: terminal auto-save, PGN auto-save,
  idempotent manual save, filters/search/paging, review persistence and restore,
  fresh-process restore, batch export, delete/clear, structured missing-game
  errors and custom-clock timeout archives.
- `tests/conftest.py` — an autouse fixture redirects the default database to a
  temporary path so existing API tests never write into the user’s real
  `state/` archive.

Result:

```text
215 passed in 11.94s
```

Existing coverage for the engine, API, review pipeline, PGN/FEN, metrics and
worker remains green.

---

## 8. Verification

Commands run from the repository root:

```powershell
npm run build
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m chess_master --smoke-test
```

- `npm run build`: vendor sync and Tailwind CSS minification succeed.
- `pytest -q`: `215 passed`.
- Smoke test: hidden PyWebView window opens and closes cleanly.
- HTTP bridge check: started `chess_master --serve`, created `3+2` and custom
  clock games over `/api/*`, confirmed increment, flag fall, auto-save, restore,
  export and the `feature_phase: 5` metadata.
- Browser QA: verified the grouped picker, both banner clocks, tenths under
  twenty seconds, timeout result (`0–1`, “Black wins on time”), locked board and
  the sound toggle (`Sound` ↔ `Muted`).

---

## 9. Known limits and follow-ups

1. **Archive restore resets remaining clock time.** The required schema stores
   only the control label, so a loaded game resumes with full initial time and
   a stopped clock rather than the exact remaining milliseconds. Upgrading
   would need two integer columns or a richer clock JSON column.
2. **No archive browser UI yet.** The six API methods are complete and tested,
   but Phase 5 did not add a dedicated archive screen; the archive is ready for
   the next UI phase.
3. **Sound is synthesized, not sampled.** This keeps the build dependency-free
   and small; replacing voices later only touches `VOICES` in `sound.js`.
4. **Browser audio needs a gesture.** The first sound may be silent until the
   user clicks or presses a key, which is the platform’s autoplay policy.
