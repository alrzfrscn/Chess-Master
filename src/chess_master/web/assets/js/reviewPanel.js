/** Game Review dashboard: advantage curve, coach, PV sandbox, player summaries
 * and the classification matrix. All chess facts arrive in the report payload,
 * so this module never needs an engine call to step through a line. */

import { call, errorMessage } from "./api.js";
import { Chess } from "../vendor/chess.js/chess.js";
import { sound } from "./sound.js";
import { toast } from "./ui.js";

const PRESET_DEPTH = { fast: 10, standard: 14, deep: 16 };

const CLASS_META = {
  brilliant: { label: "Brilliant", symbol: "!!", order: 0 },
  great: { label: "Great", symbol: "!", order: 1 },
  best: { label: "Best", symbol: "★", order: 2 },
  excellent: { label: "Excellent", symbol: "✓", order: 3 },
  good: { label: "Good", symbol: "○", order: 4 },
  book: { label: "Book", symbol: "▤", order: 5 },
  inaccuracy: { label: "Inaccuracy", symbol: "?!", order: 6 },
  mistake: { label: "Mistake", symbol: "?", order: 7 },
  miss: { label: "Miss", symbol: "✕", order: 8 },
  blunder: { label: "Blunder", symbol: "??", order: 9 },
};

const CLASS_ORDER = Object.keys(CLASS_META).sort(
  (a, b) => CLASS_META[a].order - CLASS_META[b].order,
);

const KEY_MOMENT_CLASSES = new Set([
  "brilliant",
  "great",
  "inaccuracy",
  "mistake",
  "miss",
  "blunder",
]);

const RETRY_CLASSES = new Set(["inaccuracy", "mistake", "miss", "blunder"]);

const PHASE_LABEL = {
  queued: "Queued",
  scan: "Scanning positions",
  confirm: "Confirming Brilliant / Great",
  aggregate: "Scoring the game",
  complete: "Complete",
  cancelled: "Cancelled",
  failed: "Failed",
};

const CHART = { width: 320, height: 140, padL: 10, padR: 10, padT: 12, padB: 18 };
const SVG_NS = "http://www.w3.org/2000/svg";

function number(value, digits = 1) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : null;
}

function percent(value, digits = 1) {
  const numeric = number(value, digits);
  return numeric === null ? "—" : `${numeric}%`;
}

function initials(name) {
  const words = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return "?";
  return words
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

/** White-POV evaluation in the compact Chess.com form. */
function formatEval(cp, mate) {
  if (mate !== null && mate !== undefined) {
    const value = Number(mate);
    if (Number.isFinite(value)) {
      if (value === 0) return Number(cp) > 0 ? "+#" : "-#";
      return `${value > 0 ? "+" : "-"}M${Math.abs(value)}`;
    }
  }
  if (cp === null || cp === undefined) return "—";
  const numeric = Number(cp) / 100;
  if (!Number.isFinite(numeric)) return "—";
  return `${numeric > 0 ? "+" : ""}${numeric.toFixed(1)}`;
}

function evalTransition(move) {
  if (!move) return "—";
  if (move.is_book) return "Book";
  const before = formatEval(move.eval_before, move.mate_before);
  const after = formatEval(move.eval_after, move.mate_after);
  if (before === "—" && after === "—") return "—";
  return `${before} → ${after}`;
}

function moveLabel(move) {
  if (!move) return "Start";
  return `${move.move_number}${move.color === "white" ? "." : "…"} ${move.san || move.uci}`;
}

function sameUci(left, right) {
  return String(left || "").toLowerCase() === String(right || "").toLowerCase();
}

/** Apply a UCI move to a FEN, returning the attempt descriptor or null. */
function applyUci(fen, uci, promotion = null) {
  const text = String(uci || "");
  if (text.length < 4) return null;
  try {
    const chess = new Chess(fen);
    const move = chess.move({
      from: text.slice(0, 2),
      to: text.slice(2, 4),
      promotion: promotion || text.slice(4, 5) || undefined,
    });
    if (!move) return null;
    return {
      uci: `${move.from}${move.to}${move.promotion || ""}`,
      san: move.san,
      from: move.from,
      to: move.to,
      fen: chess.fen(),
    };
  } catch {
    return null;
  }
}

/** Win probability for the side that owns the move. */
function moverWin(winWhite, color) {
  const value = Number(winWhite);
  if (!Number.isFinite(value)) return null;
  return color === "black" ? 100 - value : value;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

function bookGlyph() {
  const node = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  node.append(
    svg("path", { d: "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5Z" }),
    svg("path", { d: "M4 5.5v15A2.5 2.5 0 0 0 6.5 23H20" }),
  );
  return node;
}

export class ReviewPanel {
  constructor({ onNavigate, onPreview, onSceneChange, onRetryChange } = {}) {
    this.onNavigate = onNavigate || (() => {});
    this.onPreview = onPreview || (() => {});
    this.onSceneChange = onSceneChange || (() => {});
    this.onRetryChange = onRetryChange || (() => {});
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
    this.filter = null;
    this.currentMove = null;
    this.preview = null;
    this.retry = null;
    this.playTimer = null;

    this.refs = {
      chip: document.getElementById("review-state-chip"),
      hero: document.getElementById("review-hero"),
      dashboard: document.getElementById("review-dashboard"),
      heroTitle: document.getElementById("review-hero-title"),
      heroCopy: document.getElementById("review-hero-copy"),
      presets: [
        document.getElementById("review-presets"),
        document.getElementById("review-presets-compact"),
      ].filter(Boolean),
      run: document.getElementById("btn-run-review"),
      runLabel: document.getElementById("review-run-label"),
      cancel: document.getElementById("btn-cancel-review"),
      reviewAgain: document.getElementById("btn-review-again"),
      exportCard: document.getElementById("btn-export-card"),
      progress: document.getElementById("review-progress"),
      progressPhase: document.getElementById("review-progress-phase"),
      progressCount: document.getElementById("review-progress-count"),
      progressFill: document.getElementById("review-progress-fill"),
      model: document.getElementById("review-model-label"),
      dashboardPreset: document.getElementById("review-dashboard-preset"),
      dashboardSub: document.getElementById("review-dashboard-sub"),
      chart: document.getElementById("review-chart"),
      chartWrap: document.getElementById("review-chart-wrap"),
      chartTooltip: document.getElementById("review-chart-tooltip"),
      chartPly: document.getElementById("review-chart-ply"),
      chartEval: document.getElementById("review-chart-eval"),
      coach: document.getElementById("review-coach"),
      coachNumber: document.getElementById("coach-number"),
      coachSan: document.getElementById("coach-san"),
      coachPill: document.getElementById("coach-pill"),
      coachForced: document.getElementById("coach-forced"),
      coachAccuracy: document.getElementById("coach-accuracy"),
      coachEval: document.getElementById("coach-eval"),
      coachBody: document.getElementById("coach-body"),
      coachBest: document.getElementById("coach-best"),
      showFollowup: document.getElementById("btn-show-followup"),
      retryButton: document.getElementById("btn-retry-mistake"),
      retryPanel: document.getElementById("retry-panel"),
      retryPrompt: document.getElementById("retry-prompt"),
      retryFeedback: document.getElementById("retry-feedback"),
      retryHint: document.getElementById("btn-retry-hint"),
      retrySolution: document.getElementById("btn-retry-solution"),
      retryTryBest: document.getElementById("btn-retry-trybest"),
      retryContinue: document.getElementById("btn-retry-continue"),
      retryCancel: document.getElementById("btn-retry-cancel"),
      pvControls: document.getElementById("pv-controls"),
      pvBanner: document.getElementById("pv-banner"),
      pvProgress: document.getElementById("pv-progress"),
      pvPlay: document.getElementById("pv-play"),
      prevKey: document.getElementById("btn-prev-key"),
      nextKey: document.getElementById("btn-next-key"),
      keyCount: document.getElementById("keymoment-count"),
      players: document.getElementById("review-players"),
      matrix: document.getElementById("review-matrix"),
      moves: document.getElementById("review-moves"),
      moveList: document.getElementById("review-move-list"),
      filter: document.getElementById("review-move-filter"),
      filterLabel: document.getElementById("review-filter-label"),
      filterClear: document.getElementById("review-filter-clear"),
      opening: document.getElementById("review-opening"),
      openingEco: document.getElementById("review-opening-eco"),
      openingName: document.getElementById("review-opening-name"),
      openingMeta: document.getElementById("review-opening-meta"),
      note: document.getElementById("review-note"),
      noteCopy: document.getElementById("review-note-copy"),
    };

    this.wire();
    this.renderPreset();
  }

  wire() {
    for (const container of this.refs.presets) {
      container.addEventListener("click", (event) => {
        const button = event.target.closest("[data-review-preset]");
        if (!button || this.isRunning()) return;
        this.preset = button.dataset.reviewPreset;
        this.renderPreset();
      });
    }
    this.refs.run?.addEventListener("click", () => this.start());
    this.refs.cancel?.addEventListener("click", () => this.cancel());
    this.refs.reviewAgain?.addEventListener("click", () => this.start());
    this.refs.exportCard?.addEventListener("click", () => this.exportCard());
    this.refs.moveList?.addEventListener("click", (event) => {
      const row = event.target.closest("[data-review-ply]");
      if (!row) return;
      this.onNavigate(Number(row.dataset.reviewPly));
    });
    this.refs.showFollowup?.addEventListener("click", () => this.startPreview());
    this.refs.retryButton?.addEventListener("click", () => this.startRetry());
    this.refs.retryHint?.addEventListener("click", () => this.hint());
    this.refs.retrySolution?.addEventListener("click", () => this.showSolution());
    this.refs.retryTryBest?.addEventListener("click", () => this.retryAgain());
    this.refs.retryContinue?.addEventListener("click", () => this.continueReview());
    this.refs.retryCancel?.addEventListener("click", () => this.exitRetry());
    this.refs.pvControls?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-pv]");
      if (!button) return;
      const action = button.dataset.pv;
      if (action === "prev") this.stepPreview(-1);
      if (action === "next") this.stepPreview(1);
      if (action === "play") this.togglePlayback();
      if (action === "exit") this.clearPreview();
    });
    this.refs.prevKey?.addEventListener("click", () => this.jumpKeyMoment(-1));
    this.refs.nextKey?.addEventListener("click", () => this.jumpKeyMoment(1));
    this.refs.matrix?.addEventListener("click", (event) => this.handleMatrix(event));
    this.refs.matrix?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest(".matrix-row");
      if (!row) return;
      event.preventDefault();
      this.handleMatrix(event, row);
    });
    this.refs.filterClear?.addEventListener("click", () => this.setFilter(null));
  }

  isRunning() {
    return Boolean(this.review && ["queued", "running"].includes(this.review.status));
  }

  hasPreview() {
    return Boolean(this.preview);
  }

  setGame(game) {
    const changed = this.gameId !== (game?.game_id || null);
    const plyChanged = Boolean(this.game && game && this.game.ply !== game.ply);
    this.game = game;
    this.gameId = game?.game_id || null;
    if (changed) {
      this.resetLocal();
    } else if (this.preview && plyChanged) {
      // Main move-list navigation always leaves the temporary PV sandbox.
      this.stopPlayback();
      this.preview = null;
    }
    if (this.retry && (changed || plyChanged)) this.clearRetryLocal();
    if (this.refs.run) {
      this.refs.run.disabled = this.busy || this.isRunning() || !game || !game.total_plies;
    }
  }

  resetLocal() {
    this.stopPlayback();
    this.clearRetryLocal();
    this.review = null;
    this.report = null;
    this.loadedSignature = null;
    this.failedSignature = null;
    this.lastKey = "";
    this.filter = null;
    this.currentMove = null;
    this.preview = null;
  }

  clearRetryLocal() {
    if (this.retry?.timer) window.clearTimeout(this.retry.timer);
    this.retry = null;
  }

  update(review) {
    this.review = review || null;
    const key = [
      this.gameId,
      this.game?.ply,
      this.preview?.index,
      this.preview?.playing,
      this.report ? "report" : "",
      review?.signature,
      review?.status,
      review?.progress?.done,
      review?.progress?.total,
      review?.progress?.phase,
      this.retry?.phase,
      this.retry?.hint,
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
    for (const button of document.querySelectorAll("[data-review-preset]")) {
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
    const hasReport = Boolean(this.report);
    const progress = review?.progress || { done: 0, total: 0, percent: 0, phase: status };
    const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));

    if (this.refs.hero) this.refs.hero.hidden = hasReport;
    if (this.refs.dashboard) this.refs.dashboard.hidden = !hasReport;
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

    if (hasReport) {
      this.renderDashboard();
    } else {
      this.currentMove = null;
      this.renderNote(status, review, progress);
    }
  }

  renderDashboard() {
    const report = this.report;
    if (!report) return;
    if (this.refs.dashboardPreset) {
      this.refs.dashboardPreset.textContent = `${report.preset_label || "Standard"} · depth ${report.engine?.depth || 14}`;
    }
    if (this.refs.dashboardSub) {
      const keyCount = report.key_moments?.length || 0;
      this.refs.dashboardSub.textContent = `${report.totals?.plies || 0} plies · ${keyCount} key moment${keyCount === 1 ? "" : "s"} · ${report.totals?.confirmed_positions || 0} confirmed`;
    }
    this.renderOpening(report.opening);
    this.renderChart();
    this.renderCoach();
    this.renderKeyMoments();
    this.renderPlayers();
    this.renderMatrix();
    this.renderFilter();
    this.renderMoves();
    this.renderPreviewUI();
    this.renderNote("complete", this.review, report.progress);
  }

  /* --------------------------------------------------------------------
     Advantage chart
     -------------------------------------------------------------------- */

  // Exact ply match first; otherwise fall back to the clamped index.
  pointAtPly(ply) {
    const points = this.report?.eval_chart?.points || [];
    if (!points.length) return null;
    const target = Math.max(0, Math.min(points.length - 1, Number(ply) || 0));
    return points.find((point) => point.ply === Number(ply)) || points[target];
  }

  renderChart() {
    const svgEl = this.refs.chart;
    const points = this.report?.eval_chart?.points || [];
    if (!svgEl) return;
    svgEl.replaceChildren();
    if (points.length < 2) {
      if (this.refs.chartPly) this.refs.chartPly.textContent = "No curve";
      if (this.refs.chartEval) this.refs.chartEval.textContent = "—";
      return;
    }

    const { width, height, padL, padR, padT, padB } = CHART;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const midY = padT + plotH / 2;
    const total = points.length - 1;
    const x = (index) => padL + (total ? (index / total) * plotW : plotW / 2);
    const y = (win) => padT + (1 - Math.max(0, Math.min(100, Number(win))) / 100) * plotH;

    const defs = svg("defs");
    for (const [id, top] of [
      ["review-chart-top", 0],
      ["review-chart-bottom", 1],
    ]) {
      const clip = svg("clipPath", { id });
      clip.append(
        svg("rect", {
          x: 0,
          y: top ? midY : padT,
          width,
          height: plotH / 2,
        }),
      );
      defs.append(clip);
    }
    svgEl.append(defs);

    for (const value of [0, 25, 50, 75, 100]) {
      svgEl.append(
        svg("line", {
          x1: padL,
          x2: padL + plotW,
          y1: y(value),
          y2: y(value),
          class: value === 50 ? "review-chart-mid" : "review-chart-grid",
        }),
      );
    }

    const curve = points
      .map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(point.win_prob_white).toFixed(2)}`)
      .join(" ");
    const band = `${curve} L${x(total).toFixed(2)},${midY.toFixed(2)} L${x(0).toFixed(2)},${midY.toFixed(2)} Z`;

    const whiteArea = svg("path", {
      d: band,
      class: "review-chart-area-white",
      "clip-path": "url(#review-chart-top)",
    });
    const blackArea = svg("path", {
      d: band,
      class: "review-chart-area-black",
      "clip-path": "url(#review-chart-bottom)",
    });
    svgEl.append(whiteArea, blackArea);
    svgEl.append(svg("path", { d: curve, class: "review-chart-line" }));

    points.forEach((point, index) => {
      if (!KEY_MOMENT_CLASSES.has(point.classification)) return;
      const dot = svg("circle", {
        cx: x(index),
        cy: y(point.win_prob_white),
        r: 3.6,
        class: `review-chart-dot review-class-${point.classification}`,
      });
      const title = svg("title");
      title.textContent = `${moveLabel(point)} · ${formatEval(point.eval_cp, point.mate)} · ${CLASS_META[point.classification]?.label || point.classification}`;
      dot.append(title);
      svgEl.append(dot);
    });

    const currentIndex = Math.max(0, Math.min(total, Number(this.game?.ply) || 0));
    const currentPoint = points[currentIndex] || points[0];
    const currentX = x(currentIndex);
    svgEl.append(
      svg("line", {
        x1: currentX,
        x2: currentX,
        y1: padT,
        y2: padT + plotH,
        class: "review-chart-playhead",
      }),
      svg("circle", {
        cx: currentX,
        cy: y(currentPoint.win_prob_white),
        r: 3.2,
        class: "review-chart-playhead-dot",
      }),
    );

    // Transparent per-ply hit areas power hover tooltips and click-to-jump.
    // Each point owns half of the gap on either side, including both ends.
    for (let index = 0; index <= total; index += 1) {
      const left = index === 0 ? x(0) : (x(index - 1) + x(index)) / 2;
      const right = index === total ? x(total) : (x(index) + x(index + 1)) / 2;
      const hit = svg("rect", {
        x: left,
        y: padT,
        width: Math.max(1, right - left),
        height: plotH,
        class: "review-chart-hit",
        "data-ply": points[index].ply,
        "data-index": index,
      });
      svgEl.append(hit);
    }

    svgEl.onpointermove = (event) => {
      const hit = event.target.closest?.("rect[data-ply]");
      if (!hit) return;
      const index = Math.max(0, Math.min(total, Number(hit.dataset.index) || 0));
      this.showChartTooltip(points[index], x(index));
    };
    svgEl.onpointerleave = () => this.hideChartTooltip();
    svgEl.onclick = (event) => {
      const hit = event.target.closest?.("rect[data-ply]");
      if (!hit) return;
      this.hideChartTooltip();
      this.onNavigate(Number(hit.dataset.ply));
      this.revealCoach();
    };
    svgEl.onkeydown = (event) => {
      const ply = Number(this.game?.ply) || 0;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        this.onNavigate(Math.max(0, ply - 1));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        this.onNavigate(Math.min(total, ply + 1));
      } else if (event.key === "Home") {
        event.preventDefault();
        this.onNavigate(0);
      } else if (event.key === "End") {
        event.preventDefault();
        this.onNavigate(total);
      }
    };

    this.renderChartFoot(currentPoint);
  }

  showChartTooltip(point, x) {
    const tooltip = this.refs.chartTooltip;
    const wrap = this.refs.chartWrap;
    if (!tooltip || !wrap) return;
    const head = element("strong", "chart-tooltip-move", moveLabel(point));
    const meta = element(
      "span",
      "chart-tooltip-meta",
      `${formatEval(point.eval_cp, point.mate)} · ${point.classification ? CLASS_META[point.classification]?.label || point.classification : "Starting position"}`,
    );
    tooltip.replaceChildren(head, meta);
    tooltip.hidden = false;
    const rect = wrap.getBoundingClientRect();
    const ratio = CHART.width ? rect.width / CHART.width : 1;
    const left = Math.max(64, Math.min(rect.width - 64, x * ratio));
    tooltip.style.left = `${left}px`;
  }

  hideChartTooltip() {
    if (this.refs.chartTooltip) this.refs.chartTooltip.hidden = true;
  }

  renderChartFoot(point) {
    if (!point) return;
    const move = this.report?.moves?.find((item) => item.ply === point.ply);
    if (this.refs.chartPly) {
      this.refs.chartPly.textContent =
        point.ply === 0 ? "Starting position" : moveLabel(move || point);
    }
    if (this.refs.chartEval) {
      this.refs.chartEval.textContent = `${formatEval(point.eval_cp, point.mate)}${
        point.classification ? ` · ${CLASS_META[point.classification]?.label || point.classification}` : ""
      }`;
    }
  }

  /* --------------------------------------------------------------------
     Coach and PV sandbox
     -------------------------------------------------------------------- */

  renderCoach() {
    const coach = this.refs.coach;
    if (!coach) return;
    const ply = Number(this.game?.ply) || 0;
    coach.className = "review-coach";

    if (ply === 0) {
      this.currentMove = null;
      if (this.refs.coachNumber) this.refs.coachNumber.textContent = "—";
      if (this.refs.coachSan) this.refs.coachSan.textContent = "Starting position";
      if (this.refs.coachPill) this.refs.coachPill.hidden = true;
      if (this.refs.coachForced) this.refs.coachForced.hidden = true;
      if (this.refs.coachAccuracy) this.refs.coachAccuracy.textContent = "—";
      if (this.refs.coachEval) {
        const point = this.report?.eval_chart?.points?.[0];
        this.refs.coachEval.textContent = point ? formatEval(point.eval_cp, point.mate) : "—";
      }
      if (this.refs.coachBody) {
        this.refs.coachBody.textContent =
          "The starting point. Jump to a turning point with the advantage curve, the key-moment controls, or K.";
      }
      if (this.refs.coachBest) this.refs.coachBest.hidden = true;
      this.renderPreviewUI();
      this.renderRetryUI();
      return;
    }

    const move = this.report?.moves?.find((item) => item.ply === ply) || null;
    this.currentMove = move;
    if (!move) return;

    const meta = CLASS_META[move.classification] || { label: "Move", symbol: "•" };
    coach.classList.add(`review-class-${move.classification || "unknown"}`);
    if (this.refs.coachNumber) {
      this.refs.coachNumber.textContent = `${move.move_number}${move.color === "white" ? "." : "…"}`;
    }
    if (this.refs.coachSan) {
      this.refs.coachSan.textContent = `${move.san || move.uci}${move.symbol && !move.is_book ? move.symbol : ""}`;
    }
    if (this.refs.coachPill) {
      this.refs.coachPill.hidden = false;
      this.refs.coachPill.textContent = meta.label;
      this.refs.coachPill.className = `coach-pill review-class-${move.classification || "unknown"}`;
    }
    if (this.refs.coachForced) this.refs.coachForced.hidden = !move.is_forced;
    if (this.refs.coachAccuracy) {
      this.refs.coachAccuracy.textContent = move.is_book
        ? "Book move"
        : move.accuracy === null || move.accuracy === undefined
          ? "—"
          : `${Math.round(Number(move.accuracy))}% accuracy`;
    }
    if (this.refs.coachEval) this.refs.coachEval.textContent = evalTransition(move);
    if (this.refs.coachBody) {
      this.refs.coachBody.textContent =
        move.explanation ||
        (move.is_book ? "A known book move." : "No explanation was recorded for this move.");
    }
    if (this.refs.coachBest) {
      const best = move.best_move;
      if (best && !best.is_played && !move.is_book) {
        this.refs.coachBest.hidden = false;
        this.refs.coachBest.textContent = `Best was ${best.san} (${formatEval(best.score_cp, best.mate)}).`;
      } else {
        this.refs.coachBest.hidden = true;
      }
    }
    this.renderPreviewUI();
    this.renderRetryUI();
  }

  renderPreviewUI() {
    const followup = this.currentMove?.pv_followup;
    const canFollow = Boolean(followup?.moves?.length);
    if (this.refs.showFollowup) {
      this.refs.showFollowup.hidden = !canFollow || Boolean(this.preview) || Boolean(this.retry);
      this.refs.showFollowup.disabled = !canFollow;
    }
    if (this.refs.pvControls) this.refs.pvControls.hidden = !this.preview || Boolean(this.retry);

    if (this.retry) {
      if (this.refs.pvBanner) this.refs.pvBanner.hidden = true;
      return;
    }
    if (!this.preview) {
      if (this.refs.pvBanner) this.refs.pvBanner.hidden = true;
      this.refs.coach?.classList.remove("is-previewing");
      return;
    }

    const state = this.previewState();
    if (!state) return;
    this.refs.coach?.classList.add("is-previewing");
    if (this.refs.pvBanner) {
      this.refs.pvBanner.hidden = false;
      this.refs.pvBanner.textContent = state.banner;
    }
    if (this.refs.pvProgress) this.refs.pvProgress.textContent = `${state.index} of ${state.total}`;
    const prev = this.refs.pvControls?.querySelector('[data-pv="prev"]');
    const next = this.refs.pvControls?.querySelector('[data-pv="next"]');
    if (prev) prev.disabled = state.index <= 0;
    if (next) next.disabled = state.index >= state.total;
    if (this.refs.pvPlay) {
      this.refs.pvPlay.textContent = this.preview.playing
        ? "Pause Line"
        : state.index >= state.total
          ? "Replay Line"
          : "Play Line";
    }
  }

  previewState() {
    if (!this.preview) return null;
    const line = this.preview.line;
    const moves = line?.moves || [];
    const total = moves.length;
    const index = Math.max(0, Math.min(total, Number(this.preview.index) || 0));
    const move = index > 0 ? moves[index - 1] : null;
    return {
      active: true,
      basePly: Number(this.preview.basePly) || 0,
      index,
      total,
      fen: move ? move.fen_after : line.start_fen,
      lastMove: move ? { from: move.from, to: move.to } : null,
      move: move
        ? {
            from: move.from,
            to: move.to,
            san: move.san,
            uci: move.uci,
            color: move.color,
            move_number: move.move_number,
          }
        : null,
      banner: `Previewing Engine Line (Move ${Math.max(1, index)} of ${total}) — Click 'Back to Game' to exit`,
    };
  }

  startPreview() {
    const followup = this.currentMove?.pv_followup;
    if (!followup?.moves?.length || !this.currentMove) return;
    this.stopPlayback();
    this.preview = {
      index: 1,
      basePly: Number(this.currentMove.ply) || 0,
      line: followup,
      playing: false,
    };
    this.renderPreviewUI();
    this.onPreview(this.previewState());
  }

  setPreviewIndex(index) {
    if (!this.preview) return;
    const total = this.preview.line?.moves?.length || 0;
    this.preview.index = Math.max(0, Math.min(total, Number(index) || 0));
    this.renderPreviewUI();
    this.onPreview(this.previewState());
  }

  stepPreview(delta) {
    if (!this.preview) return;
    this.stopPlayback();
    this.setPreviewIndex(this.preview.index + delta);
  }

  togglePlayback() {
    if (!this.preview) return;
    if (this.preview.playing) {
      this.stopPlayback();
      this.renderPreviewUI();
      return;
    }
    const total = this.preview.line?.moves?.length || 0;
    if (this.preview.index >= total) this.preview.index = 0;
    this.preview.playing = true;
    this.renderPreviewUI();
    this.playTimer = window.setInterval(() => {
      if (!this.preview) return;
      if (this.preview.index >= total) {
        this.stopPlayback();
        this.renderPreviewUI();
        return;
      }
      this.setPreviewIndex(this.preview.index + 1);
    }, 850);
  }

  stopPlayback() {
    if (this.playTimer) window.clearInterval(this.playTimer);
    this.playTimer = null;
    if (this.preview) this.preview.playing = false;
  }

  clearPreview() {
    if (!this.preview && !this.playTimer) return;
    this.stopPlayback();
    this.preview = null;
    this.renderPreviewUI();
    this.onPreview(null);
  }

  /* --------------------------------------------------------------------
     Retry mistake sandbox
     -------------------------------------------------------------------- */

  moveAtPly(ply) {
    return this.report?.moves?.find((item) => item.ply === Number(ply)) || null;
  }

  canRetry(move) {
    const best = move?.best_move;
    return Boolean(
      move &&
        RETRY_CLASSES.has(move.classification) &&
        best?.from &&
        best?.to &&
        best?.uci &&
        !move.is_book &&
        !sameUci(best.uci, move.uci),
    );
  }

  isRetrying() {
    return Boolean(this.retry);
  }

  retryState() {
    if (!this.retry) return null;
    const board = this.boardState();
    return {
      ply: this.retry.ply,
      phase: this.retry.phase,
      fen: board?.fen || null,
      playable: Boolean(board?.playable),
      hint: Boolean(this.retry.hint),
      attempt: this.retry.attempt
        ? { uci: this.retry.attempt.uci, san: this.retry.attempt.san }
        : null,
      best: this.retry.move?.best_move
        ? {
            uci: this.retry.move.best_move.uci,
            san: this.retry.move.best_move.san,
            from: this.retry.move.best_move.from,
            to: this.retry.move.best_move.to,
          }
        : null,
    };
  }

  /** Board override consumed by app.js and board.js while the sandbox is open. */
  boardState() {
    if (!this.retry) return null;
    const retry = this.retry;
    const move = retry.move;
    let fen = move.fen_before;
    let lastMove = null;
    let playable = retry.phase === "awaiting";
    if (["correct", "alternative", "wrong", "solution"].includes(retry.phase)) {
      fen = retry.fen || move.fen_before;
      lastMove = retry.attempt
        ? { from: retry.attempt.from, to: retry.attempt.to }
        : null;
      playable = false;
    }
    return { fen, color: move.color, lastMove, playable };
  }

  startRetry() {
    if (!this.report) return false;
    const ply = Number(this.game?.ply) || 0;
    const move = this.moveAtPly(ply);
    if (!this.canRetry(move)) {
      toast({
        title: "No retry for this ply",
        description: move?.is_book
          ? "Book moves are not mistakes."
          : move?.best_move?.is_played
            ? "You already played the engine's best move."
            : "This move has no stored alternative.",
        kind: "info",
        duration: 2800,
      });
      return false;
    }
    this.stopPlayback();
    this.clearPreview();
    this.clearRetryLocal();
    this.retry = {
      ply: move.ply,
      move,
      phase: "awaiting",
      hint: false,
      attempt: null,
      fen: move.fen_before,
      timer: null,
    };
    this.renderRetryUI();
    this.renderPreviewUI();
    this.onRetryChange();
    this.revealCoach();
    return true;
  }

  exitRetry() {
    if (!this.retry) return false;
    this.clearRetryLocal();
    this.renderRetryUI();
    this.renderPreviewUI();
    this.onRetryChange();
    return true;
  }

  continueReview() {
    if (!this.retry) return false;
    const ply = this.retry.ply;
    this.exitRetry();
    // Key-moment navigation wraps; after the final moment resume on the next ply instead.
    const hasNextMoment = (this.report?.key_moments || []).some((moment) => moment.ply > ply);
    if (hasNextMoment && this.jumpKeyMoment(1)) return true;
    this.onNavigate(Math.min((Number(this.game?.total_plies) || 0), ply + 1));
    return true;
  }

  retryAgain() {
    if (!this.retry) return false;
    const move = this.retry.move;
    if (this.retry.timer) window.clearTimeout(this.retry.timer);
    this.retry = {
      ply: move.ply,
      move,
      phase: "awaiting",
      hint: false,
      attempt: null,
      fen: move.fen_before,
      timer: null,
    };
    this.renderRetryUI();
    this.onRetryChange();
    return true;
  }

  hint() {
    if (!this.retry || !["awaiting", "wrong"].includes(this.retry.phase)) return;
    this.retry.hint = true;
    this.renderRetryUI();
    this.onRetryChange();
  }

  showSolution() {
    if (!this.retry) return;
    const move = this.retry.move;
    const best = move.best_move;
    if (!best?.uci) return;
    const applied = applyUci(move.fen_before, best.uci);
    if (!applied) return;
    if (this.retry.timer) window.clearTimeout(this.retry.timer);
    this.retry.phase = "solution";
    this.retry.attempt = applied;
    this.retry.fen = applied.fen;
    this.retry.hint = true;
    sound.play("move");
    this.renderRetryUI();
    this.onRetryChange();
  }

  handleRetryMove(uci, promotion = null) {
    if (!this.retry || !["awaiting", "wrong"].includes(this.retry.phase)) return false;
    const retry = this.retry;
    const move = retry.move;
    const applied = applyUci(move.fen_before, uci, promotion);
    if (!applied) return false;
    if (retry.timer) {
      window.clearTimeout(retry.timer);
      retry.timer = null;
    }

    const best = move.best_move;
    if (best?.uci && sameUci(applied.uci, best.uci)) {
      retry.phase = "correct";
      retry.attempt = applied;
      retry.fen = applied.fen;
      sound.play("retry_correct");
      this.renderRetryUI();
      this.onRetryChange();
      return true;
    }

    const bestWin = moverWin(best?.win_prob_white, move.color);
    const alternative = (move.multi_pv || []).find(
      (line) =>
        Number(line.rank || 0) > 1 &&
        sameUci(line.uci, applied.uci) &&
        bestWin !== null &&
        moverWin(line.win_prob_white, move.color) !== null &&
        bestWin - moverWin(line.win_prob_white, move.color) < 2.0,
    );
    if (alternative) {
      retry.phase = "alternative";
      retry.attempt = applied;
      retry.fen = applied.fen;
      sound.play("retry_correct");
      this.renderRetryUI();
      this.onRetryChange();
      return true;
    }

    retry.phase = "wrong";
    retry.attempt = applied;
    retry.fen = applied.fen;
    sound.play("retry_wrong");
    this.renderRetryUI();
    this.onRetryChange();
    retry.timer = window.setTimeout(() => {
      if (!this.retry || this.retry !== retry || retry.phase !== "wrong") return;
      retry.timer = null;
      retry.phase = "awaiting";
      retry.attempt = null;
      retry.fen = move.fen_before;
      this.renderRetryUI();
      this.onRetryChange();
    }, 450);
    return true;
  }

  renderRetryUI() {
    const refs = this.refs;
    const retry = this.retry;
    refs.coach?.classList.toggle("is-retrying", Boolean(retry));
    if (refs.retryButton) {
      refs.retryButton.hidden =
        Boolean(retry) || Boolean(this.preview) || !this.canRetry(this.currentMove);
    }
    if (!refs.retryPanel) return;
    refs.retryPanel.hidden = !retry;
    if (!retry) {
      refs.retryPanel.classList.remove("is-correct", "is-wrong");
      return;
    }

    const move = retry.move;
    const best = move.best_move;
    const label = CLASS_META[move.classification]?.label || move.label || "Mistake";
    const side = move.color === "black" ? "Black" : "White";
    const attemptSan = retry.attempt?.san || "That move";
    let prompt = "";
    let feedback = "";

    if (retry.phase === "correct") {
      prompt = `Brilliant find! ${best?.san || "The engine move"} was the engine's best move.`;
      feedback = "Correct! Continue the review to keep the story moving.";
    } else if (retry.phase === "alternative") {
      prompt = `Good move! ${attemptSan} also keeps the advantage. The engine's top choice was ${best?.san}.`;
      feedback = "This line is nearly as strong as the engine's first choice.";
    } else if (retry.phase === "wrong") {
      prompt = `Not quite — ${attemptSan} doesn't solve the position. Try another move or click 'Hint'!`;
    } else if (retry.phase === "solution") {
      prompt = `Solution: ${best?.san}. Continue review when you're ready.`;
      feedback = "The engine's best move has been played on the board.";
    } else {
      prompt = `You played ${move.san} (${label}) — Find a better move for ${side}!`;
      feedback = retry.hint
        ? `Hint: the piece on ${best?.from} is the one to move.`
        : "";
    }

    refs.retryPanel.classList.toggle(
      "is-correct",
      ["correct", "alternative", "solution"].includes(retry.phase),
    );
    refs.retryPanel.classList.toggle("is-wrong", retry.phase === "wrong");
    if (refs.retryPrompt) refs.retryPrompt.textContent = prompt;
    if (refs.retryFeedback) {
      refs.retryFeedback.hidden = !feedback;
      refs.retryFeedback.textContent = feedback;
    }

    const canTry = ["awaiting", "wrong"].includes(retry.phase);
    if (refs.retryHint) refs.retryHint.hidden = !canTry;
    if (refs.retrySolution) refs.retrySolution.hidden = !canTry;
    if (refs.retryTryBest) refs.retryTryBest.hidden = retry.phase !== "alternative";
    if (refs.retryContinue) {
      refs.retryContinue.hidden = !["correct", "alternative", "solution"].includes(retry.phase);
    }
    if (refs.retryCancel) refs.retryCancel.hidden = false;
  }

  revealCoach() {
    this.refs.coach?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  /* --------------------------------------------------------------------
     Key moments
     -------------------------------------------------------------------- */

  renderKeyMoments() {
    const moments = this.report?.key_moments || [];
    const ply = Number(this.game?.ply) || 0;
    const index = moments.findIndex((moment) => moment.ply === ply);
    if (this.refs.keyCount) {
      this.refs.keyCount.textContent = moments.length ? `${index >= 0 ? index + 1 : 0} / ${moments.length}` : "0 / 0";
    }
    if (this.refs.prevKey) this.refs.prevKey.disabled = moments.length === 0;
    if (this.refs.nextKey) this.refs.nextKey.disabled = moments.length === 0;
  }

  jumpKeyMoment(direction) {
    const moments = this.report?.key_moments || [];
    if (!moments.length) return false;
    const ply = Number(this.game?.ply) || 0;
    let target;
    if (direction > 0) {
      target = moments.find((moment) => moment.ply > ply) || moments[0];
    } else {
      target = [...moments].reverse().find((moment) => moment.ply < ply) || moments[moments.length - 1];
    }
    if (!target) return false;
    this.onNavigate(target.ply);
    this.revealCoach();
    return true;
  }

  /* --------------------------------------------------------------------
     Player summary
     -------------------------------------------------------------------- */

  renderPlayers() {
    const container = this.refs.players;
    if (!container) return;
    container.replaceChildren();
    const summary = this.report?.summary || {};
    const reportPlayers = this.report?.players || {};
    const gamePlayers = this.game?.players || {};
    const white = summary.white || {};
    const black = summary.black || {};
    const whiteAccuracy = Number(white.accuracy);
    const blackAccuracy = Number(black.accuracy);
    const leader =
      Number.isFinite(whiteAccuracy) && Number.isFinite(blackAccuracy)
        ? whiteAccuracy === blackAccuracy
          ? null
          : whiteAccuracy > blackAccuracy
            ? "white"
            : "black"
        : null;

    for (const color of ["white", "black"]) {
      const player = summary[color] || {};
      const name =
        reportPlayers[color] ||
        gamePlayers[color]?.name ||
        (color === "white" ? "White" : "Black");
      const accuracy = player.accuracy === null || player.accuracy === undefined ? "—" : percent(player.accuracy);

      const card = element("article", `review-player review-player-${color}`);
      if (leader === color) card.classList.add("is-leader");

      const head = element("div", "review-player-head");
      const avatar = element("span", "review-avatar", initials(name));
      const identity = element("div", "review-player-id");
      identity.append(
        element("strong", null, name),
        element(
          "span",
          null,
          `${color === "white" ? "White" : "Black"} · ${player.moves || 0} moves`,
        ),
      );
      const elo = element(
        "span",
        "review-elo",
        player.estimated_elo?.elo ? `≈ ${player.estimated_elo.elo}` : "—",
      );
      head.append(avatar, identity, elo);

      const accuracyRow = element("div", "review-accuracy-row");
      accuracyRow.append(
        element("span", "review-accuracy-label", "Accuracy"),
        element("strong", "review-accuracy-value", accuracy),
      );

      const stats = element("div", "review-player-stats");
      for (const [label, value] of [
        ["ACPL", player.acpl === null || player.acpl === undefined ? "—" : number(player.acpl, 1)],
        ["Graded", String(player.graded_moves ?? 0)],
        ["Best", String(player.counts?.best ?? 0)],
      ]) {
        const cell = element("span");
        cell.append(element("span", null, label), element("b", null, value));
        stats.append(cell);
      }

      const phases = element("div", "review-phases");
      for (const [key, label] of [
        ["opening", "Opening"],
        ["middlegame", "Middlegame"],
        ["endgame", "Endgame"],
      ]) {
        const phase = player.phases?.[key] || {};
        const phaseMoves = Number(phase.moves || 0);
        const phaseAccuracy = phaseMoves > 0 ? percent(phase.accuracy) : "—";
        const row = element("div", "review-phase");
        const copy = element("span", "review-phase-copy");
        copy.append(element("span", null, label), element("b", null, phaseAccuracy));
        const bar = element("span", "review-phase-bar");
        const fill = element("i");
        fill.style.width = `${phaseMoves > 0 ? Math.max(0, Math.min(100, Number(phase.accuracy) || 0)) : 0}%`;
        bar.append(fill);
        row.append(copy, bar);
        phases.append(row);
      }

      card.append(head, accuracyRow, stats, phases);
      container.append(card);
    }
  }

  /* --------------------------------------------------------------------
     Shareable review card
     -------------------------------------------------------------------- */

  async exportCard() {
    if (!this.report) return;
    const button = this.refs.exportCard;
    if (button) button.disabled = true;
    try {
      if (document.fonts?.ready) await document.fonts.ready;
      const canvas = this.drawCard();
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("The canvas could not be exported.");
      const filename = `chess-master-review-${this.game?.game_id || this.report.signature || "game"}.png`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast({ title: "Review card exported", description: filename, kind: "success" });
    } catch (error) {
      toast({ title: "Could not export the card", description: errorMessage(error), kind: "error" });
    } finally {
      if (button) button.disabled = false;
    }
  }

  /** Draw a self-contained 1200×630 card: header, players, curve, counts. */
  drawCard() {
    const width = 1200;
    const height = 630;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return canvas;

    const report = this.report || {};
    const summary = report.summary || {};
    const counts = report.totals?.counts || summary.counts || {};
    const opening = report.opening || {};
    const cardPlayers = report.players || {};
    const gamePlayers = this.game?.players || {};
    const mono = '"Fira Code", ui-monospace, monospace';
    const sans = '"Fira Sans", system-ui, sans-serif';
    const styles = getComputedStyle(document.documentElement);
    const classColor = (key) =>
      (styles.getPropertyValue(`--class-${key}`) || "").trim() || "#8d99a5";
    const clamp = (value) => Math.max(0, Math.min(100, Number(value) || 0));

    const round = (x, y, w, h, r, fill, stroke) => {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
      if (fill) {
        ctx.fillStyle = fill;
        ctx.fill();
      }
      if (stroke) {
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    };
    const write = (value, x, y, size, weight, color, align = "left", family = sans) => {
      ctx.font = `${weight} ${size}px ${family}`;
      ctx.fillStyle = color;
      ctx.textAlign = align;
      ctx.textBaseline = "alphabetic";
      ctx.fillText(String(value ?? ""), x, y);
    };

    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, "#0a0f13");
    bg.addColorStop(0.55, "#0b1418");
    bg.addColorStop(1, "#0d1a16");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);
    const glow = ctx.createRadialGradient(940, 0, 30, 940, 0, 620);
    glow.addColorStop(0, "rgba(95, 215, 164, 0.16)");
    glow.addColorStop(1, "rgba(95, 215, 164, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#5fd7a4";
    ctx.fillRect(0, 0, width, 6);

    write("CHESS MASTER", 60, 62, 15, 600, "#5fd7a4", "left", mono);
    write("Game Review", 60, 104, 36, 700, "#e8edf1");
    const game = this.game || {};
    const date = String(game.created_at || game.headers?.Date || new Date().toISOString())
      .slice(0, 10)
      .replace(/-/g, ".");
    write(date, 1140, 58, 13, 500, "#b2bcc6", "right", mono);
    write(game.mode_label || "Chess Master game", 1140, 82, 13, 500, "#8d99a5", "right", mono);

    round(60, 124, 1080, 52, 12, "rgba(95, 215, 164, 0.06)", "rgba(95, 215, 164, 0.18)");
    write(opening.eco || "—", 82, 157, 16, 600, "#5fd7a4", "left", mono);
    write(opening.full_name || opening.name || "Unknown opening", 142, 157, 17, 500, "#e8edf1");
    write("OPENING", 1120, 157, 10, 600, "#5e6b77", "right", mono);

    const boxes = [
      { x: 60, color: "white", label: "WHITE" },
      { x: 622, color: "black", label: "BLACK" },
    ];
    for (const box of boxes) {
      const side = summary[box.color] || {};
      const name =
        cardPlayers[box.color] ||
        gamePlayers[box.color]?.name ||
        (box.color === "white" ? "White" : "Black");
      round(box.x, 196, 518, 200, 14, "rgba(255, 255, 255, 0.025)", "rgba(255, 255, 255, 0.07)");
      round(
        box.x + 24,
        220,
        42,
        42,
        12,
        box.color === "white" ? "#e8edf1" : "#141b22",
        "rgba(255, 255, 255, 0.14)",
      );
      write(initials(name), box.x + 45, 247, 16, 700, box.color === "white" ? "#0a0f13" : "#e8edf1", "center");
      write(name, box.x + 80, 240, 19, 600, "#e8edf1");
      write(box.label, box.x + 80, 262, 11, 600, "#5e6b77", "left", mono);
      write("ACCURACY", box.x + 24, 306, 11, 600, "#8d99a5", "left", mono);
      write(percent(side.accuracy), box.x + 24, 358, 40, 700, "#e8edf1");
      const elo = side.estimated_elo?.elo;
      write(`EST. ELO ${elo ? `≈ ${elo}` : "—"}`, box.x + 24, 382, 12, 600, "#b2bcc6", "left", mono);
      write(
        `ACPL ${side.acpl === null || side.acpl === undefined ? "—" : number(side.acpl, 1)}`,
        box.x + 250,
        382,
        12,
        600,
        "#b2bcc6",
        "left",
        mono,
      );
    }

    const chart = { x: 60, y: 416, w: 620, h: 152 };
    round(chart.x, chart.y, chart.w, chart.h, 14, "rgba(255, 255, 255, 0.022)", "rgba(255, 255, 255, 0.07)");
    write("ADVANTAGE CURVE", chart.x + 22, chart.y + 26, 10, 600, "#8d99a5", "left", mono);
    const points = report.eval_chart?.points || [];
    const plot = { x: chart.x + 22, y: chart.y + 40, w: chart.w - 44, h: chart.h - 62 };
    const midY = plot.y + plot.h / 2;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.10)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plot.x, midY);
    ctx.lineTo(plot.x + plot.w, midY);
    ctx.stroke();
    if (points.length >= 2) {
      const xAt = (index) => plot.x + (index / (points.length - 1)) * plot.w;
      const yAt = (value) => plot.y + (1 - clamp(value) / 100) * plot.h;
      ctx.beginPath();
      ctx.moveTo(xAt(0), midY);
      points.forEach((point, index) => ctx.lineTo(xAt(index), yAt(point.win_prob_white)));
      ctx.lineTo(xAt(points.length - 1), midY);
      ctx.closePath();
      const area = ctx.createLinearGradient(0, plot.y, 0, plot.y + plot.h);
      area.addColorStop(0, "rgba(95, 215, 164, 0.42)");
      area.addColorStop(0.5, "rgba(95, 215, 164, 0.04)");
      area.addColorStop(1, "rgba(224, 104, 104, 0.34)");
      ctx.fillStyle = area;
      ctx.fill();
      ctx.beginPath();
      points.forEach((point, index) => {
        const x = xAt(index);
        const y = yAt(point.win_prob_white);
        if (index) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
      });
      ctx.strokeStyle = "#e8edf1";
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      write("No curve available", chart.x + 22, plot.y + plot.h / 2 + 4, 12, 500, "#5e6b77");
    }

    const panel = { x: 700, y: 416, w: 440, h: 152 };
    round(panel.x, panel.y, panel.w, panel.h, 14, "rgba(255, 255, 255, 0.022)", "rgba(255, 255, 255, 0.07)");
    write("MOVE CLASSIFICATION", panel.x + 22, panel.y + 26, 10, 600, "#8d99a5", "left", mono);
    CLASS_ORDER.forEach((key, index) => {
      const meta = CLASS_META[key];
      const symbol = key === "book" ? "≡" : meta.symbol;
      const x = panel.x + 22 + (index < 5 ? 0 : 202);
      const y = panel.y + 52 + (index % 5) * 21;
      write(symbol, x, y, 12, 600, classColor(key), "left", mono);
      write(meta.label, x + 26, y, 12, 500, "#b2bcc6");
      write(String(counts[key] || 0), x + 160, y, 12, 600, "#e8edf1", "right", mono);
    });

    write(`Result ${report.result || "*"}`, 60, 604, 11, 500, "#5e6b77", "left", mono);
    write("Generated by Chess Master · Stockfish Game Review", 1140, 604, 11, 500, "#5e6b77", "right", mono);
    return canvas;
  }

  /* --------------------------------------------------------------------
     Classification matrix
     -------------------------------------------------------------------- */

  renderMatrix() {
    const container = this.refs.matrix;
    if (!container) return;
    container.replaceChildren();
    const white = this.report?.summary?.white?.counts || {};
    const black = this.report?.summary?.black?.counts || {};

    for (const key of CLASS_ORDER) {
      const meta = CLASS_META[key];
      const whiteCount = Number(white[key] || 0);
      const blackCount = Number(black[key] || 0);
      const total = whiteCount + blackCount;
      const row = element("div", `matrix-row review-class-${key}`);
      row.dataset.matrixTier = key;
      row.setAttribute("role", "button");
      row.tabIndex = total ? 0 : -1;
      if (!total) row.setAttribute("aria-disabled", "true");
      if (this.filter?.tier === key) row.classList.add("is-filtered");

      const whiteButton = element("button", "matrix-count matrix-count-white", String(whiteCount));
      whiteButton.type = "button";
      whiteButton.dataset.matrixColor = "white";
      whiteButton.dataset.matrixTier = key;
      whiteButton.disabled = whiteCount === 0;
      whiteButton.setAttribute(
        "aria-label",
        `${whiteCount} ${meta.label} ${whiteCount === 1 ? "move" : "moves"} for White`,
      );

      const tier = element("span", "matrix-tier");
      const icon = element("span", "matrix-icon");
      if (key === "book") icon.append(bookGlyph());
      else icon.textContent = meta.symbol;
      tier.append(icon, element("span", "matrix-label", meta.label));

      const blackButton = element("button", "matrix-count matrix-count-black", String(blackCount));
      blackButton.type = "button";
      blackButton.dataset.matrixColor = "black";
      blackButton.dataset.matrixTier = key;
      blackButton.disabled = blackCount === 0;
      blackButton.setAttribute(
        "aria-label",
        `${blackCount} ${meta.label} ${blackCount === 1 ? "move" : "moves"} for Black`,
      );

      row.append(whiteButton, tier, blackButton);
      container.append(row);
    }
  }

  handleMatrix(event, explicitRow = null) {
    const row = explicitRow || event.target.closest(".matrix-row");
    if (!row) return;
    const colorButton = event.target.closest?.("[data-matrix-color]");
    const color = colorButton?.dataset.matrixColor || null;
    const tier = row.dataset.matrixTier;
    if (!tier) return;

    const counts = {
      white: this.report?.summary?.white?.counts || {},
      black: this.report?.summary?.black?.counts || {},
    };
    const total = color ? Number(counts[color]?.[tier] || 0) : Number(counts.white[tier] || 0) + Number(counts.black[tier] || 0);
    if (!total) {
      toast({
        title: `No ${CLASS_META[tier]?.label || tier} moves`,
        description: color ? `There are none for ${color}.` : "No move in this game earned that tier.",
        kind: "info",
        duration: 2600,
      });
      return;
    }

    const same = this.filter?.tier === tier && this.filter?.color === color;
    this.setFilter(same ? null : { tier, color });
    if (!same) this.jumpToTier(tier, color);
  }

  setFilter(filter) {
    this.filter = filter || null;
    this.renderMatrix();
    this.renderFilter();
    this.renderMoves();
  }

  renderFilter() {
    const filter = this.filter;
    if (!this.refs.filter) return;
    this.refs.filter.hidden = !filter;
    if (!filter) return;
    const count = (this.report?.moves || []).filter(
      (move) => move.classification === filter.tier && (!filter.color || move.color === filter.color),
    ).length;
    if (this.refs.filterLabel) {
      const color = filter.color ? ` · ${filter.color}` : "";
      this.refs.filterLabel.textContent = `Filtering ${CLASS_META[filter.tier]?.label || filter.tier}${color} (${count})`;
    }
  }

  jumpToTier(tier, color) {
    const pool = (this.report?.moves || []).filter(
      (move) => move.classification === tier && (!color || move.color === color),
    );
    if (!pool.length) return false;
    const ply = Number(this.game?.ply) || 0;
    const next = pool.find((move) => move.ply > ply) || pool[0];
    if (!next) return false;
    this.onNavigate(next.ply);
    this.revealCoach();
    return true;
  }

  /* --------------------------------------------------------------------
     Move list and shared pieces
     -------------------------------------------------------------------- */

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

  renderMoves() {
    if (!this.refs.moves || !this.refs.moveList) return;
    const all = this.report?.moves || [];
    const moves = this.filter
      ? all.filter(
          (move) =>
            move.classification === this.filter.tier &&
            (!this.filter.color || move.color === this.filter.color),
        )
      : all;
    this.refs.moves.hidden = all.length === 0;
    this.refs.moveList.replaceChildren();
    const current = Number(this.game?.ply) || 0;

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
      if (move.ply === current) row.classList.add("is-current");

      const index = element(
        "span",
        "review-move-index",
        move.color === "white" ? `${move.move_number}.` : `${move.move_number}…`,
      );
      const san = element("strong", "review-move-san", move.san || move.uci || "—");
      const grade = element(
        "span",
        "review-move-grade",
        `${meta.symbol} ${meta.label}${move.is_forced ? " · forced" : ""}`,
      );
      const loss = element(
        "span",
        "review-move-loss",
        move.is_book || move.win_loss === null || move.win_loss === undefined
          ? "—"
          : `${Number(move.win_loss).toFixed(1)}%`,
      );
      row.append(index, san, grade, loss);
      this.refs.moveList.append(row);
    }

    const currentRow = this.refs.moveList.querySelector(".is-current");
    if (currentRow) {
      // Keep the long inspector still; only the move list gets its own scroll.
      const listRect = this.refs.moveList.getBoundingClientRect();
      const rowRect = currentRow.getBoundingClientRect();
      if (rowRect.top < listRect.top) {
        this.refs.moveList.scrollTop -= listRect.top - rowRect.top + 6;
      } else if (rowRect.bottom > listRect.bottom) {
        this.refs.moveList.scrollTop += rowRect.bottom - listRect.bottom + 6;
      }
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
      message = `${timing.searches || 0} searches · ${timing.cache_hits || 0} cache hits · ${seconds.toFixed(1)}s. Pass 2 confirmed ${this.report.totals?.confirmed_positions || 0} positions.`;
    } else if (status === "complete" && progress?.done) {
      message = "The review is complete. The full move list is loading.";
    }
    this.refs.note.hidden = !message;
    if (this.refs.noteCopy && message) this.refs.noteCopy.textContent = message;
  }

  /* --------------------------------------------------------------------
     Board scene
     -------------------------------------------------------------------- */

  retryScene(orientation) {
    const retry = this.retry;
    if (!retry) return null;
    const move = retry.move;
    const best = move.best_move;
    if (retry.phase === "awaiting") {
      return {
        arrows:
          move.from && move.to
            ? [{ orig: move.from, dest: move.to, brush: "retry" }]
            : [],
        overlay: {
          tint: { square: move.to, classification: move.classification || "mistake" },
          ring: retry.hint && best?.from ? { square: best.from } : null,
          banner: null,
          orientation,
        },
      };
    }
    if (retry.phase === "correct" || retry.phase === "solution") {
      const meta = CLASS_META.best;
      return {
        arrows: best?.from && best?.to ? [{ orig: best.from, dest: best.to, brush: "best" }] : [],
        overlay: {
          badge: best?.to
            ? {
                square: best.to,
                classification: "best",
                symbol: meta.symbol,
                label: meta.label,
                san: best.san,
              }
            : null,
          tint: best?.to ? { square: best.to, classification: "best" } : null,
          banner: null,
          orientation,
        },
      };
    }
    if (retry.phase === "wrong" && retry.attempt) {
      return {
        arrows: [{ orig: retry.attempt.from, dest: retry.attempt.to, brush: "miss" }],
        overlay: {
          tint: { square: retry.attempt.to, classification: "miss" },
          banner: null,
          orientation,
        },
      };
    }
    if (retry.phase === "alternative" && retry.attempt) {
      return {
        arrows: [{ orig: retry.attempt.from, dest: retry.attempt.to, brush: "good" }],
        overlay: {
          tint: { square: retry.attempt.to, classification: "good" },
          banner: null,
          orientation,
        },
      };
    }
    return null;
  }

  boardScene(game, orientation, preview) {
    if (this.retry) return this.retryScene(orientation);
    if (!this.report || !game) return null;
    if (preview?.active) {
      return {
        arrows: preview.move
          ? [{ orig: preview.move.from, dest: preview.move.to, brush: "pv" }]
          : [],
        overlay: { banner: preview.banner, preview: true },
      };
    }
    const ply = Number(game.ply) || 0;
    if (ply < 1) return null;
    const move = this.report.moves?.find((item) => item.ply === ply);
    if (!move) return null;

    const arrows = [];
    const best = move.best_move;
    if (best?.from && best?.to && best.uci && best.uci !== move.uci && !move.is_book) {
      arrows.push({ orig: best.from, dest: best.to, brush: "alternative" });
    }
    if (move.from && move.to) {
      arrows.push({ orig: move.from, dest: move.to, brush: move.classification || "best" });
    }

    const meta = CLASS_META[move.classification] || { label: move.label, symbol: move.symbol };
    return {
      arrows,
      overlay: {
        badge: {
          square: move.to,
          classification: move.classification || "best",
          symbol: meta.symbol || move.symbol,
          label: meta.label || move.label,
          san: move.san,
        },
        tint: { square: move.to, classification: move.classification || "best" },
        banner: null,
        orientation,
      },
    };
  }

  /* --------------------------------------------------------------------
     Lifecycle
     -------------------------------------------------------------------- */

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
    this.stopPlayback();
    this.preview = null;
    try {
      const result = await call("start_game_review", this.preset, {
        use_book: true,
        force: true,
      });
      this.resetLocal();
      this.update(result.review);
      this.refs.dashboard?.closest(".tab-panel")?.scrollTo({ top: 0, behavior: "auto" });
      this.onSceneChange();
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
      this.onSceneChange();
    } catch (error) {
      toast({
        title: "Could not cancel the review",
        description: errorMessage(error),
        kind: "error",
      });
    }
  }

  focusOrStart() {
    if (this.report) {
      this.refs.dashboard?.scrollIntoView({ block: "nearest" });
      this.refs.chart?.focus({ preventScroll: true });
      return;
    }
    if (this.isRunning()) {
      toast({
        title: "Review already running",
        description: "The shared Stockfish worker is grading this game.",
        kind: "info",
        duration: 2800,
      });
      return;
    }
    this.start();
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
        this.refs.dashboard?.closest(".tab-panel")?.scrollTo({ top: 0, behavior: "auto" });
        this.onSceneChange();
      }
    } catch (error) {
      console.warn("Could not load the full review", error);
      this.failedSignature = signature;
    } finally {
      this.loadingReport = false;
    }
  }
}
