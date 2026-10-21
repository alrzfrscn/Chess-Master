/** Game Review panel: presets, progress, summary and the graded move list. */

import { call, errorMessage } from "./api.js";
import { toast } from "./ui.js";

const PRESET_DEPTH = { fast: 10, standard: 14, deep: 16 };

const CLASS_META = {
  brilliant: { label: "Brilliant", symbol: "!!", order: 0 },
  great: { label: "Great", symbol: "!", order: 1 },
  best: { label: "Best", symbol: "★", order: 2 },
  excellent: { label: "Excellent", symbol: "✓", order: 3 },
  good: { label: "Good", symbol: "○", order: 4 },
  book: { label: "Book", symbol: "📖", order: 5 },
  inaccuracy: { label: "Inaccuracy", symbol: "?!", order: 6 },
  mistake: { label: "Mistake", symbol: "?", order: 7 },
  miss: { label: "Miss", symbol: "✕", order: 8 },
  blunder: { label: "Blunder", symbol: "??", order: 9 },
};

const PHASE_LABEL = {
  queued: "Queued",
  scan: "Scanning positions",
  confirm: "Confirming Brilliant / Great",
  aggregate: "Scoring the game",
  complete: "Complete",
  cancelled: "Cancelled",
  failed: "Failed",
};

function number(value, digits = 1) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : null;
}

function pair(values, digits = 1) {
  const white = number(values?.white, digits);
  const black = number(values?.black, digits);
  if (white === null && black === null) return "—";
  return `${white ?? "—"} · ${black ?? "—"}`;
}

export class ReviewPanel {
  constructor({ onNavigate } = {}) {
    this.onNavigate = onNavigate || (() => {});
    this.game = null;
    this.gameId = null;
    this.preset = "standard";
    this.review = null;
    this.report = null;
    this.loadedSignature = null;
    this.failedSignature = null;
    this.loadingReport = false;
    this.busy = false;
    this.lastKey = "";

    this.refs = {
      chip: document.getElementById("review-state-chip"),
      heroTitle: document.getElementById("review-hero-title"),
      heroCopy: document.getElementById("review-hero-copy"),
      presets: document.getElementById("review-presets"),
      run: document.getElementById("btn-run-review"),
      runLabel: document.getElementById("review-run-label"),
      cancel: document.getElementById("btn-cancel-review"),
      progress: document.getElementById("review-progress"),
      progressPhase: document.getElementById("review-progress-phase"),
      progressCount: document.getElementById("review-progress-count"),
      progressFill: document.getElementById("review-progress-fill"),
      model: document.getElementById("review-model-label"),
      accuracy: document.getElementById("review-accuracy"),
      acpl: document.getElementById("review-acpl"),
      elo: document.getElementById("review-elo"),
      opening: document.getElementById("review-opening"),
      openingEco: document.getElementById("review-opening-eco"),
      openingName: document.getElementById("review-opening-name"),
      openingMeta: document.getElementById("review-opening-meta"),
      counts: document.getElementById("review-counts"),
      moves: document.getElementById("review-moves"),
      moveList: document.getElementById("review-move-list"),
      note: document.getElementById("review-note"),
      noteCopy: document.getElementById("review-note-copy"),
    };

    this.wire();
    this.renderPreset();
  }

  wire() {
    this.refs.presets?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-review-preset]");
      if (!button || this.isRunning()) return;
      this.preset = button.dataset.reviewPreset;
      this.renderPreset();
    });
    this.refs.run?.addEventListener("click", () => this.start());
    this.refs.cancel?.addEventListener("click", () => this.cancel());
    this.refs.moveList?.addEventListener("click", (event) => {
      const row = event.target.closest("[data-review-ply]");
      if (!row) return;
      this.onNavigate(Number(row.dataset.reviewPly));
    });
  }

  isRunning() {
    return Boolean(this.review && ["queued", "running"].includes(this.review.status));
  }

  setGame(game) {
    const changed = this.gameId !== (game?.game_id || null);
    this.game = game;
    this.gameId = game?.game_id || null;
    if (changed) this.resetLocal();
    if (this.refs.run) {
      this.refs.run.disabled = this.busy || this.isRunning() || !game || !game.total_plies;
    }
  }

  resetLocal() {
    this.review = null;
    this.report = null;
    this.loadedSignature = null;
    this.failedSignature = null;
    this.lastKey = "";
  }

  update(review) {
    this.review = review || null;
    const key = [
      review?.signature,
      review?.status,
      review?.progress?.done,
      review?.progress?.total,
      review?.progress?.phase,
      review?.summary ? JSON.stringify(review.summary) : "",
    ].join("|");
    if (key !== this.lastKey) {
      this.lastKey = key;
      this.render();
    }
    if (
      review?.status === "complete" &&
      review.signature !== this.loadedSignature &&
      review.signature !== this.failedSignature &&
      !this.loadingReport
    ) {
      this.loadReport(review.signature);
    }
  }

  renderPreset() {
    for (const button of this.refs.presets?.querySelectorAll("[data-review-preset]") || []) {
      const active = button.dataset.reviewPreset === this.preset;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
      button.disabled = this.isRunning();
    }
    if (this.refs.model) {
      const depth = PRESET_DEPTH[this.preset] || 14;
      const confirm = Math.min(depth + 2, 18);
      this.refs.model.textContent = `Lichess logistic · depth ${depth} · confirm ${confirm} · MultiPV 3`;
    }
  }

  render() {
    const review = this.review;
    const status = review?.status || "idle";
    const running = status === "queued" || status === "running";
    const complete = status === "complete";
    const progress = review?.progress || { done: 0, total: 0, percent: 0, phase: status };
    const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));

    if (this.refs.run) {
      this.refs.run.disabled = this.busy || running || !this.game?.total_plies;
    }
    if (this.refs.cancel) this.refs.cancel.hidden = !running;
    if (this.refs.runLabel) {
      this.refs.runLabel.textContent = running
        ? "Reviewing…"
        : complete
          ? "Review again"
          : status === "failed"
            ? "Retry review"
            : "Run game review";
    }
    this.renderPreset();

    if (this.refs.chip) {
      this.refs.chip.textContent = running
        ? "Reviewing"
        : complete
          ? "Complete"
          : status === "cancelled"
            ? "Cancelled"
            : status === "failed"
              ? "Failed"
              : "Ready";
      this.refs.chip.classList.toggle("panel-chip-warm", running || complete);
      this.refs.chip.classList.toggle("is-error", status === "failed");
    }

    if (this.refs.progress) this.refs.progress.hidden = !running;
    if (this.refs.progressPhase) {
      this.refs.progressPhase.textContent = PHASE_LABEL[progress.phase] || "Working";
    }
    if (this.refs.progressCount) {
      this.refs.progressCount.textContent = `${progress.done || 0} / ${progress.total || 0}`;
    }
    if (this.refs.progressFill) this.refs.progressFill.style.width = `${percent}%`;

    if (this.refs.heroTitle) {
      this.refs.heroTitle.textContent = running
        ? "Reviewing every decision…"
        : complete
          ? "Review complete."
          : status === "cancelled"
            ? "Review cancelled."
            : status === "failed"
              ? "Review could not finish."
              : "See the game behind the moves.";
    }
    if (this.refs.heroCopy) {
      this.refs.heroCopy.textContent = running
        ? "The shared Stockfish worker is grading positions. You can navigate the board; the review keeps its game line."
        : complete
          ? "Brilliant and Great candidates were re-checked two plies deeper. Every percentage below is final."
          : status === "cancelled"
            ? "Nothing was committed. Start again when the table is quiet."
            : status === "failed"
              ? review?.error || "The engine did not finish the review."
              : "Stockfish grades every non-book ply, confirms Brilliant and Great moves two plies deeper, and measures accuracy with the approved review model.";
    }

    const summary = review?.summary || this.report?.summary || null;
    if (this.refs.accuracy) this.refs.accuracy.textContent = summary ? pair(summary.accuracy) : "—";
    if (this.refs.acpl) this.refs.acpl.textContent = summary ? pair(summary.acpl, 0) : "—";
    if (this.refs.elo) this.refs.elo.textContent = summary ? pair(summary.estimated_elo, 0) : "—";

    this.renderOpening(review?.opening || this.report?.opening || null);
    this.renderCounts(this.report?.totals?.counts || summary?.counts || null);
    this.renderMoves();
    this.renderNote(status, review, progress);
  }

  renderOpening(opening) {
    if (!this.refs.opening) return;
    const found = Boolean(opening && opening.eco);
    this.refs.opening.hidden = !found;
    if (!found) return;
    if (this.refs.openingEco) this.refs.openingEco.textContent = opening.eco;
    if (this.refs.openingName) {
      const variation = opening.variation ? `: ${opening.variation}` : "";
      this.refs.openingName.textContent = `${opening.name || "Opening"}${variation}`;
    }
    if (this.refs.openingMeta) {
      const plies = Number(opening.last_book_ply) || 0;
      this.refs.openingMeta.textContent = plies
        ? `Book ends after ${plies} ${plies === 1 ? "ply" : "plies"}`
        : "No book line matched";
    }
  }

  renderCounts(counts) {
    if (!this.refs.counts) return;
    this.refs.counts.replaceChildren();
    if (!counts) {
      this.refs.counts.hidden = true;
      return;
    }
    const entries = Object.entries(counts)
      .filter(([, count]) => Number(count) > 0)
      .sort((a, b) => (CLASS_META[a[0]]?.order ?? 99) - (CLASS_META[b[0]]?.order ?? 99));
    this.refs.counts.hidden = entries.length === 0;
    for (const [key, count] of entries) {
      const meta = CLASS_META[key] || { label: key, symbol: "•" };
      const chip = document.createElement("span");
      chip.className = `review-count review-count-${key}`;
      chip.title = `${count} ${meta.label}`;
      const symbol = document.createElement("span");
      symbol.className = "review-count-symbol";
      symbol.textContent = meta.symbol;
      const label = document.createElement("span");
      label.textContent = meta.label;
      const value = document.createElement("strong");
      value.textContent = String(count);
      chip.append(symbol, label, value);
      this.refs.counts.append(chip);
    }
  }

  renderMoves() {
    if (!this.refs.moves || !this.refs.moveList) return;
    const moves = this.report?.moves || [];
    this.refs.moves.hidden = moves.length === 0;
    this.refs.moveList.replaceChildren();
    for (const move of moves) {
      const meta = CLASS_META[move.classification] || {
        label: move.label || move.classification || "—",
        symbol: move.symbol || "•",
      };
      const row = document.createElement("button");
      row.type = "button";
      row.className = `review-move-row review-class-${move.classification || "unknown"}`;
      row.dataset.reviewPly = String(move.ply);
      row.title = move.explanation || `${meta.label} — ${move.san}`;
      if (move.is_book) row.classList.add("is-book");
      if (move.is_forced) row.classList.add("is-forced");

      const index = document.createElement("span");
      index.className = "review-move-index";
      index.textContent =
        move.color === "white" ? `${move.move_number}.` : `${move.move_number}…`;

      const san = document.createElement("strong");
      san.className = "review-move-san";
      san.textContent = move.san || move.uci || "—";

      const grade = document.createElement("span");
      grade.className = "review-move-grade";
      grade.textContent = `${meta.symbol} ${meta.label}${move.is_forced ? " · forced" : ""}`;

      const loss = document.createElement("span");
      loss.className = "review-move-loss";
      loss.textContent =
        move.is_book || move.win_loss == null ? "—" : `${Number(move.win_loss).toFixed(1)}%`;

      row.append(index, san, grade, loss);
      this.refs.moveList.append(row);
    }
  }

  renderNote(status, review, progress) {
    if (!this.refs.note) return;
    const running = status === "queued" || status === "running";
    let message = null;
    if (running) {
      const depth = review?.depth || PRESET_DEPTH[this.preset] || 14;
      message = `Depth ${depth}. Navigating or browsing the move list will not cancel the review.`;
    } else if (status === "cancelled") {
      message = "The review was cancelled. The game line and live evaluation are unchanged.";
    } else if (status === "failed") {
      message = review?.error || "Stockfish did not finish the review. Check the engine and try again.";
    } else if (status === "complete" && this.report?.timing) {
      const timing = this.report.timing;
      const seconds = Math.max(0.1, Number(timing.elapsed_ms || 0) / 1000);
      message = `${timing.searches || 0} searches · ${timing.cache_hits || 0} cache hits · ${seconds.toFixed(1)}s. Pass 2 confirmed ${
        this.report.totals?.confirmed_positions || 0
      } positions.`;
    } else if (status === "complete" && progress?.done) {
      message = "The review is complete. The full move list is loading.";
    }
    this.refs.note.hidden = !message;
    if (this.refs.noteCopy && message) this.refs.noteCopy.textContent = message;
  }

  async start() {
    if (this.busy || this.isRunning()) return;
    if (!this.game?.total_plies) {
      toast({
        title: "Nothing to review yet",
        description: "Play or import at least one move first.",
        kind: "warning",
      });
      return;
    }
    this.busy = true;
    this.refs.run.disabled = true;
    try {
      const result = await call("start_game_review", this.preset, {
        use_book: true,
        force: true,
      });
      this.resetLocal();
      this.update(result.review);
      toast({
        title: "Review started",
        description: `${
          this.preset.charAt(0).toUpperCase() + this.preset.slice(1)
        } preset · depths ${PRESET_DEPTH[this.preset]} → ${Math.min(
          PRESET_DEPTH[this.preset] + 2,
          18,
        )}.`,
        kind: "success",
        duration: 3600,
      });
    } catch (error) {
      toast({
        title: "Could not start the review",
        description: errorMessage(error),
        kind: "error",
        duration: 6500,
      });
    } finally {
      this.busy = false;
      this.refs.run.disabled = false;
      this.render();
    }
  }

  async cancel() {
    if (!this.isRunning()) return;
    try {
      const result = await call("cancel_game_review");
      this.update(result.review);
    } catch (error) {
      toast({
        title: "Could not cancel the review",
        description: errorMessage(error),
        kind: "error",
      });
    }
  }

  async loadReport(signature) {
    this.loadingReport = true;
    try {
      const result = await call("get_game_review", true);
      if (result.report && result.review?.signature === signature) {
        this.report = result.report;
        this.loadedSignature = signature;
        this.update(result.review);
        this.render();
      }
    } catch (error) {
      console.warn("Could not load the full review", error);
      this.failedSignature = signature;
    } finally {
      this.loadingReport = false;
    }
  }
}
