/** Chessground adapter. Rendering is server-authoritative; the board is only
 * an optimistic input surface. */

import { Chessground } from "../vendor/chessground/chessground.min.js";
import { isPromotion, legalDests } from "./state.js";

export class BoardController {
  constructor(element, { onMove, onPromotion, loadingElement } = {}) {
    this.element = element;
    this.onMove = onMove || (() => {});
    this.onPromotion = onPromotion || (() => {});
    this.loadingElement = loadingElement || null;
    this.game = null;
    this.orientation = "white";
    this.pendingPromotion = null;
    this.autoShapes = [];
    this.preview = null;
    this.sandbox = null;
    this.reviewOverlay = null;
    this.arrowsFrame = null;

    this.ground = Chessground(element, {
      fen: "8/8/8/8/8/8/8/8 w - - 0 1",
      orientation: "white",
      coordinates: true,
      viewOnly: false,
      animation: { enabled: true, duration: 190 },
      highlight: { lastMove: true, check: true },
      movable: {
        free: false,
        color: undefined,
        showDests: true,
        dests: new Map(),
        events: {
          after: (from, to) => this.handleDrop(from, to),
        },
      },
      premovable: { enabled: false },
      predroppable: { enabled: false },
      draggable: { enabled: true, showGhost: true, deleteOnDropOff: false },
      selectable: { enabled: true },
      drawable: {
        enabled: true,
        visible: true,
        defaultSnapToValidMove: true,
        eraseOnClick: true,
        brushes: {
          green: { key: "g", color: "#5fd7a4", opacity: 0.8, lineWidth: 10 },
          engine: { key: "e", color: "#4fe0b2", opacity: 0.9, lineWidth: 11 },
          brilliant: { key: "b", color: "#1baca6", opacity: 0.94, lineWidth: 11 },
          great: { key: "r", color: "#3b82f6", opacity: 0.92, lineWidth: 10 },
          best: { key: "s", color: "#22c55e", opacity: 0.9, lineWidth: 10 },
          excellent: { key: "x", color: "#96bc4b", opacity: 0.88, lineWidth: 9 },
          good: { key: "o", color: "#97af8b", opacity: 0.82, lineWidth: 8 },
          book: { key: "k", color: "#d97706", opacity: 0.74, lineWidth: 8 },
          inaccuracy: { key: "i", color: "#eab308", opacity: 0.9, lineWidth: 9 },
          mistake: { key: "m", color: "#f97316", opacity: 0.92, lineWidth: 10 },
          miss: { key: "n", color: "#f43f5e", opacity: 0.94, lineWidth: 10 },
          blunder: { key: "l", color: "#ef4444", opacity: 0.96, lineWidth: 11 },
          alternative: { key: "a", color: "#34d399", opacity: 0.9, lineWidth: 8 },
          pv: { key: "p", color: "#5fd7a4", opacity: 0.92, lineWidth: 10 },
          retry: { key: "t", color: "#f59e0b", opacity: 0.45, lineWidth: 6 },
        },
      },
    });

    this.overlay = document.createElement("div");
    this.overlay.className = "board-review-overlay";
    this.overlay.setAttribute("aria-hidden", "true");
    this.element.append(this.overlay);

    const handleResize = () => {
      if (!this.ground) return;
      this.ground.redrawAll();
      if (this.reviewOverlay) this.setReviewOverlay(this.reviewOverlay);
      if (this.autoShapes.length) this.queueArrows();
    };

    if ("ResizeObserver" in window) {
      this.resizeObserver = new ResizeObserver(handleResize);
      this.resizeObserver.observe(this.element);
    }
    window.addEventListener("resize", handleResize);
  }

  render(game, { orientation = "white", canPlay = true, preview = null, sandbox = null } = {}) {
    this.game = game;
    this.orientation = orientation;
    this.canPlay = canPlay;
    this.preview = preview && preview.active ? preview : null;
    this.sandbox = sandbox || null;
    const previewFen = this.preview?.fen || null;
    const sandboxFen = this.sandbox?.fen || null;
    const playable = sandboxFen
      ? Boolean(this.sandbox.playable)
      : Boolean(
          game &&
            !this.preview &&
            game.status.state === "in_progress" &&
            canPlay &&
            !this.pendingPromotion,
        );
    const fen = sandboxFen || previewFen || game.fen;
    const turn = fen.split(" ")[1] === "b" ? "black" : "white";
    const dests = playable ? legalDests(fen) : new Map();
    this.element.dataset.playable = String(playable);
    this.element.dataset.dests = String(dests.size);
    this.element.dataset.turn = turn;
    this.element.dataset.preview = String(Boolean(this.preview));
    this.element.classList.toggle("manipulable", playable);
    const lastMove = this.sandbox?.lastMove
      ? [this.sandbox.lastMove.from, this.sandbox.lastMove.to]
      : this.preview?.lastMove
        ? [this.preview.lastMove.from, this.preview.lastMove.to]
        : game && game.last_move
          ? [game.last_move.from, game.last_move.to]
          : undefined;
    const check = this.sandbox ? false : this.preview ? false : Boolean(game && game.status.check);

    this.ground.set({
      fen,
      orientation,
      turnColor: turn,
      viewOnly: !playable,
      check,
      lastMove,
      animation: { enabled: true, duration: 190 },
      movable: {
        free: false,
        color: playable ? turn : undefined,
        showDests: true,
        dests,
      },
      drawable: {
        shapes: [],
        autoShapes: [],
      },
    });

    if (this.loadingElement && this.loadingElement.hidden === false) {
      this.loadingElement.hidden = true;
    }
    if (this.autoShapes.length) this.queueArrows();
  }

  handleDrop(from, to) {
    if (!this.game || this.pendingPromotion) return;

    const fen = this.sandbox?.fen || this.game.fen;
    const color = this.sandbox?.color || this.game.turn;
    if (isPromotion(fen, from, to)) {
      this.pendingPromotion = { from, to, color };
      this.lock();
      this.onPromotion({ from, to, color });
      return;
    }

    this.lock();
    this.onMove(`${from}${to}`);
  }

  /** Freeze input while a request is in flight or a dialog is open. */
  lock() {
    this.element.classList.remove("manipulable");
    this.ground.set({
      viewOnly: true,
      movable: { color: undefined, dests: new Map() },
    });
  }

  cancelPromotion() {
    this.pendingPromotion = null;
    if (this.game) {
      this.render(this.game, {
        orientation: this.orientation,
        canPlay: this.canPlay,
        preview: this.preview,
        sandbox: this.sandbox,
      });
    }
  }

  clearPromotion() {
    this.pendingPromotion = null;
  }

  setEngineArrow(move) {
    if (!move || !move.from || !move.to) {
      this.clearArrows();
      return;
    }
    this.setArrows([{ orig: move.from, dest: move.to, brush: "engine" }]);
  }

  setArrows(shapes) {
    this.autoShapes = Array.isArray(shapes) ? shapes : [];
    this.arrowAttempts = 0;
    this.queueArrows();
  }

  queueArrows() {
    if (this.arrowsFrame) window.cancelAnimationFrame(this.arrowsFrame);
    this.arrowsFrame = window.requestAnimationFrame(() => {
      this.arrowsFrame = null;
      this.drawArrows();
    });
  }

  drawArrows() {
    if (!this.ground) return;
    const boardElement = this.element.querySelector("cg-board");
    const rect = boardElement?.getBoundingClientRect();
    if (!rect || !Number.isFinite(rect.width) || !Number.isFinite(rect.height) || rect.width < 8) {
      // The first render can happen before CSS has sized the board. Wait for
      // layout instead of letting Chessground compute NaN arrow coordinates.
      this.arrowAttempts = (this.arrowAttempts || 0) + 1;
      if (this.arrowAttempts < 120) this.queueArrows();
      return;
    }
    // Clear first: Chessground skips a redraw when the shape hash is unchanged,
    // which would keep a previously mis-measured arrow on screen.
    this.ground.setAutoShapes([]);
    this.ground.setAutoShapes(this.autoShapes);
  }

  clearArrows() {
    this.setArrows([]);
  }

  /** Square under a viewport point, for the board editor. */
  squareAt(clientX, clientY) {
    const boardElement = this.element.querySelector("cg-board");
    if (!boardElement) return null;
    const rect = boardElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const col = Math.floor(((clientX - rect.left) / rect.width) * 8);
    const row = Math.floor(((clientY - rect.top) / rect.height) * 8);
    if (col < 0 || col > 7 || row < 0 || row > 7) return null;
    const file = this.orientation === "black" ? 7 - col : col;
    const rank = this.orientation === "black" ? row : 7 - row;
    return `${"abcdefgh"[file]}${rank + 1}`;
  }

  squareBox(square) {
    const boardElement = this.element.querySelector("cg-board") || this.element;
    const boardRect = boardElement.getBoundingClientRect();
    const holderRect = this.element.getBoundingClientRect();
    if (!boardRect.width || !square || square.length < 2) return null;
    const file = square.charCodeAt(0) - 97;
    const rank = 8 - Number(square.slice(1));
    if (!Number.isFinite(file) || !Number.isFinite(rank) || file < 0 || file > 7 || rank < 0 || rank > 7) {
      return null;
    }
    const size = boardRect.width / 8;
    const col = this.orientation === "black" ? 7 - file : file;
    const row = this.orientation === "black" ? 7 - rank : rank;
    return {
      size,
      left: boardRect.left - holderRect.left + col * size,
      top: boardRect.top - holderRect.top + row * size,
    };
  }

  setReviewOverlay(scene) {
    if (!this.overlay) return;
    this.reviewOverlay = scene || null;
    this.overlay.replaceChildren();
    if (!scene) return;

    if (scene.tint) {
      const box = this.squareBox(scene.tint.square);
      if (box) {
        const tint = document.createElement("span");
        tint.className = `review-square-tint review-tint-${scene.tint.classification || "best"}`;
        tint.style.left = `${box.left + 1}px`;
        tint.style.top = `${box.top + 1}px`;
        tint.style.width = `${box.size - 2}px`;
        tint.style.height = `${box.size - 2}px`;
        this.overlay.append(tint);
      }
    }

    if (scene.ring) {
      const box = this.squareBox(scene.ring.square);
      if (box) {
        const size = Math.max(20, box.size * 0.72);
        const ring = document.createElement("span");
        ring.className = "review-hint-ring";
        ring.style.left = `${box.left + box.size / 2 - size / 2}px`;
        ring.style.top = `${box.top + box.size / 2 - size / 2}px`;
        ring.style.width = `${size}px`;
        ring.style.height = `${size}px`;
        this.overlay.append(ring);
      }
    }

    if (scene.badge) {
      const box = this.squareBox(scene.badge.square);
      if (box) {
        const size = Math.max(17, Math.min(30, Math.round(box.size * 0.4)));
        const badge = document.createElement("span");
        const classification = scene.badge.classification || "best";
        badge.className = `review-badge review-badge-${classification}`;
        badge.style.setProperty("--badge-size", `${size}px`);
        badge.style.left = `${box.left + box.size - size * 0.28}px`;
        badge.style.top = `${box.top + size * 0.28}px`;
        badge.title = `${scene.badge.label || "Move"}${scene.badge.san ? ` — ${scene.badge.san}` : ""}`;
        if (classification === "book") {
          badge.innerHTML =
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5Z"/><path d="M4 5.5v15A2.5 2.5 0 0 0 6.5 23H20"/></svg>';
        } else {
          badge.textContent = scene.badge.symbol || "•";
        }
        this.overlay.append(badge);
      }
    }
  }

  clearReviewOverlay() {
    this.setReviewOverlay(null);
  }

  redraw() {
    if (this.game) {
      this.render(this.game, {
        orientation: this.orientation,
        canPlay: this.canPlay,
        preview: this.preview,
        sandbox: this.sandbox,
      });
    }
  }
}
