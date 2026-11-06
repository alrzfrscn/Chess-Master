/** Player banners, captured material and the to-move glow. */

import { app, initials, player, titleCase } from "./state.js";

const PIECE_LETTERS = { p: "P", n: "N", b: "B", r: "R", q: "Q", k: "K" };

/** Re-anchor the client interpolation to the authoritative server clocks. */
export function anchorClock(game) {
  const clocks = game && game.clocks;
  if (!clocks || !clocks.enabled) {
    app.clockAnchor = null;
    return;
  }
  app.clockAnchor = {
    gameId: game.game_id,
    revision: game.revision,
    ply: game.ply,
    white: Math.max(0, Number(clocks.white_ms) || 0),
    black: Math.max(0, Number(clocks.black_ms) || 0),
    active: clocks.active_color === "black" ? "black" : "white",
    running: Boolean(clocks.running),
    at: performance.now(),
  };
}

/** Remaining milliseconds for one side, including the local 100 ms tick. */
export function remainingClock(color) {
  const anchor = app.clockAnchor;
  if (!anchor) return null;
  let ms = color === "black" ? anchor.black : anchor.white;
  if (anchor.running && anchor.active === color) ms -= performance.now() - anchor.at;
  return Math.max(0, ms);
}

/** `M:SS` normally, `0:SS.d` under twenty seconds. */
export function formatClock(ms) {
  const clamped = Math.max(0, Number(ms) || 0);
  if (clamped < 20_000) {
    const tenths = Math.floor(clamped / 100);
    return `0:${String(Math.floor(tenths / 10)).padStart(2, "0")}.${tenths % 10}`;
  }
  const total = Math.ceil(clamped / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function renderClock(banner, game, color) {
  const element = banner.querySelector("[data-clock]");
  if (!element) return;
  const clocks = game.clocks;
  if (!clocks || !clocks.enabled) {
    element.hidden = true;
    return;
  }
  element.hidden = false;
  const remaining = remainingClock(color);
  if (remaining == null) return;

  const time = element.querySelector("[data-clock-time]");
  if (time) time.textContent = formatClock(remaining);
  const active = clocks.running && clocks.active_color === color && game.status.state === "in_progress";
  element.classList.toggle("is-active", active);
  element.classList.toggle("is-low", remaining < 20_000 && remaining >= 10_000);
  element.classList.toggle("is-critical", remaining < 10_000);

  const increment = Number(clocks.increment_ms) || 0;
  const badge = element.querySelector("[data-clock-inc]");
  if (badge) {
    badge.textContent = increment ? `+${increment / 1000}` : "";
    badge.hidden = !increment;
  }
  element.title = clocks.label ? `${clocks.label} · ${titleCase(color)}` : "";
}

/** Repaint both banner clocks; the 100 ms ticker calls this directly. */
export function renderClocks(game, orientation, refs) {
  if (!game || !refs) return;
  const topColor = orientation === "white" ? "black" : "white";
  if (refs.top) renderClock(refs.top, game, topColor);
  if (refs.bottom) renderClock(refs.bottom, game, topColor === "black" ? "white" : "black");
}

function pieceImage(color, letter) {
  const prefix = color === "white" ? "b" : "w";
  const piece = PIECE_LETTERS[letter] || "P";
  return `assets/pieces/${prefix}${piece}.svg`;
}

function renderBanner(banner, game, color) {
  const info = player(game, color);
  const active = game.status.state === "in_progress" && game.turn === color;
  const thinking = Boolean(active && info.is_engine && game.engine && game.engine.thinking);
  banner.dataset.color = color;
  banner.classList.toggle("is-to-move", active);
  banner.classList.toggle("is-thinking", thinking);
  banner.classList.toggle("is-engine", Boolean(info.is_engine));

  const avatarText = banner.querySelector("[data-avatar-text]");
  if (avatarText) avatarText.textContent = info.is_engine ? "SF" : initials(info.name, color);

  const name = banner.querySelector("[data-name]");
  if (name) name.textContent = info.name || titleCase(color);

  const title = banner.querySelector("[data-title]");
  if (title) {
    title.textContent = info.title || "";
    title.hidden = !info.title;
  }

  const rating = banner.querySelector("[data-rating]");
  if (rating) {
    rating.textContent = info.rating ? String(info.rating) : "";
    rating.hidden = !info.rating;
  }

  const side = banner.querySelector("[data-side]");
  if (side) side.textContent = titleCase(color);

  const engine = banner.querySelector("[data-engine]");
  if (engine) engine.hidden = !info.is_engine;

  const turn = banner.querySelector("[data-turn]");
  if (turn) {
    turn.hidden = !active;
    turn.textContent = thinking ? "Thinking…" : "To move";
  }

  const captured = banner.querySelector("[data-captured]");
  if (captured) {
    captured.replaceChildren();
    const pieces = (game.material && game.material[color] && game.material[color].captured) || [];
    for (const letter of pieces) {
      const img = document.createElement("img");
      img.src = pieceImage(color, letter);
      img.alt = "";
      img.draggable = false;
      captured.append(img);
    }
    captured.hidden = pieces.length === 0;
  }

  const badge = banner.querySelector("[data-material]");
  if (badge) {
    const advantage = Number(game.material && game.material.advantage) || 0;
    const leader = game.material && game.material.leader;
    if (leader === color && advantage !== 0) {
      badge.textContent = `+${Math.abs(advantage)}`;
      badge.hidden = false;
    } else {
      badge.textContent = "";
      badge.hidden = true;
    }
  }

  renderClock(banner, game, color);
}

export function renderPlayers(game, orientation, refs) {
  if (!game) return;
  const topColor = orientation === "white" ? "black" : "white";
  const bottomColor = topColor === "black" ? "white" : "black";
  if (refs.top) renderBanner(refs.top, game, topColor);
  if (refs.bottom) renderBanner(refs.bottom, game, bottomColor);
}
