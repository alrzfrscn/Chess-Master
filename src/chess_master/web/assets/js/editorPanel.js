/** Board editor and endgame drill picker for the Import workspace. */

import { Chess, validateFen } from "../vendor/chess.js/chess.js";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const KINGS_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1";
const WHITE = [
  ["K", "White king"],
  ["Q", "White queen"],
  ["R", "White rook"],
  ["B", "White bishop"],
  ["N", "White knight"],
  ["P", "White pawn"],
];
const BLACK = [
  ["k", "Black king"],
  ["q", "Black queen"],
  ["r", "Black rook"],
  ["b", "Black bishop"],
  ["n", "Black knight"],
  ["p", "Black pawn"],
];
const HOME = {
  K: ["e1", "h1", "K", "R"],
  Q: ["e1", "a1", "K", "R"],
  k: ["e8", "h8", "k", "r"],
  q: ["e8", "a8", "k", "r"],
};

const STATUS_ICONS = { legal: "#i-check-circle", error: "#i-alert" };

function isWhitePiece(piece) {
  return Boolean(piece) && piece === piece.toUpperCase();
}

function sameSide(a, b) {
  return isWhitePiece(a) === isWhitePiece(b);
}

/** Parse the board, side and castling fields of a FEN into editable state. */
function parseFen(fen) {
  const [placement, turn = "w", castling = "-"] = fen.split(" ");
  const pieces = {};
  const rows = placement.split("/");
  for (let row = 0; row < 8; row += 1) {
    let file = 0;
    for (const char of rows[row] || "") {
      if (/\d/.test(char)) {
        file += Number(char);
      } else {
        if (file < 8) pieces[`${"abcdefgh"[file]}${8 - row}`] = char;
        file += 1;
      }
    }
  }
  return {
    pieces,
    side: turn === "b" ? "black" : "white",
    castling: new Set(castling === "-" ? [] : [...castling]),
  };
}

export class EditorPanel {
  constructor({ onChange, onLaunch, onFlip, onStartDrill } = {}) {
    this.onChange = onChange || (() => {});
    this.onLaunch = onLaunch || (() => {});
    this.onFlip = onFlip || (() => {});
    this.onStartDrill = onStartDrill || (() => {});
    this.active = false;
    this.tool = "move";
    this.moveFrom = null;
    this.drills = [];

    this.card = document.getElementById("editor-card");
    this.body = document.getElementById("editor-body");
    this.toggle = document.getElementById("btn-editor-toggle");
    this.toggleLabel = document.getElementById("btn-editor-toggle-label");
    this.palette = document.getElementById("editor-palette");
    this.fenInput = document.getElementById("editor-fen");
    this.status = document.getElementById("editor-status");
    this.statusText = document.getElementById("editor-status-text");
    this.statusIcon = document.getElementById("editor-status-icon");
    this.drillList = document.getElementById("drill-list");
    this.launchStockfish = document.getElementById("btn-editor-stockfish");
    this.launchPassPlay = document.getElementById("btn-editor-passplay");

    this.loadFen(START_FEN);
    this.buildPalette();
    this.wire();
    this.sync();
  }

  /* --------------------------------------------------------------------
     Setup
     -------------------------------------------------------------------- */

  buildPalette() {
    if (!this.palette) return;
    const fragment = document.createDocumentFragment();
    for (const [piece, label] of [...WHITE, ...BLACK]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `editor-piece${isWhitePiece(piece) ? "" : " is-dark"}`;
      button.dataset.piece = piece;
      button.title = label;
      button.setAttribute("aria-label", label);
      button.setAttribute("aria-pressed", "false");
      const img = document.createElement("img");
      img.src = `assets/pieces/${isWhitePiece(piece) ? "w" : "b"}${piece.toUpperCase()}.svg`;
      img.alt = "";
      button.append(img);
      fragment.append(button);
    }
    this.palette.replaceChildren(fragment);
  }

  wire() {
    this.toggle?.addEventListener("click", () => this.setActive(!this.active));

    this.palette?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-piece]");
      if (!button) return;
      this.setTool(button.dataset.piece);
    });

    for (const button of document.querySelectorAll("[data-tool]")) {
      button.addEventListener("click", () => this.setTool(button.dataset.tool));
    }

    for (const button of document.querySelectorAll("[data-preset]")) {
      button.addEventListener("click", () => {
        if (button.dataset.preset === "start") this.loadFen(START_FEN);
        else if (button.dataset.preset === "kings") this.loadFen(KINGS_FEN);
        else {
          this.onFlip();
          return;
        }
        this.afterEdit();
      });
    }

    for (const button of document.querySelectorAll("[data-side]")) {
      button.addEventListener("click", () => {
        if (this.side === button.dataset.side) return;
        this.side = button.dataset.side;
        this.afterEdit();
      });
    }

    for (const input of document.querySelectorAll("[data-castle]")) {
      input.addEventListener("change", () => {
        if (input.checked) this.castling.add(input.dataset.castle);
        else this.castling.delete(input.dataset.castle);
        this.afterEdit();
      });
    }

    this.launchStockfish?.addEventListener("click", () => this.launch("stockfish"));
    this.launchPassPlay?.addEventListener("click", () => this.launch("pass_play"));
  }

  /* --------------------------------------------------------------------
     Editor state
     -------------------------------------------------------------------- */

  isActive() {
    return this.active;
  }

  setActive(active) {
    const next = Boolean(active);
    if (next === this.active) return;
    this.active = next;
    if (this.body) this.body.hidden = !next;
    if (this.card) this.card.classList.toggle("is-active", next);
    this.toggle?.setAttribute("aria-pressed", String(next));
    if (this.toggleLabel) this.toggleLabel.textContent = next ? "Close editor" : "Open editor";
    this.moveFrom = null;
    this.sync();
  }

  deactivate() {
    this.setActive(false);
  }

  /** Board override consumed by app.js while the editor is open. */
  boardState() {
    if (!this.active) return null;
    return { fen: this.fen(), playable: false };
  }

  loadFen(fen) {
    const parsed = parseFen(fen);
    this.pieces = parsed.pieces;
    this.side = parsed.side;
    this.castling = parsed.castling;
    this.moveFrom = null;
  }

  fen() {
    const rows = [];
    for (let rank = 8; rank >= 1; rank -= 1) {
      let row = "";
      let empty = 0;
      for (const file of "abcdefgh") {
        const piece = this.pieces[`${file}${rank}`];
        if (piece) {
          if (empty) {
            row += String(empty);
            empty = 0;
          }
          row += piece;
        } else {
          empty += 1;
        }
      }
      if (empty) row += String(empty);
      rows.push(row);
    }
    const rights = ["K", "Q", "k", "q"].filter((right) => this.castling.has(right)).join("");
    return `${rows.join("/")} ${this.side === "black" ? "b" : "w"} ${rights || "-"} - 0 1`;
  }

  setTool(tool) {
    this.tool = tool;
    this.moveFrom = null;
    for (const button of document.querySelectorAll("[data-piece]")) {
      button.setAttribute("aria-pressed", String(button.dataset.piece === tool));
    }
    for (const button of document.querySelectorAll("[data-tool]")) {
      button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
    }
    this.sync();
  }

  /** Click routing from the board while the editor is open. */
  handleSquare(square) {
    if (!this.active || !square) return;

    if (this.tool === "erase") {
      delete this.pieces[square];
      this.afterEdit();
      return;
    }

    if (this.tool === "move") {
      if (!this.moveFrom) {
        if (this.pieces[square]) {
          this.moveFrom = square;
          this.sync();
        }
        return;
      }
      if (this.moveFrom === square) {
        this.moveFrom = null;
        this.sync();
        return;
      }
      const moving = this.pieces[this.moveFrom];
      const target = this.pieces[square];
      if (target && sameSide(moving, target)) {
        this.moveFrom = square;
        this.sync();
        return;
      }
      delete this.pieces[this.moveFrom];
      this.pieces[square] = moving;
      this.moveFrom = null;
      this.afterEdit();
      return;
    }

    this.placePiece(square, this.tool);
    this.afterEdit();
  }

  placePiece(square, piece) {
    // One king per side: placing a king replaces the old one instead of
    // leaving a position the validator can only reject.
    if (piece === "K" || piece === "k") {
      for (const [key, value] of Object.entries(this.pieces)) {
        if (value === piece) delete this.pieces[key];
      }
    }
    this.pieces[square] = piece;
  }

  afterEdit() {
    this.pruneCastling();
    this.sync();
  }

  /** Drop rights whose king or rook is no longer on its home square. */
  pruneCastling() {
    for (const [right, [kingSquare, rookSquare, king, rook]] of Object.entries(HOME)) {
      if (
        this.castling.has(right) &&
        (this.pieces[kingSquare] !== king || this.pieces[rookSquare] !== rook)
      ) {
        this.castling.delete(right);
      }
    }
  }

  validate() {
    const fen = this.fen();
    const result = validateFen(fen);
    if (!result.ok) {
      const message = result.error.replace(/^Invalid FEN:\s*/, "");
      if (/king/i.test(message)) return { ok: false, message: "A legal position needs exactly one king for each side." };
      if (/pawn/i.test(message)) return { ok: false, message: "Pawns cannot stand on the first or eighth rank." };
      return { ok: false, message };
    }

    let whitePieces = 0;
    let blackPieces = 0;
    let whitePawns = 0;
    let blackPawns = 0;
    for (const piece of Object.values(this.pieces)) {
      if (isWhitePiece(piece)) {
        whitePieces += 1;
        if (piece === "P") whitePawns += 1;
      } else {
        blackPieces += 1;
        if (piece === "p") blackPawns += 1;
      }
    }
    if (whitePawns > 8 || blackPawns > 8) {
      return { ok: false, message: "A side cannot have more than eight pawns." };
    }
    if (whitePieces > 16 || blackPieces > 16) {
      return { ok: false, message: "A side cannot have more than sixteen pieces." };
    }

    for (const [right, [kingSquare, rookSquare, king, rook]] of Object.entries(HOME)) {
      if (
        this.castling.has(right) &&
        (this.pieces[kingSquare] !== king || this.pieces[rookSquare] !== rook)
      ) {
        return {
          ok: false,
          message: "Castling rights do not match the king and rook placement.",
        };
      }
    }

    // The side that is not to move may not be in check (this also catches
    // adjacent kings). Flip the turn and ask chess.js directly.
    const fields = fen.split(" ");
    fields[1] = fields[1] === "w" ? "b" : "w";
    try {
      if (new Chess(fields.join(" ")).isCheck()) {
        return { ok: false, message: "The side that is not to move is in check." };
      }
    } catch {
      return { ok: false, message: "That position could not be read. Check the piece placement." };
    }

    return { ok: true, message: "Legal position", fen };
  }

  sync() {
    const fen = this.fen();
    const check = this.validate();
    if (this.fenInput) this.fenInput.value = fen;

    if (this.status && this.statusText) {
      this.status.classList.toggle("is-legal", check.ok);
      this.status.classList.toggle("is-error", !check.ok);
      this.statusText.textContent =
        check.ok && this.moveFrom
          ? `Selected ${this.moveFrom} — click a destination square.`
          : check.message;
      if (this.statusIcon) {
        this.statusIcon.innerHTML = `<use href="${check.ok ? STATUS_ICONS.legal : STATUS_ICONS.error}" />`;
      }
    }

    for (const button of document.querySelectorAll("[data-side]")) {
      button.setAttribute("aria-pressed", String(button.dataset.side === this.side));
    }
    for (const input of document.querySelectorAll("[data-castle]")) {
      input.checked = this.castling.has(input.dataset.castle);
    }
    if (this.launchStockfish) this.launchStockfish.disabled = !check.ok;
    if (this.launchPassPlay) this.launchPassPlay.disabled = !check.ok;

    this.onChange();
  }

  launch(mode) {
    const check = this.validate();
    if (!check.ok) return;
    this.onLaunch({ mode, fen: check.fen, playerColor: this.side });
  }

  /* --------------------------------------------------------------------
     Drill picker
     -------------------------------------------------------------------- */

  setDrills(drills) {
    this.drills = Array.isArray(drills) ? drills : [];
    if (!this.drillList) return;
    const fragment = document.createDocumentFragment();
    for (const drill of this.drills) {
      const card = document.createElement("article");
      card.className = "drill-card";
      card.title = drill.hint || "";

      const head = document.createElement("div");
      head.className = "drill-head";
      head.append(this.badge(drill.category, "info"));
      head.append(this.badge(drill.difficulty, drill.difficulty === "Beginner" ? "success" : "warning"));

      const title = document.createElement("h3");
      title.textContent = drill.title;

      const goal = document.createElement("p");
      goal.className = "drill-goal";
      goal.textContent = drill.goal;

      const foot = document.createElement("div");
      foot.className = "drill-foot";
      const meta = document.createElement("span");
      meta.className = "drill-meta";
      meta.textContent = `${drill.player_color === "white" ? "White" : "Black"} · ≈${drill.recommended_elo} Elo`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-quiet btn-compact";
      button.dataset.drill = drill.id;
      button.textContent = "Practice vs Stockfish";
      button.addEventListener("click", () => this.onStartDrill(drill.id));
      foot.append(meta, button);

      card.append(head, title, goal, foot);
      fragment.append(card);
    }
    this.drillList.replaceChildren(fragment);
  }

  badge(label, tone) {
    const badge = document.createElement("span");
    badge.className = "drill-badge";
    badge.dataset.tone = tone;
    badge.textContent = label;
    return badge;
  }
}
