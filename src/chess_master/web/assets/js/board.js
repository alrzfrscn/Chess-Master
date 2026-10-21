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
        },
      },
    });
  }

  render(game, { orientation = "white", canPlay = true } = {}) {
    this.game = game;
    this.orientation = orientation;
    this.canPlay = canPlay;
    const playable = Boolean(
      game && game.status.state === "in_progress" && canPlay && !this.pendingPromotion,
    );
    const dests = playable ? legalDests(game.fen) : new Map();
    this.element.dataset.playable = String(playable);
    this.element.dataset.dests = String(dests.size);
    this.element.dataset.turn = game.turn;
    this.element.classList.toggle("manipulable", playable);
    const lastMove = game && game.last_move ? [game.last_move.from, game.last_move.to] : undefined;
    const check = Boolean(game && game.status.check);

    this.ground.set({
      fen: game.fen,
      orientation,
      turnColor: game.turn,
      viewOnly: !playable,
      check,
      lastMove,
      animation: { enabled: true, duration: 190 },
      movable: {
        free: false,
        color: playable ? game.turn : undefined,
        showDests: true,
        dests,
      },
      drawable: {
        shapes: [],
        autoShapes: this.autoShapes,
      },
    });

    if (this.loadingElement && this.loadingElement.hidden === false) {
      this.loadingElement.hidden = true;
    }
  }

  handleDrop(from, to) {
    if (!this.game || this.pendingPromotion) return;

    if (isPromotion(this.game.fen, from, to)) {
      const color = this.game.turn;
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
    if (this.game) this.render(this.game, { orientation: this.orientation, canPlay: this.canPlay });
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
    this.ground.setAutoShapes(this.autoShapes);
  }

  clearArrows() {
    this.setArrows([]);
  }

  redraw() {
    if (this.game) this.render(this.game, { orientation: this.orientation, canPlay: this.canPlay });
  }
}
