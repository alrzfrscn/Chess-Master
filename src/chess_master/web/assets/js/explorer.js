/** Opening Explorer: book continuations for the viewed ply plus an ECO browser.
 * Chess facts come from the bundled trie through the bridge; this module only
 * renders them and turns a click into a board move or a study line. */

import { call } from "./api.js";
import { Chess } from "../vendor/chess.js/chess.js";
import { canHumanMove } from "./state.js";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

export class OpeningExplorer {
  constructor({ onPlay, onStudyLine, onHoverArrow, onClearArrow } = {}) {
    this.onPlay = onPlay || (() => {});
    this.onStudyLine = onStudyLine || (() => {});
    this.onHoverArrow = onHoverArrow || (() => {});
    this.onClearArrow = onClearArrow || (() => {});

    this.game = null;
    this.key = "";
    this.token = 0;
    this.data = null;
    this.searchToken = 0;

    this.refs = {
      card: document.getElementById("opening-card"),
      toggle: document.getElementById("opening-toggle"),
      toggleSub: document.getElementById("opening-toggle-sub"),
      body: document.getElementById("opening-body"),
      eco: document.getElementById("opening-eco"),
      name: document.getElementById("opening-name"),
      variation: document.getElementById("opening-variation"),
      continuations: document.getElementById("opening-continuations"),
      empty: document.getElementById("opening-empty"),
      form: document.getElementById("opening-search-form"),
      input: document.getElementById("opening-search-input"),
      results: document.getElementById("opening-results"),
    };

    this.wire();
  }

  wire() {
    this.refs.toggle?.addEventListener("click", () => {
      const expanded = this.refs.toggle.getAttribute("aria-expanded") !== "false";
      this.refs.toggle.setAttribute("aria-expanded", String(!expanded));
      if (this.refs.body) this.refs.body.hidden = expanded;
    });

    this.refs.continuations?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-uci]");
      if (!button) return;
      const item = (this.data?.continuations || []).find((move) => move.uci === button.dataset.uci);
      this.clearArrow();
      if (item) this.choose(item);
    });
    this.refs.continuations?.addEventListener("pointerover", (event) => {
      const button = event.target.closest("[data-from]");
      if (!button) return;
      if (event.relatedTarget && button.contains(event.relatedTarget)) return;
      this.onHoverArrow({ orig: button.dataset.from, dest: button.dataset.to });
    });
    this.refs.continuations?.addEventListener("pointerout", (event) => {
      const button = event.target.closest("[data-from]");
      if (!button) return;
      if (event.relatedTarget && button.contains(event.relatedTarget)) return;
      this.clearArrow();
    });
    this.refs.continuations?.addEventListener("focusin", (event) => {
      const button = event.target.closest("[data-from]");
      if (button) this.onHoverArrow({ orig: button.dataset.from, dest: button.dataset.to });
    });
    this.refs.continuations?.addEventListener("focusout", () => this.clearArrow());

    this.refs.form?.addEventListener("submit", (event) => {
      event.preventDefault();
      this.search();
    });
    this.refs.results?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-line]");
      if (!button) return;
      this.study(button.dataset.line.split(" ").filter(Boolean), button.dataset.label);
    });
  }

  clearArrow() {
    this.onClearArrow();
  }

  setGame(game) {
    this.game = game;
    const key = game ? `${game.game_id}:${game.ply}` : "";
    if (key === this.key) return;
    this.key = key;
    this.refresh();
  }

  async refresh() {
    const token = ++this.token;
    try {
      const data = await call("get_opening_explorer");
      if (token !== this.token) return;
      this.data = data || null;
      this.render();
    } catch (error) {
      console.warn("Opening explorer unavailable", error);
    }
  }

  /** Moves seen up to the viewed ply; the prefix a continuation extends. */
  prefix() {
    const moves = this.game?.moves || [];
    return moves.slice(0, this.game?.ply ?? 0).map((move) => move.uci);
  }

  choose(item) {
    const game = this.game;
    const live = Boolean(game && game.is_viewing_live && canHumanMove(game));
    if (live) {
      this.onPlay(item.uci);
      return;
    }
    this.study(this.prefix().concat(item.uci), item.full_name);
  }

  /** Load a UCI line on the table as a study game. */
  study(uciLine, label = "") {
    const pgn = this.buildPgn(uciLine);
    if (!pgn) return;
    this.onStudyLine(pgn, label);
  }

  buildPgn(uciLine) {
    try {
      const startFen = this.game?.start_fen || START_FEN;
      const chess = new Chess(startFen);
      if (startFen !== START_FEN) {
        chess.setHeader("SetUp", "1");
        chess.setHeader("FEN", startFen);
      }
      for (const uci of uciLine) {
        const text = String(uci);
        const move = chess.move({
          from: text.slice(0, 2),
          to: text.slice(2, 4),
          promotion: text.slice(4, 5) || undefined,
        });
        if (!move) return null;
      }
      return chess.pgn();
    } catch {
      return null;
    }
  }

  async search() {
    const query = (this.refs.input?.value || "").trim();
    if (!query) {
      if (this.refs.results) {
        this.refs.results.hidden = true;
        this.refs.results.replaceChildren();
      }
      return;
    }
    const token = ++this.searchToken;
    try {
      const data = await call("search_openings", query, "", 30);
      if (token !== this.searchToken) return;
      this.renderResults(data?.openings || []);
    } catch (error) {
      console.warn("Opening search failed", error);
    }
  }

  render() {
    const data = this.data || {};
    const current = data.current || {};
    const name = current.full_name || current.name || "Starting position";
    if (this.refs.eco) this.refs.eco.textContent = current.eco || "—";
    if (this.refs.name) this.refs.name.textContent = name;
    if (this.refs.variation) {
      this.refs.variation.textContent = current.variation
        ? current.variation
        : current.last_book_ply
          ? `${current.last_book_ply} book plies deep`
          : data.transposed
            ? "Transposed into book"
            : "Play a book move or browse the ECO dictionary.";
    }
    if (this.refs.toggleSub) {
      this.refs.toggleSub.textContent = current.eco ? `${current.eco} · ${name}` : name;
    }

    const continuations = Array.isArray(data.continuations) ? data.continuations : [];
    const container = this.refs.continuations;
    if (container) {
      const fragment = document.createDocumentFragment();
      for (const item of continuations) {
        const button = element("button", "opening-move");
        button.type = "button";
        button.dataset.uci = item.uci;
        button.dataset.from = item.from;
        button.dataset.to = item.to;
        button.title = `${item.san} — ${item.full_name || item.name} · ${item.line_count} lines (${item.share_pct}%)`;

        const san = element("span", "opening-move-san", item.san);
        const body = element("span", "opening-move-body");
        const line = element("span", "opening-move-line");
        line.append(
          element("span", "opening-move-name", item.full_name || item.name || item.san),
          element("span", "opening-move-count", String(item.line_count)),
        );
        const bar = element("span", "opening-move-bar");
        const fill = document.createElement("i");
        fill.style.width = `${Math.max(2, Math.min(100, Number(item.share_pct) || 0))}%`;
        bar.append(fill);
        body.append(line, bar);
        button.append(san, body, element("span", "opening-move-pct", `${item.share_pct}%`));
        fragment.append(button);
      }
      container.replaceChildren(fragment);
      container.hidden = continuations.length === 0;
    }
    if (this.refs.empty) this.refs.empty.hidden = continuations.length > 0;
  }

  renderResults(openings) {
    const container = this.refs.results;
    if (!container) return;
    const fragment = document.createDocumentFragment();
    for (const opening of openings) {
      const button = element("button", "opening-result");
      button.type = "button";
      button.dataset.line = opening.uci_line || "";
      button.dataset.label = opening.full_name || opening.name || "";
      button.title = `${opening.full_name} · ${opening.ply_count} plies`;
      button.append(
        element("span", "opening-result-eco", opening.eco || "—"),
        element("span", "opening-result-name", opening.full_name || opening.name),
        element("span", "opening-result-plies", `${opening.ply_count}p`),
      );
      fragment.append(button);
    }
    if (!openings.length) {
      fragment.append(element("p", "opening-empty", "No opening matched that search."));
    }
    container.replaceChildren(fragment);
    container.hidden = false;
  }
}
