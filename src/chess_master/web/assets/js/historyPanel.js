/** Local archive dashboard: KPI strip, performance trend, filters and actions. */

import { bridgeMode, call, errorMessage, shareGameUrl } from "./api.js";
import { askConfirm, copyText, downloadText, toast } from "./ui.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const MODE_LABEL = {
  stockfish: "Stockfish",
  pass_play: "Pass & Play",
  import: "Imported",
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

function svgNode(tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatBytes(size) {
  const bytes = Number(size) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function cleanLabel(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function resultText(row) {
  const result = row.result || "*";
  if (result === "1-0") return "1–0";
  if (result === "0-1") return "0–1";
  if (result === "1/2-1/2") return "½–½";
  return row.source === "pgn" ? "Imported" : "Unfinished";
}

function resultTone(row) {
  if (row.result === "1-0") return "is-white";
  if (row.result === "0-1") return "is-black";
  if (row.result === "1/2-1/2") return "is-draw";
  return "";
}

function pgnName(row) {
  const clean = (value) =>
    String(value || "Player")
      .normalize("NFKD")
      .replace(/[^\w\s.-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .slice(0, 40) || "Player";
  const stamp = String(row.updated_at || row.created_at || row.game_id || "").slice(0, 10);
  return `${clean(row.white_name)}-vs-${clean(row.black_name)}-${stamp || "game"}.pgn`;
}

function reviewPill(summary, tone) {
  if (summary.accuracy === null && summary.elo === null) return null;
  const parts = [];
  if (summary.accuracy !== null) parts.push(`${Number(summary.accuracy).toFixed(1)}%`);
  if (summary.elo !== null) parts.push(`≈${summary.elo}`);
  return node("span", `history-review-pill ${tone}`, `${summary.label} ${parts.join(" · ")}`);
}

export class HistoryPanel {
  constructor({ onOpen, notify = toast } = {}) {
    this.onOpen = onOpen || (() => {});
    this.notify = notify;
    this.mode = "";
    this.query = "";
    this.games = [];
    this.data = null;
    this.seq = 0;
    this.busy = false;

    this.refs = {
      chip: document.getElementById("history-state-chip"),
      total: document.getElementById("history-total"),
      wdl: document.getElementById("history-wdl"),
      accuracy: document.getElementById("history-accuracy"),
      elo: document.getElementById("history-elo"),
      trend: document.getElementById("history-trend"),
      trendEmpty: document.getElementById("history-trend-empty"),
      search: document.getElementById("history-search"),
      mode: document.getElementById("history-mode"),
      list: document.getElementById("history-list"),
      empty: document.getElementById("history-empty"),
      emptyTitle: document.querySelector("#history-empty .move-empty-title"),
      emptyCopy: document.querySelector("#history-empty .move-empty-copy"),
      dbSize: document.getElementById("history-db-size"),
      dbPath: document.getElementById("history-db-path"),
      copyPath: document.getElementById("btn-history-copy-path"),
      exportAll: document.getElementById("btn-history-export"),
      reset: document.getElementById("btn-history-reset"),
      confirm: document.getElementById("confirm-dialog"),
      navCount: document.getElementById("history-nav-count"),
      tabCount: document.getElementById("history-tab-count"),
    };

    this.wire();
  }

  wire() {
    let timer = null;
    this.refs.search?.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        this.query = this.refs.search.value.trim();
        this.refresh();
      }, 240);
    });
    this.refs.search?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      window.clearTimeout(timer);
      this.query = this.refs.search.value.trim();
      this.refresh();
    });

    this.refs.mode?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-history-mode]");
      if (!button) return;
      this.mode = button.dataset.historyMode || "";
      for (const item of this.refs.mode.querySelectorAll("[data-history-mode]")) {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", String(active));
      }
      this.refresh();
    });

    this.refs.list?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-history-action]");
      if (!button) return;
      const row = this.games.find((game) => game.game_id === button.dataset.historyGame);
      if (row) this.act(button.dataset.historyAction, row);
    });

    this.refs.copyPath?.addEventListener("click", () => this.copyPath());
    this.refs.exportAll?.addEventListener("click", () => this.exportAll());
    this.refs.reset?.addEventListener("click", () => this.reset());
  }

  setCount(total) {
    const value = String(Math.max(0, Number(total) || 0));
    if (this.refs.navCount) this.refs.navCount.textContent = value;
    if (this.refs.tabCount) this.refs.tabCount.textContent = value;
  }

  async refresh({ silent = false } = {}) {
    if (!this.refs.list) return;
    const sequence = ++this.seq;
    try {
      // shortcut: one 200-row page, add paging when the archive outgrows it.
      const data = await call(
        "list_archived_games",
        200,
        0,
        this.mode || null,
        this.query || null,
      );
      if (sequence !== this.seq) return;
      this.data = data;
      this.games = Array.isArray(data.games) ? data.games : [];
      this.render();
    } catch (error) {
      if (!silent) {
        this.notify({ title: "Could not read the archive", description: errorMessage(error), kind: "error" });
      }
    }
  }

  async refreshCount() {
    try {
      const data = await call("list_archived_games", 1, 0);
      this.setCount(data.total);
    } catch {
      // A missing count is cosmetic; the panel reports real errors.
    }
  }

  render() {
    const data = this.data || {};
    const stats = data.stats || {};
    if (this.refs.total) this.refs.total.textContent = String(stats.total || 0);
    if (this.refs.wdl) {
      this.refs.wdl.textContent = `${stats.white_wins || 0} / ${stats.draws || 0} / ${stats.black_wins || 0}`;
    }
    if (this.refs.accuracy) {
      this.refs.accuracy.textContent = stats.reviewed ? `${Number(stats.avg_accuracy || 0).toFixed(1)}%` : "—";
    }
    if (this.refs.elo) {
      this.refs.elo.textContent = stats.peak_elo ? String(stats.peak_elo) : "—";
    }
    if (this.refs.chip) {
      this.refs.chip.textContent = stats.total
        ? `${stats.reviewed || 0} reviewed`
        : "Empty";
    }
    this.setCount(stats.total);
    this.renderTrend(data.trend || []);
    this.renderList();
    this.renderDb(data.db_info);
  }

  renderTrend(points) {
    const container = this.refs.trend;
    if (!container) return;
    container.replaceChildren();
    const rows = (points || []).filter(
      (point) => point.accuracy !== null && point.accuracy !== undefined ||
        point.estimated_elo !== null && point.estimated_elo !== undefined,
    );
    const hasData = rows.length > 0;
    container.hidden = !hasData;
    if (this.refs.trendEmpty) this.refs.trendEmpty.hidden = hasData;
    if (!hasData) return;

    const width = 320;
    const height = 96;
    const padX = 8;
    const padY = 12;
    const x = (index) =>
      rows.length === 1 ? width / 2 : padX + (index / (rows.length - 1)) * (width - padX * 2);

    for (const ratio of [0.25, 0.5, 0.75]) {
      container.append(
        svgNode("line", {
          x1: padX,
          x2: width - padX,
          y1: padY + ratio * (height - padY * 2),
          y2: padY + ratio * (height - padY * 2),
          class: "history-trend-grid",
        }),
      );
    }

    const eloValues = rows
      .map((point) => Number(point.estimated_elo))
      .filter((value) => Number.isFinite(value));
    const eloMin = eloValues.length ? Math.min(...eloValues) : 0;
    const eloMax = eloValues.length ? Math.max(...eloValues) : 0;
    const eloSpan = eloMax - eloMin || 1;

    const accuracyY = (value) => padY + (1 - Math.max(0, Math.min(100, value)) / 100) * (height - padY * 2);
    const eloY = (value) =>
      eloMax === eloMin
        ? height / 2
        : height - padY - ((value - eloMin) / eloSpan) * (height - padY * 2);

    const drawSeries = (valueAt, yAt, lineClass, dotClass) => {
      const points = [];
      rows.forEach((point, index) => {
        const value = valueAt(point);
        if (!Number.isFinite(value)) return;
        const px = x(index);
        const py = yAt(value);
        points.push(`${px.toFixed(1)},${py.toFixed(1)}`);
        const dot = svgNode("circle", { cx: px, cy: py, r: 2.8, class: `history-trend-dot ${dotClass}` });
        const title = svgNode("title");
        title.textContent = `${formatDate(point.date)} · ${point.white_name || "White"} vs ${point.black_name || "Black"} · ${Math.round(value)}`;
        dot.append(title);
        container.append(dot);
      });
      if (points.length > 1) {
        container.append(svgNode("polyline", { points: points.join(" "), class: lineClass }));
      }
    };

    drawSeries(
      (point) => Number(point.accuracy),
      accuracyY,
      "history-trend-line-accuracy",
      "history-trend-dot-accuracy",
    );
    drawSeries(
      (point) => Number(point.estimated_elo),
      eloY,
      "history-trend-line-elo",
      "history-trend-dot-elo",
    );
  }

  renderList() {
    const list = this.refs.list;
    if (!list) return;
    list.replaceChildren();
    const empty = this.games.length === 0;
    if (this.refs.empty) this.refs.empty.hidden = !empty;
    if (empty) {
      const archived = Boolean(this.data?.stats?.total);
      if (this.refs.emptyTitle) {
        this.refs.emptyTitle.textContent = archived ? "No games match" : "The archive is empty";
      }
      if (this.refs.emptyCopy) {
        this.refs.emptyCopy.textContent = archived
          ? "Try another search, or switch the mode filter back to All."
          : "Finish a game, import a PGN, or save the current line to see it here.";
      }
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const row of this.games) fragment.append(this.row(row));
    list.append(fragment);
  }

  row(row) {
    const article = node("article", "history-row");
    article.dataset.gameId = row.game_id;

    const top = node("div", "history-row-top");
    const mode = node(
      "span",
      "history-mode-badge",
      row.mode === "stockfish" && row.bot_elo
        ? `${MODE_LABEL.stockfish} ${row.bot_elo}`
        : MODE_LABEL[row.mode] || cleanLabel(row.mode) || "Game",
    );
    const time = node("span", "history-tc-pill", row.time_control || "Unlimited");
    const date = node("span", "history-row-date", formatDate(row.updated_at || row.created_at));
    const result = node(
      "span",
      `history-result-badge ${resultTone(row)}`.trim(),
      resultText(row),
    );
    top.append(mode, time, date, result);

    const title = node("p", "history-row-title");
    title.append(
      node("span", null, row.white_name || "White"),
      node("span", "vs", "vs"),
      node("span", null, row.black_name || "Black"),
    );

    const meta = node("p", "history-row-meta");
    if (row.opening_eco || row.opening_name) {
      meta.append(
        node(
          "span",
          null,
          [row.opening_eco, row.opening_name || "Opening"].filter(Boolean).join(" · "),
        ),
      );
    } else {
      meta.append(node("span", null, "Opening not recognised"));
    }
    meta.append(node("span", null, `${row.total_plies || 0} plies`));
    if (row.termination_reason) {
      meta.append(node("span", "history-reason", cleanLabel(row.termination_reason)));
    }

    article.append(top, title, meta);

    const pills = node("div", "history-review-pills");
    const white = reviewPill(
      {
        label: "W",
        accuracy: row.white_accuracy ?? null,
        elo: row.white_estimated_elo ?? null,
      },
      "is-white",
    );
    const black = reviewPill(
      {
        label: "B",
        accuracy: row.black_accuracy ?? null,
        elo: row.black_estimated_elo ?? null,
      },
      "is-black",
    );
    if (white) pills.append(white);
    if (black) pills.append(black);
    if (Number(row.brilliant_count) > 0) {
      pills.append(node("span", "history-review-pill is-brilliant", `★ ${row.brilliant_count} brilliant`));
    }
    if (row.review_signature && !white && !black) {
      pills.append(node("span", "history-review-pill", "Reviewed"));
    }
    if (pills.children.length) article.append(pills);

    const actions = node("div", "history-row-actions");
    for (const [action, label, variant] of [
      ["open", "Open", "btn-quiet"],
      ["review", "Review", "btn-quiet"],
      ["pgn", "Export PGN", "btn-quiet"],
      ["fen", "Copy FEN", "btn-quiet"],
      ...(bridgeMode() === "native" ? [] : [["share", "Copy link", "btn-quiet"]]),
      ["delete", "Delete", "btn-danger"],
    ]) {
      const button = node("button", `btn ${variant} btn-compact`, label);
      button.type = "button";
      button.dataset.historyAction = action;
      button.dataset.historyGame = row.game_id;
      actions.append(button);
    }
    article.append(actions);
    return article;
  }

  renderDb(dbInfo) {
    const info = dbInfo || this.data?.db_info || {};
    if (this.refs.dbPath) this.refs.dbPath.textContent = info.path || "state\\chess_master.db";
    if (this.refs.dbSize) this.refs.dbSize.textContent = formatBytes(info.size_bytes);
  }

  async act(action, row) {
    if (this.busy) return;
    if (action === "pgn") {
      this.exportGame(row);
      return;
    }
    if (action === "fen") {
      const copied = await copyText(row.final_fen || "");
      this.notify({
        title: copied ? "FEN copied" : "Copy unavailable",
        description: copied ? row.final_fen : "The archived position could not be copied.",
        kind: copied ? "success" : "warning",
      });
      return;
    }
    if (action === "share") {
      const url = shareGameUrl(row.game_id);
      const copied = await copyText(url);
      this.notify({
        title: copied ? "Share link copied" : "Copy unavailable",
        description: copied ? url : "Select the address bar and add the game id manually.",
        kind: copied ? "success" : "warning",
      });
      return;
    }
    if (action === "delete") {
      await this.remove(row);
      return;
    }

    this.busy = true;
    try {
      const state = await call("load_archived_game", row.game_id);
      this.onOpen(state, { review: action === "review" });
      this.notify({
        title: action === "review" ? "Archived game loaded" : "Game opened",
        description: action === "review"
          ? state.restored_review
            ? "The stored Game Review is ready."
            : "Starting a fresh Game Review."
          : `${row.white_name || "White"} vs ${row.black_name || "Black"}.`,
        kind: "success",
      });
      this.refreshCount();
    } catch (error) {
      this.notify({ title: "Could not open the archive", description: errorMessage(error), kind: "error" });
    } finally {
      this.busy = false;
    }
  }

  exportGame(row) {
    if (!row.pgn) {
      this.notify({ title: "No PGN stored", description: "This archive row has no exported line.", kind: "warning" });
      return;
    }
    downloadText(pgnName(row), row.pgn);
    this.notify({ title: "Game exported", description: pgnName(row), kind: "success" });
  }

  async remove(row) {
    const confirmed = await askConfirm(this.refs.confirm, {
      eyebrow: "Archive",
      title: "Delete this archived game?",
      copy: `${row.white_name || "White"} vs ${row.black_name || "Black"} and its saved review will be permanently removed from the local database.`,
      acceptLabel: "Delete game",
    });
    if (!confirmed) return;
    this.busy = true;
    try {
      const result = await call("delete_archived_game", row.game_id);
      this.notify({
        title: result.deleted ? "Game deleted" : "Already gone",
        description: result.deleted ? "The archive was updated." : "That game was not in the archive.",
        kind: result.deleted ? "success" : "info",
      });
      await this.refresh({ silent: true });
    } catch (error) {
      this.notify({ title: "Could not delete the game", description: errorMessage(error), kind: "error" });
    } finally {
      this.busy = false;
    }
  }

  async copyPath() {
    const info = this.data?.db_info;
    const path = info?.path;
    if (!path) return;
    const copied = await copyText(path);
    this.notify({
      title: copied ? "Database path copied" : "Copy unavailable",
      description: copied ? path : "Select the path above and copy it manually.",
      kind: copied ? "success" : "warning",
    });
  }

  async exportAll() {
    if (this.busy) return;
    this.busy = true;
    try {
      const result = await call("export_archive_pgn");
      if (!result.pgn || !result.count) {
        this.notify({ title: "Nothing to export", description: "The archive has no saved PGNs yet.", kind: "info" });
        return;
      }
      downloadText(result.filename, result.pgn);
      this.notify({
        title: "Archive exported",
        description: `${result.count} ${result.count === 1 ? "game" : "games"} · ${result.filename}`,
        kind: "success",
      });
    } catch (error) {
      this.notify({ title: "Could not export the archive", description: errorMessage(error), kind: "error" });
    } finally {
      this.busy = false;
    }
  }

  async reset() {
    if (this.busy) return;
    const total = Number(this.data?.stats?.total) || 0;
    if (!total) {
      this.notify({ title: "Archive already empty", description: "There is nothing to reset.", kind: "info" });
      return;
    }
    const confirmed = await askConfirm(this.refs.confirm, {
      eyebrow: "Reset archive",
      title: "Delete every saved game?",
      copy: `${total} ${total === 1 ? "game" : "games"} and every saved review report will be permanently deleted from SQLite. The live table is not affected.`,
      acceptLabel: "Reset archive",
    });
    if (!confirmed) return;
    this.busy = true;
    try {
      const result = await call("clear_game_archive");
      this.notify({
        title: "Archive reset",
        description: `${result.deleted || 0} ${result.deleted === 1 ? "game" : "games"} removed.`,
        kind: "success",
      });
      await this.refresh({ silent: true });
    } catch (error) {
      this.notify({ title: "Could not reset the archive", description: errorMessage(error), kind: "error" });
    } finally {
      this.busy = false;
    }
  }
}
