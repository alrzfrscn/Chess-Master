/** Application entry point: bridge boot, state rendering, input routing. */

import { call, connect, detailLines, errorMessage } from "./api.js";
import { BoardController } from "./board.js";
import { ImportPanel } from "./importPanel.js";
import { renderMoves } from "./moves.js";
import { renderPlayers } from "./players.js";
import { ReviewPanel } from "./reviewPanel.js";
import {
  app,
  canHumanMove,
  modeTitle,
  resultLabel,
  resultTone,
  sessionEvent,
  sourceLabel,
  titleCase,
} from "./state.js";
import { askConfirm, closeDialog, openDialog, TabController, toast } from "./ui.js";

const $ = (id) => document.getElementById(id);

const refs = {
  board: $("board"),
  boardLoading: $("board-loading"),
  promotion: $("promotion"),
  promotionOptions: $("promotion-options"),
  topPlayer: $("player-top"),
  bottomPlayer: $("player-bottom"),
  newGameDialog: $("new-game-dialog"),
  engineDialog: $("engine-dialog"),
  confirmDialog: $("confirm-dialog"),
  bootError: $("boot-error"),
  bootErrorMessage: $("boot-error-message"),
  stageTitle: $("stage-title"),
  stageEvent: $("stage-event"),
  stageResult: $("stage-result"),
  sessionMode: $("session-mode"),
  sessionId: $("session-id"),
  sessionEvent: $("session-event"),
  sessionPlies: $("session-plies"),
  sessionSource: $("session-source"),
  sessionDot: $("session-dot"),
  bridgeDot: $("bridge-dot"),
  bridgeLabel: $("bridge-label"),
  bridgeState: $("bridge-state"),
  appVersion: $("app-version"),
  stageImport: $("btn-stage-import"),
  newGameButton: $("btn-new-game"),
  evalRail: $("eval-rail"),
  evalFill: $("eval-fill"),
  evalValue: $("eval-value"),
  evalPhase: $("eval-phase"),
  engineStrip: $("engine-strip"),
  engineStripDot: $("engine-strip-dot"),
  engineStripName: $("engine-strip-name"),
  engineStripEval: $("engine-strip-eval"),
  engineStripPv: $("engine-strip-pv"),
  engineStripDepth: $("engine-strip-depth"),
  engineStatusCard: $("engine-status-card"),
  engineStatusDot: $("engine-status-dot"),
  engineStatusTitle: $("engine-status-title"),
  engineStatusCopy: $("engine-status-copy"),
  engineStatusBadge: $("engine-status-badge"),
  reviewCta: $("review-cta"),
  reviewCtaTitle: $("review-cta-title"),
  reviewCtaCopy: $("review-cta-copy-text"),
  reviewCtaButton: $("btn-review-cta"),
  reviewCtaLabel: $("btn-review-cta-label"),
};

refs.players = { top: refs.topPlayer, bottom: refs.bottomPlayer };

refs.turnLabel = $("turn-label");
refs.turnDot = $("turn-dot");
refs.moves = {
  list: $("move-list"),
  empty: $("move-empty"),
  count: $("move-count"),
  head: document.querySelector(".moves-grid-head"),
  branchNote: $("branch-note"),
  positionTitle: $("position-title"),
  positionSub: $("position-sub"),
  positionResult: $("position-result"),
  positionIcon: $("position-icon"),
  turnLabel: refs.turnLabel,
  turnDot: refs.turnDot,
  nav: {
    first: document.querySelector('[data-nav="first"]'),
    prev: document.querySelector('[data-nav="prev"]'),
    next: document.querySelector('[data-nav="next"]'),
    last: document.querySelector('[data-nav="last"]'),
  },
};

let board = null;
let panel = null;
let reviewPanel = null;
let tabs = null;
let booting = false;

/* --------------------------------------------------------------------------
   State rendering
   -------------------------------------------------------------------------- */

function renderAll() {
  const game = app.game;
  if (!game) return;

  const preview = reviewPanel?.previewState() || null;
  app.preview = preview;
  const interactive = !app.busy && !app.navigating && !preview && canHumanMove(game);
  board.render(game, { orientation: app.orientation, canPlay: interactive, preview });
  renderPlayers(game, app.orientation, refs.players);
  renderMoves(game, refs.moves);
  renderHeader(game);
  renderSession(game);
  renderModeNav(game);
  renderEval(game);
  renderEngineStrip();

  panel.setGame(game);
  panel.refreshExport();
  reviewPanel?.setGame(game);
  reviewPanel?.update(game.review);
  syncArrows();
  renderReviewCta(game);

  const evalButton = document.querySelector('[data-action="eval"]');
  if (evalButton) evalButton.setAttribute("aria-pressed", String(app.evalVisible));
  const arrowButton = document.querySelector('[data-action="arrows"]');
  if (arrowButton) arrowButton.setAttribute("aria-pressed", String(app.arrowsEnabled));
  document.body.classList.toggle("eval-hidden", !app.evalVisible);

  const pageEvent = String(game.event || modeTitle(game)).replace(/^Chess Master\s*[—–-]\s*/i, "");
  document.title = `Chess Master — ${pageEvent}`;
  const resetButton = document.querySelector('[data-action="reset"]');
  if (resetButton) resetButton.disabled = game.total_plies === 0;

  window.dispatchEvent(new CustomEvent("chessmaster:state", { detail: game }));
}

function stateFingerprint(state) {
  if (!state) return "";
  const engine = state.engine || {};
  const evaluation = state.evaluation || {};
  return [
    state.game_id,
    state.revision,
    state.ply,
    state.total_plies,
    state.orientation,
    engine.thinking,
    engine.status,
    engine.available,
    engine.bot_elo,
    evaluation.updated_at,
    evaluation.available,
    evaluation.enabled,
    evaluation.arrows,
    evaluation.score_text,
    state.review ? state.review.status : "",
    state.review && state.review.signature ? state.review.signature : "",
    state.review && state.review.progress ? state.review.progress.done : "",
    state.review && state.review.progress ? state.review.progress.total : "",
  ].join("|");
}

function renderModeNav(game) {
  for (const button of document.querySelectorAll("[data-mode]")) {
    const selected = Boolean(game && game.mode === button.dataset.mode);
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}

function renderEval(game) {
  const rail = refs.evalRail;
  if (!rail) return;
  rail.dataset.orientation = app.orientation;

  const evaluation = app.evaluation || (game && game.evaluation) || null;
  const engine = app.engine || (game && game.engine) || null;
  const thinking = Boolean(evaluation && evaluation.thinking);
  const available = Boolean(evaluation && evaluation.available) && !isEngineOffline(engine);

  rail.classList.toggle("is-thinking", thinking);
  rail.classList.toggle("is-offline", !available);
  rail.classList.toggle("is-mate", Boolean(evaluation && Number.isInteger(evaluation.mate)));

  if (!evaluation || !available) {
    if (refs.evalFill) refs.evalFill.style.height = "50%";
    if (refs.evalValue) {
      refs.evalValue.textContent = "—";
      refs.evalValue.dataset.tone = "neutral";
    }
    if (refs.evalPhase) refs.evalPhase.textContent = "OFF";
    rail.title = engine && engine.note ? engine.note : "Live Stockfish evaluation is unavailable.";
    return;
  }

  const white = Number(evaluation.win_prob_white);
  const percentage = Number.isFinite(white) ? Math.max(0, Math.min(100, white)) : 50;
  if (refs.evalFill) refs.evalFill.style.height = `${percentage}%`;

  const label = evaluation.score_text || (evaluation.mate != null ? `${evaluation.mate > 0 ? "M" : "-M"}${Math.abs(evaluation.mate)}` : thinking ? "…" : "0.0");
  if (refs.evalValue) {
    refs.evalValue.textContent = label;
    refs.evalValue.dataset.tone =
      evaluation.mate != null
        ? evaluation.mate > 0
          ? "white"
          : "black"
        : Number(evaluation.score_cp) > 20
          ? "white"
          : Number(evaluation.score_cp) < -20
            ? "black"
            : "neutral";
  }

  if (refs.evalPhase) {
    if (evaluation.terminal === "checkmate") refs.evalPhase.textContent = "MATE";
    else if (thinking) refs.evalPhase.textContent = "…";
    else if (evaluation.cached) refs.evalPhase.textContent = "CACHE";
    else if (evaluation.depth) refs.evalPhase.textContent = `d${evaluation.depth}`;
    else refs.evalPhase.textContent = "LIVE";
  }

  const engineName = evaluation.engine || (engine && engine.name) || "Stockfish";
  const depthText = evaluation.depth ? `depth ${evaluation.depth}` : "live";
  rail.title = `${engineName} · ${label} · ${depthText} · White POV`;
}

function isEngineOffline(engine) {
  return Boolean(engine && (!engine.available || engine.status === "error"));
}

function applyReviewScene() {
  if (!board) return false;
  const scene = reviewPanel?.boardScene(app.game, app.orientation, app.preview) || null;
  if (scene) {
    board.setArrows(scene.arrows || []);
    board.setReviewOverlay(scene.overlay || null);
    return true;
  }
  board.setReviewOverlay(null);
  return false;
}

function syncArrows() {
  if (!board) return;
  if (applyReviewScene()) return;
  const game = app.game;
  const evaluation = app.evaluation || (game && game.evaluation);
  if (!app.arrowsEnabled || !game || !evaluation || !evaluation.best_move) {
    board.clearArrows();
    return;
  }
  const token = evaluation.token || {};
  const fresh =
    token.game_id === game.game_id && token.revision === game.revision && token.ply === game.ply;
  if (!fresh) {
    board.clearArrows();
    return;
  }
  board.setEngineArrow(evaluation.best_move);
}

function renderEngineStrip() {
  const strip = refs.engineStrip;
  if (!strip) return;
  const game = app.game;
  const engine = app.engine || (game && game.engine) || null;
  const evaluation = app.evaluation || (game && game.evaluation) || null;
  const wanted = Boolean(
    game && (game.mode === "stockfish" || app.evalVisible || app.arrowsEnabled),
  );
  strip.hidden = !wanted;
  if (!wanted || !engine) return;

  const offline = isEngineOffline(engine);
  const thinking = Boolean(engine.thinking);
  const ready = engine.available && engine.status === "ready";
  strip.classList.toggle("is-warning", offline);
  strip.classList.toggle("is-thinking", thinking && !offline);
  strip.title = engine.path ? `${engine.name || "Stockfish"} · ${engine.path}` : engine.note || "";

  if (refs.engineStripDot) {
    refs.engineStripDot.classList.remove("is-online", "is-pending", "is-error");
    refs.engineStripDot.classList.add(offline ? "is-error" : ready ? "is-online" : "is-pending");
  }

  if (refs.engineStripName) {
    if (offline) refs.engineStripName.textContent = "Stockfish offline";
    else if (engine.status === "starting") refs.engineStripName.textContent = "Starting Stockfish";
    else refs.engineStripName.textContent = `${engine.name || "Stockfish"}${game.mode === "stockfish" && engine.bot_elo ? ` · ${engine.bot_elo}` : ""}`;
  }

  if (refs.engineStripEval) {
    refs.engineStripEval.textContent =
      offline || !evaluation || !evaluation.available || !evaluation.updated_at
        ? "—"
        : evaluation.score_text || "0.0";
  }

  if (refs.engineStripPv) {
    if (offline) {
      refs.engineStripPv.textContent =
        engine.status === "error"
          ? engine.message || "The engine stopped unexpectedly. Open Engine setup."
          : "Live evaluation and bot moves are offline. Open Engine setup.";
    } else if (thinking) {
      refs.engineStripPv.textContent = "Stockfish is thinking…";
    } else if (evaluation && evaluation.updated_at) {
      const pv = Array.isArray(evaluation.pv_san) ? evaluation.pv_san.slice(0, 7) : [];
      refs.engineStripPv.textContent = pv.length ? pv.join(" ") : "No principal variation.";
    } else if (app.evalVisible || app.arrowsEnabled) {
      refs.engineStripPv.textContent = "Analysing the current position…";
    } else {
      refs.engineStripPv.textContent = "Live evaluation is off.";
    }
  }

  if (refs.engineStripDepth) {
    if (offline) {
      refs.engineStripDepth.textContent = "Set path";
    } else if (thinking) {
      refs.engineStripDepth.textContent = `d${engine.analysis?.depth || 16}`;
    } else if (evaluation && evaluation.updated_at) {
      const lines = Number(evaluation.multipv) || 1;
      refs.engineStripDepth.textContent = `d${evaluation.depth || 0} · ${lines} line${lines === 1 ? "" : "s"}${evaluation.cached ? " · cache" : ""}`;
    } else {
      refs.engineStripDepth.textContent = engine.available ? "Engine ready" : "Offline";
    }
  }
}

function applyState(state, { autoOrient = false } = {}) {
  if (!state) return;
  app.game = state;
  app.evaluation = state.evaluation || null;
  app.engine = state.engine || null;
  if (autoOrient && state.orientation) app.orientation = state.orientation;
  app.pendingPromotion = null;
  board.clearPromotion();
  hidePromotion();
  renderAll();
  app.stateFingerprint = stateFingerprint(state);
}

function renderHeader(game) {
  if (refs.stageTitle) refs.stageTitle.textContent = modeTitle(game);
  if (refs.stageEvent) refs.stageEvent.textContent = game.event || modeTitle(game);
  if (refs.stageResult) {
    refs.stageResult.textContent = resultLabel(game);
    refs.stageResult.className = `result-pill ${resultTone(game)}`;
  }
}

function renderSession(game) {
  if (refs.sessionMode) refs.sessionMode.textContent = game.mode_label || modeTitle(game);
  if (refs.sessionId) refs.sessionId.textContent = `#${String(game.game_id || "").slice(0, 6).toUpperCase()}`;
  if (refs.sessionEvent) refs.sessionEvent.textContent = sessionEvent(game);
  if (refs.sessionPlies) refs.sessionPlies.textContent = `${game.total_plies} ${game.total_plies === 1 ? "ply" : "plies"}`;
  if (refs.sessionSource) refs.sessionSource.textContent = sourceLabel(game);
}

function renderReviewCta(game) {
  if (!refs.reviewCta) return;
  const finished = Boolean(game && game.status?.state && game.status.state !== "in_progress");
  const imported = Boolean(game && game.source === "pgn" && game.total_plies > 0);
  const show = Boolean(game && game.total_plies > 0 && (finished || imported) && app.activeTab !== "review");
  refs.reviewCta.hidden = !show;
  if (!show) return;

  const complete = Boolean(reviewPanel?.report);
  if (refs.reviewCtaTitle) {
    refs.reviewCtaTitle.textContent = finished ? "Game complete" : "Game imported";
  }
  if (refs.reviewCtaCopy) {
    refs.reviewCtaCopy.textContent = complete
      ? "Your Game Review is ready to reopen."
      : "Grade every move, find the turning point and replay the engine line.";
  }
  if (refs.reviewCtaLabel) {
    refs.reviewCtaLabel.textContent = complete ? "Open Game Review" : "Analyze in Game Review";
  }
}

function bridgeStatus(status, info = null) {
  const dot = refs.bridgeDot;
  const footerDot = refs.bridgeState ? refs.bridgeState.querySelector(".status-dot") : null;
  const footerText = refs.bridgeState ? refs.bridgeState.querySelector("span:last-child") : null;

  for (const target of [dot, footerDot, refs.sessionDot]) {
    if (!target) continue;
    target.classList.remove("is-pending", "is-error", "is-online");
    target.classList.add(status === "ready" ? "is-online" : status === "error" ? "is-error" : "is-pending");
  }

  if (refs.bridgeLabel) {
    if (status === "ready") {
      refs.bridgeLabel.textContent =
        app.bridgeMode === "native" ? "Python bridge · native" : "Python bridge · dev HTTP";
    } else if (status === "error") {
      refs.bridgeLabel.textContent = "Bridge offline";
    } else {
      refs.bridgeLabel.textContent = "Connecting to bridge";
    }
  }

  if (footerText) {
    footerText.textContent =
      status === "ready"
        ? `Bridge ready · v${(info && info.version) || app.apiInfo?.version || "1.0.0"}`
        : status === "error"
          ? "Bridge unavailable"
          : "Bridge handshake";
  }

  if (info && refs.appVersion) {
    refs.appVersion.textContent = `v${info.version || "1.0.0"} · Phase ${info.phase || 4}`;
  }
}

/* --------------------------------------------------------------------------
   Moves
   -------------------------------------------------------------------------- */

async function playMove(uci) {
  if (!uci || app.busy) return;
  const previous = app.game;
  const branching = Boolean(previous && previous.branch_available);
  if (reviewPanel?.hasPreview()) reviewPanel.clearPreview();
  app.busy = true;
  board?.clearArrows();
  try {
    const state = await call("make_move", uci);
    app.busy = false;
    applyState(state);
    if (branching) {
      toast({
        title: "New branch started",
        description: `The moves after ply ${previous.ply} were replaced by this line.`,
        kind: "info",
      });
    }
    announceOutcome(previous, state);
    pollSoon(60);
  } catch (error) {
    app.busy = false;
    applyState(previous);
    toast({
      title: "Move not played",
      description: composeError(error),
      kind: "error",
    });
  }
}

function announceOutcome(previous, next) {
  if (!previous || previous.status.state !== "in_progress" || next.status.state === "in_progress") return;
  if (next.status.state === "checkmate") {
    toast({
      title: "Checkmate",
      description: `${titleCase(next.status.winner)} wins the game.`,
      kind: "success",
      duration: 6500,
    });
  } else {
    const reason = (next.status.reason || "draw").replace(/_/g, " ");
    toast({ title: "Draw", description: `The game ends: ${reason}.`, kind: "info", duration: 6000 });
  }
}

async function navigateTo(index) {
  if (!app.game || app.busy || app.navigating || app.pendingPromotion) return;
  const total = app.game.total_plies;
  const target = Math.max(0, Math.min(total, Number(index)));
  if (!Number.isFinite(target) || target === app.game.ply) return;

  if (reviewPanel?.hasPreview()) reviewPanel.clearPreview();
  app.navigating = true;
  board?.clearArrows();
  try {
    const state = await call("navigate_ply", target);
    applyState(state);
    pollSoon(60);
  } catch (error) {
    toast({ title: "Could not navigate", description: composeError(error), kind: "error" });
  } finally {
    app.navigating = false;
  }
}

async function navigateBy(delta) {
  if (!app.game) return;
  await navigateTo(app.game.ply + delta);
}

/* --------------------------------------------------------------------------
   Promotion
   -------------------------------------------------------------------------- */

const PROMOTIONS = [
  ["q", "Queen"],
  ["r", "Rook"],
  ["b", "Bishop"],
  ["n", "Knight"],
];

function showPromotion({ from, to, color }) {
  app.pendingPromotion = { from, to, color };
  const prefix = color === "black" ? "b" : "w";
  if (refs.promotionOptions) {
    const fragment = document.createDocumentFragment();
    for (const [piece, label] of PROMOTIONS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "promotion-option";
      button.dataset.promotion = piece;
      button.setAttribute("aria-label", `Promote to ${label}`);
      const img = document.createElement("img");
      img.src = `assets/pieces/${prefix}${piece.toUpperCase()}.svg`;
      img.alt = label;
      button.append(img);
      fragment.append(button);
    }
    refs.promotionOptions.replaceChildren(fragment);
  }
  if (refs.promotion) refs.promotion.hidden = false;
  requestAnimationFrame(() => refs.promotionOptions?.querySelector("button")?.focus());
}

function hidePromotion() {
  if (refs.promotion) refs.promotion.hidden = true;
}

function choosePromotion(piece) {
  const pending = app.pendingPromotion || board?.pendingPromotion;
  if (!pending) return;
  app.pendingPromotion = null;
  hidePromotion();
  board.clearPromotion();
  playMove(`${pending.from}${pending.to}${piece}`);
}

function cancelPromotion() {
  app.pendingPromotion = null;
  hidePromotion();
  board.cancelPromotion();
}

/* --------------------------------------------------------------------------
   Game actions
   -------------------------------------------------------------------------- */

async function withBusy(loader, { successTitle, successDescription = "", closeDialogAfter = null, autoOrient = false } = {}) {
  if (app.busy) return null;
  app.busy = true;
  const startButton = $("btn-start-game");
  if (startButton) startButton.disabled = true;
  try {
    const state = await loader();
    app.busy = false;
    applyState(state, { autoOrient });
    if (closeDialogAfter) closeDialog(closeDialogAfter);
    if (successTitle) toast({ title: successTitle, description: successDescription, kind: "success" });
    return state;
  } catch (error) {
    app.busy = false;
    toast({ title: "That did not work", description: composeError(error), kind: "error", duration: 6000 });
    return null;
  } finally {
    app.busy = false;
    if (startButton) startButton.disabled = false;
    if (app.game) renderAll();
  }
}

const ELO_BANDS = [
  [800, "Beginner"],
  [1200, "Casual"],
  [1600, "Club"],
  [2000, "Advanced"],
  [2400, "Master"],
  [2800, "Grandmaster"],
  [4000, "Maximum"],
];

function eloLabel(elo) {
  const band = ELO_BANDS.find(([limit]) => Number(elo) < limit);
  return band ? band[1] : "Maximum";
}

function openNewGame(mode = null) {
  if (!refs.newGameDialog) return;
  const game = app.game;
  const target = mode || (game && game.mode === "stockfish" ? "stockfish" : "pass_play");
  app.newMode = target;

  const whiteInput = $("new-white-name");
  const blackInput = $("new-black-name");
  const humanInput = $("new-human-name");
  if (whiteInput && blackInput) {
    const white = game && game.mode === "pass_play" ? game.players.white.name : "White";
    const black = game && game.mode === "pass_play" ? game.players.black.name : "Black";
    whiteInput.value = white;
    blackInput.value = black;
  }
  if (humanInput) {
    const color = game && game.mode === "stockfish" ? game.engine?.player_color : null;
    humanInput.value = color && game.players[color] ? game.players[color].name : "Player";
  }
  app.newColor = game && game.mode === "stockfish" ? game.engine?.player_color || "white" : "white";
  app.newElo = game && game.mode === "stockfish" ? game.engine?.bot_elo || 1400 : 1400;

  syncNewGameForm();
  openDialog(refs.newGameDialog);
  requestAnimationFrame(() => {
    const input = app.newMode === "stockfish" ? humanInput : whiteInput;
    input?.focus();
    input?.select?.();
  });
}

function syncNewGameForm() {
  const stockfish = app.newMode === "stockfish";
  for (const button of document.querySelectorAll("[data-new-mode]")) {
    const selected = button.dataset.newMode === app.newMode;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  const passFields = $("new-pass-fields");
  const engineFields = $("new-stockfish-fields");
  if (passFields) passFields.hidden = stockfish;
  if (engineFields) engineFields.hidden = !stockfish;

  for (const button of document.querySelectorAll("#new-color [data-color]")) {
    const selected = button.dataset.color === app.newColor;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }

  const slider = $("new-elo");
  if (slider) {
    slider.value = String(app.newElo);
    const percentage = ((Number(app.newElo) - 400) / (3000 - 400)) * 100;
    slider.style.setProperty("--fill", `${Math.max(0, Math.min(100, percentage))}%`);
  }
  const value = $("new-elo-value");
  if (value) value.textContent = String(app.newElo);
  const label = $("new-elo-label");
  if (label) label.textContent = eloLabel(app.newElo);
  for (const button of document.querySelectorAll("#new-elo-presets [data-elo]")) {
    button.classList.toggle("is-active", Number(button.dataset.elo) === Number(app.newElo));
  }
  const note = $("new-elo-note");
  if (note) {
    note.textContent =
      Number(app.newElo) >= 3000
        ? "Full-strength Stockfish with no artificial limits."
        : Number(app.newElo) < 1320
          ? "Skill Level, a shallow search budget and a weighted top-three move chooser."
          : "Calibrated with Stockfish's UCI_LimitStrength and UCI_Elo.";
  }
  const warning = $("new-engine-warning");
  if (warning) warning.hidden = !stockfish || !isEngineOffline(app.engine);
  const start = $("btn-start-game");
  if (start) start.textContent = stockfish ? "Start engine match" : "Start game";
}

async function startNewGame(event) {
  event.preventDefault();
  if (app.busy) return;
  const mode = app.newMode;
  let options;
  if (mode === "stockfish") {
    const human = ($("new-human-name")?.value || "").trim() || "Player";
    options = {
      player_color: app.newColor,
      bot_elo: Number(app.newElo),
      human_name: human,
    };
  } else {
    const white = ($("new-white-name")?.value || "").trim() || "White";
    const black = ($("new-black-name")?.value || "").trim() || "Black";
    options = { white_name: white, black_name: black };
  }

  const state = await withBusy(() => call("new_game", mode, options), {
    closeDialogAfter: refs.newGameDialog,
    autoOrient: true,
  });
  if (!state) return;

  if (mode === "stockfish") {
    const color = titleCase(state.orientation);
    toast({
      title: "Engine match ready",
      description: `You play ${color} against Stockfish ${state.engine.bot_elo}.`,
      kind: "success",
    });
  } else {
    toast({
      title: "New table",
      description: `${state.players.white.name} vs ${state.players.black.name}. White begins.`,
      kind: "success",
    });
  }
  tabs.setActive("moves");
  await pushAnalysisOptions();
  pollSoon(80);
  window.dispatchEvent(new CustomEvent("chessmaster:newgame"));
}

async function resetGame() {
  if (!app.game || app.busy) return;
  const confirmed = await askConfirm(refs.confirmDialog, {
    eyebrow: "Reset table",
    title: "Reset this game?",
    copy: "Every move in the current line will be discarded and the table returns to its starting position.",
    acceptLabel: "Reset game",
  });
  if (!confirmed) return;
  const reset = await withBusy(() => call("reset_game"), {
    successTitle: "Table reset",
    successDescription: "The starting position is back on the board.",
  });
  if (reset) tabs.setActive("moves");
}

function composeError(error) {
  const details = detailLines(error);
  return details ? `${errorMessage(error)} ${details}` : errorMessage(error);
}

/* --------------------------------------------------------------------------
   Chrome
   -------------------------------------------------------------------------- */

function flipBoard() {
  if (!app.game) return;
  app.orientation = app.orientation === "white" ? "black" : "white";
  renderAll();
}

async function toggleEval() {
  app.evalVisible = !app.evalVisible;
  document.body.classList.toggle("eval-hidden", !app.evalVisible);
  const button = document.querySelector('[data-action="eval"]');
  if (button) button.setAttribute("aria-pressed", String(app.evalVisible));
  renderEval(app.game);
  renderEngineStrip();
  syncArrows();
  await pushAnalysisOptions();
  pollSoon(60);
}

async function toggleArrows() {
  app.arrowsEnabled = !app.arrowsEnabled;
  const button = document.querySelector('[data-action="arrows"]');
  if (button) button.setAttribute("aria-pressed", String(app.arrowsEnabled));
  syncArrows();
  renderEngineStrip();
  if (app.arrowsEnabled && isEngineOffline(app.engine)) {
    toast({
      title: "Stockfish is offline",
      description: "Set the engine path to enable the best-move arrow.",
      kind: "warning",
    });
  }
  await pushAnalysisOptions();
  pollSoon(60);
}

async function pushAnalysisOptions() {
  try {
    const result = await call("set_analysis_options", {
      enabled: Boolean(app.evalVisible || app.arrowsEnabled),
      arrows: Boolean(app.arrowsEnabled),
    });
    if (result && app.game) {
      if (result.engine) app.game.engine = result.engine;
      if (result.evaluation) app.game.evaluation = result.evaluation;
      app.engine = result.engine || app.engine;
      app.evaluation = result.evaluation || app.evaluation;
      renderAll();
    }
  } catch (error) {
    console.warn("Could not update analysis options", error);
  }
}

function pollSoon(delay = 80) {
  window.setTimeout(() => pollEngine(), delay);
}

async function pollEngine() {
  if (!app.game || app.pollBusy || app.busy || app.navigating || app.pendingPromotion) return;
  if (document.querySelector("dialog[open]")) return;
  app.pollBusy = true;
  try {
    const result = await call("engine_poll");
    if (!result || !result.state) return;
    const state = result.state;
    const fingerprint = stateFingerprint(state);
    if (fingerprint !== app.stateFingerprint) {
      const previous = app.game;
      applyState(state);
      if (result.moved) {
        announceOutcome(previous, state);
        window.dispatchEvent(new CustomEvent("chessmaster:enginemove", { detail: result.moved }));
      }
      if (state.engine && state.engine.status === "error" && (!previous || !previous.engine || previous.engine.status !== "error")) {
        toast({
          title: "Stockfish stopped",
          description: state.engine.message || "Open Engine setup to choose another binary.",
          kind: "error",
          duration: 7000,
        });
      }
    }
  } catch (error) {
    console.warn("Engine poll failed", error);
  } finally {
    app.pollBusy = false;
  }
}

function startEnginePolling() {
  if (app.pollTimer) return;
  app.pollTimer = window.setInterval(pollEngine, 240);
  pollEngine();
}

function openImport() {
  tabs.setActive("import");
  requestAnimationFrame(() => panel?.textarea?.focus());
}

function renderEngineStatusCard() {
  const engine = app.engine;
  if (!refs.engineStatusCard) return;
  const offline = isEngineOffline(engine);
  const ready = Boolean(engine && engine.available && engine.status === "ready");
  refs.engineStatusCard.classList.toggle("is-warning", offline);
  refs.engineStatusCard.classList.toggle("is-ready", ready);
  if (refs.engineStatusDot) {
    refs.engineStatusDot.classList.remove("is-online", "is-pending", "is-error");
    refs.engineStatusDot.classList.add(offline ? "is-error" : ready ? "is-online" : "is-pending");
  }
  if (refs.engineStatusTitle) {
    refs.engineStatusTitle.textContent = offline
      ? engine?.status === "error"
        ? "Engine failed to start"
        : "Stockfish not found"
      : ready
        ? `${engine.name || "Stockfish"} ready`
        : "Starting Stockfish";
  }
  if (refs.engineStatusCopy) {
    refs.engineStatusCopy.textContent = offline
      ? engine?.message || engine?.note || "Choose a Stockfish executable below."
      : engine?.path || "One engine process, one owner thread.";
  }
  if (refs.engineStatusBadge) {
    refs.engineStatusBadge.textContent = offline
      ? "MISSING"
      : ready
        ? "READY"
        : "STARTING";
    refs.engineStatusBadge.dataset.tone = offline ? "error" : ready ? "success" : "info";
  }
}

function openEngineDialog() {
  if (!refs.engineDialog) return;
  const input = $("engine-path-input");
  if (input) input.value = app.engine?.path || "";
  renderEngineStatusCard();
  openDialog(refs.engineDialog);
  requestAnimationFrame(() => input?.focus());
}

async function configureEngine(event) {
  event?.preventDefault();
  const input = $("engine-path-input");
  if (!input || app.busy) return;
  const path = input.value.trim();
  app.busy = true;
  const save = $("btn-engine-save");
  if (save) save.disabled = true;
  try {
    const result = await call("configure_engine_path", path || null);
    app.engine = result.engine || app.engine;
    app.evaluation = result.evaluation || app.evaluation;
    if (app.game) {
      app.game.engine = app.engine;
      app.game.evaluation = app.evaluation;
    }
    renderAll();
    renderEngineStatusCard();
    if (app.engine?.available) {
      toast({
        title: "Engine connected",
        description: `${app.engine.name || "Stockfish"} is ready${app.engine.path ? ` · ${app.engine.path}` : ""}.`,
        kind: "success",
        duration: 6000,
      });
      pollSoon(80);
    } else {
      toast({
        title: "Engine not found",
        description: app.engine?.message || "Check the path and try again.",
        kind: "error",
        duration: 7000,
      });
    }
  } catch (error) {
    toast({ title: "Could not configure engine", description: composeError(error), kind: "error" });
  } finally {
    app.busy = false;
    if (save) save.disabled = false;
    if (app.game) renderAll();
  }
}

function autoDetectEngine() {
  const input = $("engine-path-input");
  if (input) input.value = "";
  configureEngine();
}

/* --------------------------------------------------------------------------
   Wiring
   -------------------------------------------------------------------------- */

function wireShell() {
  tabs = new TabController({
    tablist: document.querySelector(".inspector-tabs"),
    ink: $("tab-ink"),
    onChange: (name) => {
      app.activeTab = name;
      if (name === "import") panel?.refreshExport();
      renderReviewCta(app.game);
    },
  });

  board = new BoardController(refs.board, {
    onMove: (uci) => playMove(uci),
    onPromotion: (pending) => showPromotion(pending),
    loadingElement: refs.boardLoading,
  });

  panel = new ImportPanel({
    onGameChanged: (state) => applyState(state),
    onTabRequested: (name) => tabs.setActive(name),
  });

  reviewPanel = new ReviewPanel({
    onNavigate: (ply) => navigateTo(ply),
    onPreview: (preview) => {
      app.preview = preview;
      renderAll();
    },
    onSceneChange: () => {
      applyReviewScene();
      renderReviewCta(app.game);
    },
  });

  refs.newGameButton?.addEventListener("click", () => openNewGame());
  refs.stageImport?.addEventListener("click", openImport);
  refs.reviewCtaButton?.addEventListener("click", () => {
    tabs.setActive("review");
    reviewPanel?.focusOrStart();
  });
  $("new-game-form")?.addEventListener("submit", startNewGame);
  $("engine-form")?.addEventListener("submit", configureEngine);
  $("btn-engine-auto")?.addEventListener("click", autoDetectEngine);
  $("btn-engine-config")?.addEventListener("click", openEngineDialog);
  $("btn-engine-strip-config")?.addEventListener("click", openEngineDialog);

  for (const closer of document.querySelectorAll("[data-close-dialog]")) {
    closer.addEventListener("click", () => closeDialog(closer.closest("dialog")));
  }
  for (const dialog of [refs.newGameDialog, refs.engineDialog]) {
    dialog?.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
  }

  for (const button of document.querySelectorAll("[data-new-mode]")) {
    button.addEventListener("click", () => {
      app.newMode = button.dataset.newMode;
      syncNewGameForm();
    });
  }
  for (const button of document.querySelectorAll("#new-color [data-color]")) {
    button.addEventListener("click", () => {
      app.newColor = button.dataset.color;
      syncNewGameForm();
    });
  }
  $("new-elo")?.addEventListener("input", (event) => {
    app.newElo = Number(event.target.value);
    syncNewGameForm();
  });
  for (const button of document.querySelectorAll("#new-elo-presets [data-elo]")) {
    button.addEventListener("click", () => {
      app.newElo = Number(button.dataset.elo);
      syncNewGameForm();
    });
  }

  for (const button of document.querySelectorAll("[data-open-tab]")) {
    button.addEventListener("click", () => tabs.setActive(button.dataset.openTab));
  }

  for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => openNewGame(button.dataset.mode));
  }

  refs.moves.list?.addEventListener("click", (event) => {
    const cell = event.target.closest(".move-cell");
    if (cell && cell.dataset.ply) navigateTo(Number(cell.dataset.ply));
  });

  for (const button of document.querySelectorAll("[data-nav]")) {
    button.addEventListener("click", () => {
      const action = button.dataset.nav;
      if (action === "first") navigateTo(0);
      if (action === "prev") navigateBy(-1);
      if (action === "next") navigateBy(1);
      if (action === "last") navigateTo(app.game?.total_plies ?? 0);
    });
  }

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "flip") flipBoard();
      if (action === "reset") resetGame();
      if (action === "eval") toggleEval();
      if (action === "arrows") toggleArrows();
    });
  });

  refs.promotionOptions?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-promotion]");
    if (button) choosePromotion(button.dataset.promotion);
  });
  $("promotion-cancel")?.addEventListener("click", cancelPromotion);

  $("btn-boot-retry")?.addEventListener("click", () => {
    if (refs.bootError) refs.bootError.hidden = true;
    bootstrap();
  });

  document.addEventListener("keydown", onKeyboard);

  document.body.classList.toggle("eval-hidden", !app.evalVisible);

  window.chessMaster = {
    setArrows: (shapes) => board.setArrows(shapes),
    clearArrows: () => board.clearArrows(),
    getArrows: () => board.autoShapes,
    getState: () => app.game,
    getEvaluation: () => app.evaluation,
    getEngine: () => app.engine,
    pollEngine,
    setAnalysisOptions: (options) => call("set_analysis_options", options),
    configureEngine: (path) => call("configure_engine_path", path),
    startGameReview: (preset, options) => call("start_game_review", preset, options),
    cancelGameReview: () => call("cancel_game_review"),
    getGameReview: (full) => call("get_game_review", full),
    getReviewPanel: () => reviewPanel,
    getReviewReport: () => reviewPanel?.report || null,
    jumpKeyMoment: (direction = 1) => reviewPanel?.jumpKeyMoment(direction),
    showFollowUp: () => reviewPanel?.startPreview(),
    clearFollowUp: () => reviewPanel?.clearPreview(),
    getPreview: () => reviewPanel?.previewState() || null,
    getBoardScene: () => reviewPanel?.boardScene(app.game, app.orientation, app.preview) || null,
    toggleEval,
    toggleArrows,
    startGame: async (mode, options) => {
      const state = await call("new_game", mode, options);
      applyState(state, { autoOrient: true });
      await pushAnalysisOptions();
      pollSoon(40);
      return state;
    },
    playMove,
    flip: flipBoard,
    bridge: () => ({ mode: app.bridgeMode, info: app.apiInfo }),
  };
}

function onKeyboard(event) {
  if (event.defaultPrevented) return;

  if (event.key === "Escape" && reviewPanel?.hasPreview()) {
    event.preventDefault();
    reviewPanel.clearPreview();
    return;
  }

  if (event.key === "Escape" && app.pendingPromotion) {
    event.preventDefault();
    cancelPromotion();
    return;
  }

  if (document.querySelector("dialog[open]")) return;
  const target = event.target;
  if (target && target.closest && target.closest("input, textarea, select, [contenteditable='true']")) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;

  const keyMoment = event.key === "k" || event.key === "K" || event.key === " ";
  if (keyMoment && app.activeTab === "review" && reviewPanel?.report) {
    if (event.key === " " && target?.closest?.("button, a, [role='button']")) return;
    event.preventDefault();
    reviewPanel.jumpKeyMoment(event.shiftKey ? -1 : 1);
    return;
  }

  switch (event.key) {
    case "ArrowLeft":
      event.preventDefault();
      navigateBy(-1);
      break;
    case "ArrowRight":
      event.preventDefault();
      navigateBy(1);
      break;
    case "Home":
      event.preventDefault();
      navigateTo(0);
      break;
    case "End":
      event.preventDefault();
      navigateTo(app.game?.total_plies ?? 0);
      break;
    case "f":
    case "F":
      event.preventDefault();
      flipBoard();
      break;
    case "e":
    case "E":
      event.preventDefault();
      toggleEval();
      break;
    case "i":
    case "I":
      event.preventDefault();
      openImport();
      break;
    case "a":
    case "A":
      event.preventDefault();
      toggleArrows();
      break;
    default:
      break;
  }
}

/* --------------------------------------------------------------------------
   Boot
   -------------------------------------------------------------------------- */

async function bootstrap() {
  if (booting) return;
  booting = true;
  bridgeStatus("pending");
  try {
    app.bridgeMode = await connect();
    const [info, state, samples] = await Promise.all([
      call("get_api_info"),
      call("get_game_state"),
      call("get_samples"),
    ]);

    app.apiInfo = info;
    app.samples = samples && Array.isArray(samples.samples) ? samples.samples : [];
    app.bridgeMode = app.bridgeMode || (window.pywebview ? "native" : "http");

    if (panel) panel.setSamples(app.samples);
    if (state) applyState(state);
    bridgeStatus("ready", info);
    await pushAnalysisOptions();
    startEnginePolling();

    if (refs.bootError) refs.bootError.hidden = true;
    document.body.classList.add("is-ready");
    if (refs.boardLoading) refs.boardLoading.hidden = true;
  } catch (error) {
    console.error("Chess Master failed to boot", error);
    bridgeStatus("error");
    if (refs.bootError) refs.bootError.hidden = false;
    if (refs.bootErrorMessage) {
      refs.bootErrorMessage.textContent =
        error && error.name === "BridgeUnavailableError"
          ? error.message
          : `The interface loaded, but the game could not be read. ${composeError(error)}`;
    }
  } finally {
    booting = false;
  }
}

wireShell();
document.body.classList.add("is-ready");
bootstrap();
