# Chess Master — Phase 4 Engineering Report

**Release:** v1.0.0  
**Phase:** 4 of 4 — Game Review dashboard and final packaging  
**Status:** Complete  
**Date:** 22 October 2026  
**Environment:** Windows 11, Python 3.11+, PyWebView 6, Chromium/Edge WebView2,
Stockfish 19, Chessground 9.2.1, chess.js 1.4.0

---

## 1. Executive summary

Phase 4 turns the existing review engine into a complete Chess.com-style Game
Review workspace and closes the four-phase product at v1.0.0. The release adds:

- a dual-arrow board layer with orientation-aware classification badges and
  destination tints;
- an interactive SVG advantage chart with hover details, click-to-jump and a
  playhead;
- a coach card with move explanations, accuracy, evaluation transitions, forced
  moves and a playable PV follow-up sandbox;
- a side-by-side player summary with accuracy, estimated Elo, ACPL and phase
  splits;
- a ten-tier White/Black classification matrix with filtering and jump-to-move;
- key-moment navigation through buttons, `K`, `Shift+K` and `Space`;
- review entry points for finished games and PGN imports;
- a v1.0.0 README, release metadata and final QA captures.

The phase keeps the Phase 3 visual language and architecture: the same dark
studio shell, jade/brass accents, Fira Sans/Fira Code typography, 368 px
inspector and additive JSON report contract.

---

## 2. Scope and constraints

The Phase 4 brief required the review dashboard, board overlays, chart, coach,
PV sandbox, player cards, matrix, key moments, CTAs, screenshot QA, tests,
build, smoke test and final documentation. The following constraints shaped the
implementation:

1. **Preserve Phase 3 architecture and tokens.** No rewrite of the shell, state
   model or engine bridge.
2. **Additive backend contract.** Existing Phase 3 fields stay valid; new fields
   are optional for older consumers.
3. **No extra engine owner.** The review uses the existing single-owner
   Stockfish worker and cancellation policy.
4. **One main line per session.** The PV sandbox previews engine moves without
   mutating the game or creating a variation tree.
5. **Local-first and offline.** No remote fonts, chart library or runtime
   services. The chart is hand-built SVG.
6. **Windows desktop first.** The layout must remain usable at 1280 × 820 and
   1440 × 900 without horizontal scrolling.

---

## 3. User experience overview

A typical review flow is:

1. Finish a game or import a PGN. The Moves panel shows a review CTA.
2. Choose **Analyze in Game Review**. The app switches to the Review tab and
   starts a Fast, Standard or Deep review.
3. The dashboard appears with the evaluation curve, coach, player cards,
   matrix and move list.
4. Click a point on the chart, a matrix row/count, a move row or a key-moment
   control. The board, badge, arrows, coach and chart playhead follow the
   selected ply.
5. Open **Show Follow-Up** to replay the engine continuation on the board.
   Step, play, pause or return with **Back to Game** or `Escape`.
6. Filter the move list from the matrix to study one tier, then clear the
   filter.

The result is a single scrolling inspector with a stable board on the left. No
modal workflow interrupts the review.

---

## 4. Visual system

Phase 4 reuses the Phase 3 tokens and adds component tokens only where needed.

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#080b0f` | App canvas |
| `--panel` | `#10151b` | Cards, inspector |
| `--jade` | `#5fd7a4` | Primary action, engine/PV accents |
| `--brass` | `#d8b26e` | Review/warm metadata |
| `--text` | `#e8edf2` | Primary copy |
| `--muted` | `#8a97a5` | Secondary copy |
| `--line` | `rgba(255,255,255,.08)` | Card borders |
| `--font-sans` | Fira Sans | UI and prose |
| `--font-mono` | Fira Code | Eval, counts, keyboard hints |
| Inspector width | `368px` | Stable review column |

### Classification palette

| Tier | Badge gradient | Symbol |
| --- | --- | --- |
| Brilliant | `#26c2a3 → #1baca6` | `!!` |
| Great | `#3b82f6 → #5c8bb0` | `!` |
| Best | `#22c55e → #81b64c` | `★` |
| Excellent | `#96bc4b → #7fa53a` | `✓` |
| Good | `#97af8b → #7c9274` | `○` |
| Book | `#d97706 → #a88865` | book icon |
| Inaccuracy | `#eab308 → #f7c631` | `?!` |
| Mistake | `#f97316 → #e68a00` | `?` |
| Miss | `#f43f5e → #ee6b5e` | `✕` |
| Blunder | `#ef4444 → #ca3431` | `??` |

The same colours drive badges, arrows, matrix swatches, coach pills and chart
dots. The palette is intentionally distinct at small sizes and remains legible
against both light and dark board squares.

### Layout

- **1280 × 820:** sidebar 248 px, board column fluid, inspector 368 px. The
  inspector scrolls; the board stays fully visible.
- **1440 × 900:** wider board and the same inspector width, showing chart,
  coach and player cards above the matrix fold.
- **Below 1100 px:** the shell stacks the inspector under the board; the review
  remains one vertical column.
- **Reduced motion:** existing `prefers-reduced-motion` rules suppress the
  chart/arrow transitions; no new infinite animation was added.

---

## 5. Architecture

### 5.1 Backend

```
game session (authoritative main line)
        │
        ▼
ReviewRequest ──► opening book ──► pass 1 searches (MultiPV 3)
        │                              │
        │                              ▼
        │                        base records
        │                              │
        │                        pass 2 confirmation
        │                              │
        ▼                              ▼
win sequence ◄──────────── _classify_all ─────────► player metrics
        │                              │
        └──────────► eval_chart ◄──────┘
                       key_moments
                       report v2
```

Key properties:

- Pass 1 searches every position from the end of book to the final ply.
- Pass 2 re-searches Brilliant/Great candidates at a deeper depth to confirm
  sacrifice and only-move gates.
- Terminal positions get synthetic evaluations; Stockfish is never asked to
  search a checkmate.
- The report is built from a frozen `ReviewRequest` signature
  `(game_id, start_fen, moves)`, so navigating during a review cannot corrupt
  the result.
- Cancellation is cooperative and checked between searches.

Primary file: `src/chess_master/analysis/review.py`.

### 5.2 Frontend

```
app.js
  ├─ owns active tab, game state, orientation, preview state
  ├─ computes board scene via reviewPanel.boardScene(...)
  ├─ calls board.render(...) / board.setReviewOverlay(...)
  └─ forwards report updates to reviewPanel.update(...)

reviewPanel.js
  ├─ report cache and filter state
  ├─ chart render + tooltip + playhead
  ├─ coach render + PV state machine
  ├─ player cards, matrix, key moments, move list
  └─ boardScene(...) contract

board.js
  ├─ Chessground instance + auto shapes
  ├─ DOM overlay: square tint + classification badge
  ├─ orientation-aware square geometry
  └─ deferred arrow redraw after layout
```

The panel never mutates the game. Navigation is delegated to `app.navigateTo`,
which calls `navigate_ply` and re-renders. Preview state lives only in
`reviewPanel.preview` and is cleared on tab change, move-list navigation,
key-moment jumps, new game, import and `Escape`.

### 5.3 Data flow for a review selection

1. User clicks a chart point / matrix row / move row.
2. `reviewPanel` calls `onNavigate(ply)`.
3. `app` calls the bridge, receives the new game state and updates `app.game`.
4. `app` asks `reviewPanel.boardScene(game, orientation, preview)`.
5. `board` renders the FEN, arrows, tint and badge.
6. `reviewPanel` updates the chart playhead, coach, key-moment counter and
   current move row.

This keeps a single source of truth: the backend game cursor.

---

## 6. Review report contract

`REVIEW_SCHEMA_VERSION = 2`; `report.phase = 4`. New fields are additive.

### 6.1 Per-move fields

| Field | Type | Meaning |
| --- | --- | --- |
| `classification` | string | One of the ten tiers |
| `symbol`, `label` | string | Display tokens |
| `is_book`, `is_forced`, `confirmed` | bool | Metadata |
| `explanation` | string | Coach copy |
| `accuracy` | float | Per-move accuracy 0–100 |
| `win_loss` | float | Win-probability loss in percentage points |
| `cp_loss` | float | Centipawn loss, clamped |
| `eval_before`, `eval_after` | int | White-POV centipawns |
| `win_before_white`, `win_after_white` | float | White win probability |
| `best_uci`, `best_san`, `best_eval` | mixed | Engine reference |
| `best_move` | object | `{uci, san, from, to, score_cp, mate, win_prob_white, is_played, rank}` |
| `pv` | string[] | PV from the position before the move |
| `pv_followup` | object | Playable continuation after the played move |
| `multi_pv` | object[] | Up to three ranked lines |
| `phase` | string | `opening`, `middlegame`, `endgame` |

### 6.2 PV follow-up

`pv_followup` is generated from the engine line **after the played move**:

```json
{
  "start_fen": "...",
  "uci": ["d7d8", "..."],
  "san": ["Qd8+", "..."],
  "fens": ["start", "...after each ply"],
  "moves": [
    {
      "uci": "d7d8",
      "san": "Qd8+",
      "from": "d7",
      "to": "d8",
      "ply": 30,
      "move_number": 15,
      "color": "black",
      "fen_before": "...",
      "fen_after": "..."
    }
  ],
  "depth": 16,
  "source": "after_position"
}
```

The line is capped at **8 plies**. The frontend uses the supplied FENs and SANs
and performs no engine calls or move generation during step/play.

### 6.3 Chart

`eval_chart`:

```json
{
  "total_plies": 33,
  "midline": 50.0,
  "points": [
    {
      "ply": 0,
      "win_prob_white": 50.3,
      "eval_cp": 15,
      "mate": null,
      "classification": null,
      "san": null,
      "uci": null,
      "move_number": null,
      "color": null,
      "is_book": false,
      "accuracy": null,
      "win_loss": null
    }
  ]
}
```

A point exists for ply 0 and every main-line ply. Book plies carry the initial
prior or the first available evaluation so the curve remains continuous.

### 6.4 Key moments

`key_moments` is ordered by ply and contains Brilliant, Great, Inaccuracy,
Mistake, Miss and Blunder events. Each entry carries
`{ply, move_number, color, san, uci, classification, symbol, label, accuracy,
win_loss, explanation}`. The frontend highlights the active moment and uses the
list for `Prev`/`Next` and `K`/`Space`.

### 6.5 Summary

`summary` includes, for each colour:

- accuracy (volatility-weighted mean of arithmetic and harmonic move accuracy);
- ACPL raw and trimmed (top 10 % trimmed when at least 8 graded moves);
- estimated Elo in `[400, 3000]`, rounded to the nearest 50;
- graded moves, best moves, counts by tier;
- opening/middlegame/endgame accuracy and graded counts.

---

## 7. Classification and metrics

### 7.1 Ten tiers

The classifier is deterministic and follows the canonical order:

1. **Book** — inside the bundled opening line; no grading.
2. **Best** — engine top choice, zero loss, or the only legal move (forced).
3. **Brilliant** — six gates: engine optimality, genuine sacrifice, legal
   capture, not a trapped/already-lost piece, win floor/ceiling, structural
   exclusions (not in check, not a queen promotion, at least two legal moves).
4. **Great** — engine-optimal or near-optimal with a MultiPV cliff of at least
   10 percentage points, position not already decided.
5. **Excellent** — win loss below 2 points.
6. **Good** — win loss below 5.
7. **Inaccuracy** — win loss below 10.
8. **Miss** — after an opponent mistake/blunder, a large loss that fails to
   punish, or a missed forced mate.
9. **Mistake** — win loss below 20.
10. **Blunder** — win loss at least 20, walking into mate, or dropping a forced
    mate.

Forced moves roll up to Best in the matrix while remaining visually distinct
with a `Forced` pill in the coach and `· forced` in the move list.

### 7.2 Accuracy

Per-move accuracy uses the approved curve:

```
accuracy = 103.1668 * exp(-0.04354 * win_loss) - 3.1669
```

Player accuracy is volatility-weighted: the arithmetic mean and harmonic mean
are blended 50/50, with a short window around volatile stretches.

### 7.3 ACPL and Elo

- Raw ACPL is the mean centipawn loss among graded moves.
- Trimmed ACPL removes the highest 10 % when at least 8 moves exist.
- Estimated Elo blends an ACPL component (`3100 * exp(-0.01 * acpl_trim)`) and
  an accuracy component (`400 + 2600 / (1 + exp(-0.10 * (accuracy - 75)))`),
  55/45, plus a consistency penalty for raw/trim divergence.
- When a known baseline rating is supplied, the estimate is anchored with the
  expected accuracy/ACPL curve. The displayed value is capped at 400–3000 and
  rounded to 50.

### 7.4 Phases

- **Endgame:** total non-pawn material for both sides ≤ 13 points.
- **Opening:** ply ≤ max(20, last book ply).
- **Middlegame:** everything else.

Material wins precedence, so a quick queenless ending is not labelled opening.
When a phase has no graded moves, the UI shows `—`.

---

## 8. Board overlay

`BoardController` now owns a DOM overlay inside the board holder. The overlay is
positioned from the live `cg-board` rectangle, not from percentage guesses.

### 8.1 Geometry

For a square such as `b5`:

1. Convert the file letter to `0..7` and the rank to `0..7`.
2. If orientation is black, mirror both axes (`7 - value`).
3. `size = boardWidth / 8`.
4. `left = boardLeft - holderLeft + col * size`.
5. `top = boardTop - holderTop + row * size`.

This makes the badge and tint correct after any board resize, sidebar change or
window resize. The overlay is purely decorative and `pointer-events: none`.

### 8.2 Badge

- Placed at the destination square.
- Size: 40 % of a square, clamped to 17–30 px.
- Top-right offset: 28 % of the badge size, so the badge sits on the corner
  without covering the piece centre.
- Book uses an inline SVG book glyph; other tiers use their symbol.
- Tooltip: `Classification — SAN`.
- The badge flips with the board because its square box is orientation-aware.

### 8.3 Tint

A translucent square tint is drawn first, then the badge. The tint is 1 px
inset and 2 px smaller than the square, keeping grid lines readable. Its colour
comes from the classification token.

### 8.4 Dual arrows

When the viewed ply is at least 1:

- the played move is always drawn with its classification brush;
- if `best_move.uci !== played.uci` and the move is not book, the best
  alternative is drawn with the emerald `alternative` brush;
- arrows are cleared at ply 0 and when no report/move exists.

Draw order places the alternative first and the played move second, so the
played move remains visually dominant when the arrows overlap.

### 8.5 Arrow layout regression

During QA, arrows occasionally rendered at invalid coordinates. The cause was
Chessground computing arrow geometry before the board had a non-zero bounding
box. The fix:

1. `setArrows` stores the shapes and schedules a `requestAnimationFrame`.
2. `drawArrows` checks that the `cg-board` width/height are finite and at least
   8 px.
3. If not, it retries up to 120 frames.
4. Before setting new shapes it clears `setAutoShapes([])` to defeat
   Chessground's shape-hash short-circuit.

This eliminated the `NaN` coordinates and stale arrows observed before the fix.

---

## 9. Interactive advantage chart

The chart is a hand-built SVG (`viewBox 0 0 320 140`) with no chart library.

### 9.1 Rendering

- X axis: ply 0–N.
- Y axis: White win probability 0–100, mapped with padding.
- White area: from the curve to the 50 % midline, filled white at low opacity.
- Black area: from the midline to the curve, filled dark.
- A 50 % dashed midline anchors the interpretation.
- Critical-point dots: Brilliant, Great, Miss and Blunder; smaller neutral dots
  for other plies.
- Playhead: vertical dashed line and a dot at the current ply.
- Foot: current move label and evaluation.

### 9.2 Interaction

- Transparent per-ply hit rectangles cover the full plot height, including both
  end points.
- Hover shows a tooltip: `Move · SAN`, then `Eval · Classification`.
- Clicking a hit area navigates to that ply and reveals the coach.
- Pointer leave hides the tooltip.
- Keyboard support when focused: `←`/`→`, `Home`, `End`.

### 9.3 QA observation

At ply 29 the tooltip read `15. Bxd7+` / `+3.4 · Mistake`; clicking the point
moved the game cursor to ply 29 and the board showed the played and best arrows.

---

## 10. Coach and PV sandbox

### 10.1 Coach

The coach card contains:

- move number + SAN (e.g. `15. Bxd7+?`);
- classification pill and optional `Forced` pill;
- move accuracy;
- evaluation transition (e.g. `+8.6 → +3.4`);
- explanation paragraph;
- best alternative line (`Best was Bxf6 (+8.6).`);
- `Show Follow-Up` when a PV exists.

The card is an `aria-live="polite"` region so navigation updates are announced
without stealing focus.

### 10.2 PV state machine

States: `idle → previewing(index 1..N) → playing → done`.

- `Show Follow-Up` starts at index 1.
- `← Step` and `Step →` clamp to `[0, N]`; step-back at 1 goes to the start FEN.
- `Play Line` advances every 850 ms; the button becomes `Pause Line`, then
  `Replay Line` at the end.
- `Back to Game` restores the base ply and clears the preview.
- `Escape` clears the preview from anywhere outside an input/dialog.
- Move-list navigation, key moments, tab changes, new game and import also
  clear the preview.
- The banner is rendered once, inside the coach, to avoid a duplicate overlay
  covering the board. It reads
  `Previewing Engine Line (Move X of N) — Click 'Back to Game' to exit`.

### 10.3 Board during preview

- The board renders the preview FEN supplied by the backend.
- A single jade `pv` arrow shows the current continuation move.
- No badge/tint is shown in preview; those belong to the reviewed game move.
- The preview does not write to the game line and does not start engine work.

---

## 11. Player summary and classification matrix

### 11.1 Player cards

Side-by-side cards show:

- avatar initials and name;
- colour and move count;
- estimated Elo;
- large accuracy, with the higher value highlighted in jade;
- ACPL, graded moves and best moves;
- opening/middlegame/endgame accuracy bars.

Missing data is shown as `—` rather than a misleading zero.

### 11.2 Matrix

The matrix is a 3-column grid: `[White count] [Badge + tier] [Black count]`.

- Rows are ordered Brilliant → Blunder.
- Zero counts are disabled but still visible, preserving the full ten-tier
  model.
- Clicking a row filters the move list to that tier across both colours and
  jumps to the next matching ply at or after the current cursor.
- Clicking a count filters that tier and colour only.
- The filter bar shows `Filtering <Tier> · <colour> (<n>)` with a
  `Clear filter` action.
- Counts always come from the full report, never from the filtered list.

---

## 12. Key moments, CTAs and keyboard

### 12.1 Key moments

- `Prev key moment` and `Next key moment` buttons are disabled at the ends.
- The counter shows `n / total`.
- `K` and `Space` move forward; `Shift+K` moves back.
- `Space` is ignored when the focused element is a button/link, so it does not
  double-activate controls.
- Navigation of any kind updates the coach, chart playhead, board and move-list
  highlight.

### 12.2 Review CTA

The Moves panel shows a CTA when:

- the game is finished (checkmate, draw, resignation), or
- the game was imported from PGN with at least one ply.

Copy:

- title `Game complete` or `Game imported`;
- body explains grading/turning points;
- label `Analyze in Game Review` when no report exists, `Open Game Review` when
  one does.

Clicking it switches to Review and either opens the existing report or starts a
new review with the current preset.

### 12.3 Keyboard map

| Key | Action |
| --- | --- |
| `←` / `→` | Previous/next ply |
| `Home` / `End` | Start/live |
| `F` | Flip |
| `E` | Eval rail |
| `A` | Best-move arrow |
| `I` | Import |
| `K` / `Space` | Next key moment |
| `Shift+K` | Previous key moment |
| `Escape` | Exit PV preview |
| Chart focused `←`/`→`/`Home`/`End` | Step the chart |

---

## 13. QA evidence

### 13.1 Functional matrix

| # | Check | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Fast review completes on the Opera Game | PASS | `33 plies · 9 key moments · 4 confirmed`, 89.9 % / 81.9 % |
| 2 | Badge on b5 after `10. Nxb5` | PASS | Brilliant `!!` at top-right of b5 |
| 3 | Badge with board flipped | PASS | `?!` stayed on b5/cxb5 after `F` |
| 4 | Dual arrows at ply 29 | PASS | orange `b5→d7`, emerald `g5→f6` |
| 5 | Arrow redraw after resize/orientation | PASS | no stale/NaN arrows after the deferred redraw fix |
| 6 | Chart hover tooltip | PASS | `15. Bxd7+` / `+3.4 · Mistake` |
| 7 | Chart click-to-jump | PASS | cursor moved to ply 29 |
| 8 | Key-moment buttons | PASS | `6 / 9 → 7 / 9` |
| 9 | `K` / `Shift+K` / `Space` | PASS | `19→20`, `20→19`, `19→20` |
| 10 | PV open, step, play, finish | PASS | `1 of 8 → 2 of 8 → 8 of 8`, Replay Line |
| 11 | PV Back to Game / Escape | PASS | preview cleared, base ply restored |
| 12 | Matrix count filter | PASS | `Filtering Mistake · white (2)` |
| 13 | Matrix row filter + jump | PASS | `Filtering Inaccuracy (4)`, jumped to ply 20 |
| 14 | Clear filter | PASS | full 33-ply list restored |
| 15 | Game-over CTA | PASS | Fool's mate `0–1` → `Analyze in Game Review` → Review COMPLETE |
| 16 | Console errors | PASS | 0 after adding the favicon |
| 17 | 1280 × 820 layout | PASS | `docs/phase4-review-1280x820.png` |
| 18 | 1440 × 900 layout | PASS | `docs/phase4-review-1440x900.png` |
| 19 | Follow-up at 1440 × 900 | PASS | `docs/phase4-followup-1440x900.png` |

### 13.2 Opera Game review snapshot

| Metric | White (Morphy) | Black (Duke Karl / Count Isouard) |
| --- | --- | --- |
| Accuracy | 89.9 % | 81.9 % |
| ACPL | 57.1 | 87.8 |
| Estimated Elo | ≈ 2550 | ≈ 1750 |
| Graded moves | 13 | 13 |
| Best moves | 8 | 5 |
| Opening accuracy | 87.9 % | 77.1 % |
| Middlegame accuracy | 91.0 % | 92.7 % |
| Endgame accuracy | — | — |

Classification totals: White 1 Brilliant, 1 Great, 8 Best, 1 Good, 4 Book,
2 Mistake; Black 5 Best, 3 Good, 3 Book, 4 Inaccuracy, 1 Mistake.

Timing for the Fast preset: 30 searches, 0 cache hits, 0.8 s, 4 confirmed
positions.

### 13.3 CTA scenario

A Fool's Mate game (`1. f3 e5 2. g4 Qh4#`, result `0–1`) rendered the CTA in the
Moves panel with `Analyze in Game Review`. Clicking it switched to Review and
completed a Standard review (`4 plies · 0 key moments`, all four plies Book).
This verifies both the finished-game condition and the start/focus behaviour.

### 13.4 Console and network

The browser QA run reported **0 console errors** after adding an inline SVG
favicon. Before the favicon, Chromium logged a single 404 for `/favicon.ico`;
that was the only console issue found.

---

## 14. Tests, build and smoke

### 14.1 Pytest

```text
180 passed in 9.21s
```

Coverage includes the review report contract, PV follow-up, chart payload, key
moments, classifier gates, phase metrics, ACPL, estimated Elo, JSON/HTTP
transports and the existing game/engine suite.

Review-specific tests live in:

- `tests/test_review_pipeline.py` — full pipeline, report schema, follow-up,
  chart and key moments.
- `tests/test_classifier.py` — ten tiers, Brilliant gates, forced moves.
- `tests/test_metrics.py` — accuracy, ACPL, Elo, phase splits.
- `tests/test_openings.py` — book detection.

### 14.2 Build

```text
npm run build
→ vendor sync (Chessground, chess.js, 12 captured-piece SVGs)
→ tailwindcss 4.3.3
→ Done in 223ms
```

### 14.3 Desktop smoke test

```text
.\.venv\Scripts\python.exe -m chess_master --smoke-test
2026-10-22 10:31:15,538 INFO chess_master.bridge.web_server: Serving Chess Master UI at http://127.0.0.1:40550/
2026-10-22 10:31:15,540 INFO chess_master.app: Smoke-test window created (hidden); closing in 3 seconds.
```

---

## 15. Screenshots

All three are captured at exact CSS viewport sizes with Chromium headless at
device scale factor 1.

| File | Size | Contents |
| --- | --- | --- |
| [`phase4-review-1440x900.png`](phase4-review-1440x900.png) | 1440 × 900 | Opera Game at ply 29: dual arrows, `?` badge, chart, coach, players, matrix |
| [`phase4-review-1280x820.png`](phase4-review-1280x820.png) | 1280 × 820 | Brilliant `10. Nxb5`, jade arrow, coach, players, compact layout |
| [`phase4-followup-1440x900.png`](phase4-followup-1440x900.png) | 1440 × 900 | PV sandbox at Move 2 of 8, coach banner and controls |

The 1440 review capture intentionally shows the matrix partially below the fold;
the inspector is a single scroll region, so this is expected at 900 px height.

---

## 16. Files changed

### Backend

| File | Change |
| --- | --- |
| `src/chess_master/analysis/review.py` | `best_move`, `pv_followup`, `eval_chart`, `key_moments`; schema 2, phase 4; PV cap 8 |
| `src/chess_master/analysis/classifier.py` | Ten-tier deterministic classifier and Brilliant gates (Phase 3, reused) |
| `src/chess_master/analysis/metrics.py` | Accuracy, ACPL, Elo and phase metrics (Phase 3, reused) |
| `src/chess_master/analysis/openings.py` | Book detection (Phase 3, reused) |
| `src/chess_master/bridge/api.py` | `PHASE = 4`, review methods and payloads |
| `src/chess_master/engine/game.py` | Game state `phase = 4` |
| `src/chess_master/__init__.py` | `__version__ = "1.0.0"` |

### Frontend

| File | Change |
| --- | --- |
| `src/chess_master/web/index.html` | Review dashboard markup, CTA, icons, Phase 4/v1.0.0 labels, `K` hint, favicon |
| `src/chess_master/web/src/styles.css` | Board overlay, badge/tint, chart, coach, PV controls, player cards, matrix, filter, CTA |
| `src/chess_master/web/assets/css/app.css` | Compiled output |
| `src/chess_master/web/assets/js/board.js` | Overlay geometry, badge/tint, preview arrows, deferred arrow redraw |
| `src/chess_master/web/assets/js/reviewPanel.js` | Chart, coach, PV state machine, players, matrix, filter, key moments |
| `src/chess_master/web/assets/js/app.js` | Preview state, board scenes, CTA, keyboard, report callbacks, debug API |

### Packaging and docs

| File | Change |
| --- | --- |
| `pyproject.toml`, `package.json`, `package-lock.json` | Version `1.0.0` |
| `README.md` | v1.0.0 feature/launch/test/review documentation |
| `docs/PHASE4_REPORT.md` | This report |
| `docs/phase4-*.png` | Final QA captures |
| `tests/test_review_pipeline.py` | Follow-up, chart and key-moment assertions |
| `tests/test_api.py`, `tests/test_http_engine.py`, `tests/test_game.py` | Phase assertions updated to 4 |

---

## 17. Deliberate decisions and trade-offs

1. **PV starts after the played move.** It answers “what did the engine see
   next?” and keeps the preview line legal from the current board. The
   alternative arrow still communicates the move the engine preferred before
   the played move.
2. **PV capped at 8 plies.** Longer lines made the step counter and board
   context noisy; 8 plies is enough to show the tactical point.
3. **Single preview banner in the coach.** A board overlay banner covered rank
   1/8 pieces and duplicated the coach message. The coach banner stays next to
   `Back to Game`, and the board remains unobstructed.
4. **Preview does not update the coach prose.** The coach continues to explain
   the reviewed move while the banner and step counter identify the preview
   line. This avoids rewriting the report card during a sandbox.
5. **No chart library.** A 320 × 140 SVG with per-ply hit rectangles is smaller,
   themeable and keyboard-accessible without a dependency.
6. **Zero-count matrix rows stay visible.** The matrix is a learning aid; hiding
   empty tiers would make the model look different from game to game.
7. **Filter jumps forward.** Clicking a tier jumps to the next match at or after
   the cursor, preserving the user's place instead of always starting at ply 1.
8. **Inline favicon.** A data-URI favicon removes the only browser console error
   without adding a binary asset.

---

## 18. Known limits and follow-ups

- One engine process per app instance; a second table cannot run concurrently.
- No persistence: the report and cache live in memory for the API lifetime.
- One main line; PGN variations remain metadata.
- Estimated Elo is a single-game heuristic, not a tournament rating.
- PV is capped at 8 plies and uses the engine line after the played move.
- The review pipeline is CPU-bound and intentionally cooperative; a Deep review
  on a long game will take longer than Fast.
- The HTTP API is loopback-only and intended for development/QA.

Possible future work: persisted review history, a variation tree, a report
export (PDF/PNG), and a shared review link for the web transport.

---

## 19. Exact run commands

```powershell
# Desktop
.\.venv\Scripts\python.exe -m chess_master

# Development browser + JSON API
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m chess_master --serve --port 8771

# Build UI assets
npm run build

# Full test suite
.\.venv\Scripts\python.exe -m pytest -q

# Desktop smoke test
.\.venv\Scripts\python.exe -m chess_master --smoke-test
```

For the QA captures, the loopback server was started at port 8771, the Opera
Game sample was loaded, and a Fast review was completed before navigating to
ply 19/29.

---

## 20. Release checklist

- [x] Review report schema v2 and phase 4
- [x] Ten-tier classification, accuracy, ACPL, Elo and phases
- [x] On-board badges, tints and dual arrows
- [x] Orientation-aware overlay and flip QA
- [x] Interactive SVG chart with hover, playhead and click-to-jump
- [x] Coach with best alternative and PV sandbox
- [x] Player summary and classification matrix
- [x] Key moments and keyboard shortcuts
- [x] Finished-game and import review CTAs
- [x] 180 pytest checks passing
- [x] `npm run build` passing
- [x] Desktop smoke test passing
- [x] 1280 × 820, 1440 × 900 and follow-up screenshots
- [x] Zero console errors in QA
- [x] README and Phase 4 report
- [x] Version 1.0.0 across Python and npm metadata

**Phase 4 is complete. Chess Master v1.0.0 is ready for local use.**
