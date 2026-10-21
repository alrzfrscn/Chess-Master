/** Player banners, captured material and the to-move glow. */

import { initials, player, titleCase } from "./state.js";

const PIECE_LETTERS = { p: "P", n: "N", b: "B", r: "R", q: "Q", k: "K" };

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
}

export function renderPlayers(game, orientation, refs) {
  if (!game) return;
  const topColor = orientation === "white" ? "black" : "white";
  const bottomColor = topColor === "black" ? "white" : "black";
  if (refs.top) renderBanner(refs.top, game, topColor);
  if (refs.bottom) renderBanner(refs.bottom, game, bottomColor);
}
