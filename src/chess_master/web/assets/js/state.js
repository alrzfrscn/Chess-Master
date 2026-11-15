/** Shared UI state and small pure helpers. */

import { Chess } from "../vendor/chess.js/chess.js";

export const app = {
  game: null,
  apiInfo: null,
  samples: [],
  orientation: "white",
  evalVisible: true,
  arrowsEnabled: false,
  evaluation: null,
  engine: null,
  activeTab: "moves",
  preview: null,
  bridgeMode: null,
  busy: false,
  navigating: false,
  pendingPromotion: null,
  lastMoveWasBranch: false,
  pollBusy: false,
  pollTimer: null,
  stateFingerprint: "",
  newMode: "pass_play",
  newColor: "white",
  newElo: 1400,
  newTimeControl: "unlimited",
  newPace: "blitz",
  clockAnchor: null,
  clockTimer: null,
  clockZeroKey: "",
  engineMode: "client_wasm",
  clientEvalKey: "",
  clientBotKey: "",
  clientBotBusy: false,
};

export function setGame(game) {
  app.game = game;
}

export function player(game, color) {
  if (!game || !game.players) return { color, name: color === "white" ? "White" : "Black" };
  return game.players[color] || { color, name: color === "white" ? "White" : "Black" };
}

export function sideToMove(game) {
  if (!game) return "white";
  return game.turn === "black" ? "black" : "white";
}

export function titleCase(value) {
  if (!value) return "";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function initials(name, color) {
  const text = String(name || "").trim();
  if (!text) return color === "black" ? "B" : "W";
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/** A Map of legal destinations, ready for Chessground. */
export function legalDests(fen) {
  const dests = new Map();
  try {
    const chess = new Chess(fen);
    for (const move of chess.moves({ verbose: true })) {
      const list = dests.get(move.from);
      if (list) {
        if (!list.includes(move.to)) list.push(move.to);
      } else {
        dests.set(move.from, [move.to]);
      }
    }
  } catch (error) {
    console.error("Could not read legal moves for", fen, error);
  }
  return dests;
}

/** True when a move from→to would need a promotion piece. */
export function isPromotion(fen, from, to) {
  try {
    const chess = new Chess(fen);
    const piece = chess.get(from);
    if (!piece || piece.type !== "p") return false;
    if (to[1] !== "8" && to[1] !== "1") return false;
    return chess.moves({ verbose: true }).some((move) => move.from === from && move.to === to);
  } catch {
    return false;
  }
}

export function modeTitle(game) {
  if (!game) return "Pass & Play";
  if (game.mode === "stockfish") return "Play vs Stockfish";
  if (game.mode === "online") return "Online Room";
  if (game.mode === "import") {
    if (game.source === "fen") return "Position Study";
    return "Imported Study";
  }
  return "Pass & Play";
}

/** In an engine game the human may only move on their own turn. */
export function canHumanMove(game) {
  if (!game || !game.status || game.status.state !== "in_progress") return false;
  const room = game.room;
  if (room) {
    if (room.status !== "active" || !room.seat) return false;
    return game.turn === room.seat;
  }
  if (game.mode !== "stockfish") return true;
  const bot = game.engine && game.engine.bot_color;
  return !bot || game.turn !== bot;
}

export function sourceLabel(game) {
  if (!game) return "Local";
  if (game.source === "pgn") return "PGN";
  if (game.source === "fen") return "FEN";
  return "Local";
}

export function resultLabel(game) {
  if (!game) return "Live";
  const result =
    game.status && game.status.state !== "in_progress"
      ? game.status.result
      : (game.headers && game.headers.Result) || "*";
  if (result === "1-0") return "1–0";
  if (result === "0-1") return "0–1";
  if (result === "1/2-1/2") return "½–½";
  return "Live";
}

export function resultTone(game) {
  if (!game) return "is-live";
  const result =
    game.status && game.status.state !== "in_progress"
      ? game.status.result
      : (game.headers && game.headers.Result) || "*";
  if (result === "1-0") return "is-white";
  if (result === "0-1") return "is-black";
  if (result === "1/2-1/2") return "is-draw";
  return "is-live";
}

export function positionTitle(game) {
  if (!game) return "White to move";
  const status = game.status || {};
  if (status.state === "checkmate") {
    return `Checkmate — ${titleCase(status.winner)} wins`;
  }
  if (status.state === "timeout") {
    return `${titleCase(status.winner)} wins on time`;
  }
  if (status.state === "resigned") {
    return `${titleCase(status.winner)} wins by resignation`;
  }
  if (status.state === "draw") {
    const reasons = {
      stalemate: "Stalemate",
      insufficient_material: "Draw — insufficient material",
      seventyfive_moves: "Draw — 75-move rule",
      fivefold_repetition: "Draw — fivefold repetition",
      agreement: "Draw — by agreement",
    };
    return reasons[status.reason] || "Draw";
  }
  const turn = titleCase(sideToMove(game));
  return `${turn} to move${status.check ? " · check" : ""}`;
}

export function turnDescription(game) {
  if (!game) return "White to move";
  if (game.status.state === "checkmate") return `${titleCase(game.status.winner)} wins by checkmate`;
  if (game.status.state === "timeout") return `${titleCase(game.status.winner)} wins on time`;
  if (game.status.state === "resigned") return `${titleCase(game.status.winner)} wins by resignation`;
  if (game.status.state === "draw") {
    return game.status.reason === "agreement" ? "The players agreed a draw" : "The game is drawn";
  }
  if (game.engine && game.engine.thinking) return "Stockfish is thinking…";
  return `${titleCase(game.turn)} to move${game.status.check ? " · check" : ""}`;
}

export function positionSubtitle(game) {
  if (!game) return "Start of the game · 0 of 0 plies";
  if (!game.total_plies) {
    const prefix = game.ply === 0 ? "Start of the game" : "Initial position";
    return `${prefix} · 0 of 0 plies`;
  }
  const move = game.last_move;
  const after = move ? `After ${move.move_number}${move.color === "white" ? "." : "..."} ${move.san}` : "Initial position";
  return `${after} · ${game.ply} of ${game.total_plies} plies`;
}

export function sessionEvent(game) {
  if (!game) return "A quiet table for two";
  if (game.branch_available) {
    return `Reviewing ply ${game.ply} — a move here starts a new branch`;
  }
  if (game.status.state === "checkmate") {
    return `${titleCase(game.status.winner)} delivered checkmate`;
  }
  if (game.status.state === "timeout") {
    return `${titleCase(game.status.winner)} won on time`;
  }
  if (game.status.state === "resigned") {
    return `${titleCase(game.status.winner)} won by resignation`;
  }
  if (game.status.state === "draw") {
    return game.status.reason === "agreement"
      ? "The players agreed a draw"
      : "The game ended in a draw";
  }
  if (game.ply < game.total_plies) {
    return `Reviewing ply ${game.ply} of ${game.total_plies}`;
  }
  if (game.engine && game.engine.thinking) {
    return "Stockfish is thinking…";
  }
  return `${titleCase(game.turn)} to move at the table`;
}

export function exportFilename(game) {
  if (!game) return "chess-master-game.pgn";
  const white = player(game, "white").name || "White";
  const black = player(game, "black").name || "Black";
  const date = (game.headers && game.headers.Date) || "";
  const clean = (value) =>
    String(value)
      .normalize("NFKD")
      .replace(/[^\w\s.-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .slice(0, 40);
  const stamp = date.replace(/\./g, "-").replace(/\?/g, "x") || game.game_id || "game";
  return `${clean(white) || "White"}-vs-${clean(black) || "Black"}-${stamp}.pgn`;
}
