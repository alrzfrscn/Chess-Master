/** Move timeline and position summary. */

import { positionSubtitle, positionTitle, resultLabel, titleCase } from "./state.js";

export function renderMoves(game, refs) {
  if (!game) return;
  const list = refs.list;
  const pieces = game.moves || [];

  if (refs.count) refs.count.textContent = String(Math.ceil(game.total_plies / 2));
  if (refs.empty) refs.empty.hidden = pieces.length > 0;
  if (list) list.hidden = pieces.length === 0;
  if (refs.head) refs.head.hidden = pieces.length === 0;

  if (list) {
    const rows = new Map();
    for (const move of pieces) {
      let row = rows.get(move.move_number);
      if (!row) {
        row = {};
        rows.set(move.move_number, row);
      }
      row[move.color] = move;
    }

    const fragment = document.createDocumentFragment();
    for (const number of [...rows.keys()].sort((a, b) => a - b)) {
      const row = rows.get(number);
      const element = document.createElement("div");
      element.className = "move-row";

      const numberEl = document.createElement("span");
      numberEl.className = "move-number";
      numberEl.textContent = `${number}.`;
      element.append(numberEl);

      for (const color of ["white", "black"]) {
        const move = row[color];
        if (!move) {
          const placeholder = document.createElement("span");
          placeholder.className = "move-placeholder";
          placeholder.setAttribute("aria-hidden", "true");
          element.append(placeholder);
          continue;
        }

        const button = document.createElement("button");
        button.type = "button";
        button.className = "move-cell";
        button.dataset.ply = String(move.ply);
        button.title = move.comment ? `${move.san} — ${move.comment}` : `${move.san} (${move.uci})`;
        if (move.ply === game.ply) button.classList.add("is-current");
        if (move.ply > game.ply) button.classList.add("is-future");
        if (move.ply === game.ply) button.setAttribute("aria-current", "true");

        const san = document.createElement("span");
        san.className = "move-san";
        san.textContent = move.san;
        button.append(san);

        if (move.comment) {
          const dot = document.createElement("span");
          dot.className = "move-comment-dot";
          dot.setAttribute("aria-hidden", "true");
          button.append(dot);
        }
        element.append(button);
      }
      fragment.append(element);
    }
    list.replaceChildren(fragment);

    const current = list.querySelector(".move-cell.is-current");
    if (current) {
      requestAnimationFrame(() => {
        const listRect = list.getBoundingClientRect();
        const activeRect = current.getBoundingClientRect();
        if (activeRect.top < listRect.top || activeRect.bottom > listRect.bottom) {
          list.scrollTop += activeRect.top - listRect.top - list.clientHeight / 2 + activeRect.height / 2;
        }
      });
    } else {
      list.scrollTop = 0;
    }
  }

  const nav = refs.nav || {};
  if (nav.first) nav.first.disabled = !game.can_prev;
  if (nav.prev) nav.prev.disabled = !game.can_prev;
  if (nav.next) nav.next.disabled = !game.can_next;
  if (nav.last) nav.last.disabled = !game.can_next;

  if (refs.branchNote) refs.branchNote.hidden = !game.branch_available;
  if (refs.positionTitle) refs.positionTitle.textContent = positionTitle(game);
  if (refs.positionSub) refs.positionSub.textContent = positionSubtitle(game);
  if (refs.positionResult) {
    refs.positionResult.textContent = resultLabel(game);
  }

  if (refs.positionIcon) {
    refs.positionIcon.classList.toggle("is-over", game.status.state !== "in_progress");
    const use = refs.positionIcon.querySelector("use");
    if (use) {
      const icon =
        game.status.state === "checkmate"
          ? "#i-crown"
          : game.status.state === "draw"
            ? "#i-target"
            : game.status.check
              ? "#i-alert"
              : "#i-activity";
      use.setAttribute("href", icon);
    }
  }

  if (refs.turnLabel) refs.turnLabel.textContent = statusLine(game);
  if (refs.turnDot) {
    refs.turnDot.classList.toggle("is-black", game.turn === "black");
    refs.turnDot.classList.toggle("is-check", Boolean(game.status.check));
  }
}

function statusLine(game) {
  if (game.status.state === "checkmate") {
    return `${titleCase(game.status.winner)} wins by checkmate`;
  }
  if (game.status.state === "draw") {
    return "Draw";
  }
  if (game.status.state === "timeout") {
    return `${titleCase(game.status.winner)} wins on time`;
  }
  if (game.engine && game.engine.thinking) {
    return "Stockfish is thinking…";
  }
  return `${titleCase(game.turn)} to move${game.status.check ? " · check" : ""}`;
}
