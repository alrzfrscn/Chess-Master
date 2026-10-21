/** Reusable interface controllers: tabs, toasts, dialogs, clipboard. */

const TOAST_ICONS = {
  info: "#i-info",
  success: "#i-check-circle",
  warning: "#i-alert",
  error: "#i-alert",
};

export class TabController {
  constructor({ tablist, ink, onChange }) {
    this.tablist = tablist;
    this.ink = ink;
    this.onChange = onChange || (() => {});
    this.tabs = [...tablist.querySelectorAll("[data-tab]")];
    this.active = null;

    for (const tab of this.tabs) {
      tab.addEventListener("click", () => this.setActive(tab.dataset.tab));
      tab.addEventListener("keydown", (event) => this.onKeydown(event));
    }

    const initial = this.tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || this.tabs[0];
    if (initial) this.setActive(initial.dataset.tab, { focus: false, silent: true });

    if ("ResizeObserver" in window) {
      this.resizeObserver = new ResizeObserver(() => this.positionInk());
      this.resizeObserver.observe(tablist);
    }
    window.addEventListener("resize", () => this.positionInk());
  }

  onKeydown(event) {
    const current = this.tabs.indexOf(document.activeElement);
    if (current < 0) return;
    let next = null;
    if (event.key === "ArrowRight") next = (current + 1) % this.tabs.length;
    if (event.key === "ArrowLeft") next = (current - 1 + this.tabs.length) % this.tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = this.tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    this.tabs[next].focus();
    this.setActive(this.tabs[next].dataset.tab);
  }

  setActive(name, { focus = false, silent = false } = {}) {
    if (!name) return;
    const tab = this.tabs.find((item) => item.dataset.tab === name);
    if (!tab) return;
    this.active = name;

    for (const item of this.tabs) {
      const selected = item === tab;
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      if (selected && focus) item.focus();
    }

    const panelId = tab.getAttribute("aria-controls");
    for (const tabItem of this.tabs) {
      const panel = document.getElementById(tabItem.getAttribute("aria-controls"));
      if (!panel) continue;
      const selected = tabItem.getAttribute("aria-controls") === panelId;
      panel.hidden = !selected;
      panel.classList.toggle("is-active", selected);
    }

    requestAnimationFrame(() => this.positionInk());
    if (!silent) this.onChange(name);
  }

  positionInk() {
    if (!this.ink || !this.active) return;
    const tab = this.tabs.find((item) => item.dataset.tab === this.active);
    if (!tab) return;
    const style = getComputedStyle(this.tablist);
    const paddingLeft = parseFloat(style.paddingLeft) || 0;
    this.ink.style.left = `${tab.offsetLeft - paddingLeft}px`;
    this.ink.style.width = `${tab.offsetWidth}px`;
  }
}

const toastTimers = new WeakMap();

export function toast({ title, description = "", kind = "info", duration = 4200 } = {}) {
  const region = document.getElementById("toast-region");
  if (!region) return;

  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.kind = kind;

  const mark = document.createElement("span");
  mark.className = "toast-mark";
  mark.innerHTML = `<svg class="icon" aria-hidden="true"><use href="${TOAST_ICONS[kind] || TOAST_ICONS.info}" /></svg>`;

  const copy = document.createElement("div");
  const titleEl = document.createElement("p");
  titleEl.className = "toast-title";
  titleEl.textContent = title || "";
  copy.append(titleEl);
  if (description) {
    const detail = document.createElement("p");
    detail.className = "toast-copy";
    detail.textContent = description;
    copy.append(detail);
  }

  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss notification");
  close.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-x" /></svg>';

  node.append(mark, copy, close);
  region.append(node);

  const remove = () => {
    if (node.classList.contains("is-leaving")) return;
    node.classList.add("is-leaving");
    clearTimeout(toastTimers.get(node));
    setTimeout(() => node.remove(), 230);
  };

  close.addEventListener("click", remove);
  toastTimers.set(node, setTimeout(remove, duration));

  while (region.children.length > 3) region.firstElementChild.remove();
}

export function openDialog(dialog) {
  if (!dialog || dialog.open) return;
  dialog.showModal();
}

export function closeDialog(dialog) {
  if (dialog && dialog.open) dialog.close();
}

/**
 * Populate and show the shared confirmation dialog.
 * Resolves true only when the destructive button is pressed.
 */
export function askConfirm(dialog, { eyebrow, title, copy, acceptLabel = "Confirm" }) {
  return new Promise((resolve) => {
    if (!dialog) {
      resolve(false);
      return;
    }
    const eyebrowEl = dialog.querySelector("#confirm-eyebrow");
    const titleEl = dialog.querySelector("#confirm-title");
    const copyEl = dialog.querySelector("#confirm-copy");
    const accept = dialog.querySelector("#confirm-accept");
    if (eyebrowEl && eyebrow) eyebrowEl.textContent = eyebrow;
    if (titleEl) titleEl.textContent = title || "Please confirm";
    if (copyEl) copyEl.textContent = copy || "";
    if (accept) accept.textContent = acceptLabel;

    let settled = false;
    const cleanups = [];
    const listen = (target, event, handler) => {
      target.addEventListener(event, handler);
      cleanups.push(() => target.removeEventListener(event, handler));
    };

    const finish = (value) => {
      if (settled) return;
      settled = true;
      for (const cleanup of cleanups) cleanup();
      if (dialog.open) dialog.close();
      resolve(value);
    };

    for (const cancel of dialog.querySelectorAll("[data-confirm-cancel]")) {
      listen(cancel, "click", () => finish(false));
    }
    if (accept) listen(accept, "click", () => finish(true));
    listen(dialog, "cancel", (event) => {
      event.preventDefault();
      finish(false);
    });
    listen(dialog, "close", () => finish(false));
    listen(dialog, "click", (event) => {
      if (event.target === dialog) finish(false);
    });

    openDialog(dialog);
    if (accept) requestAnimationFrame(() => accept.focus());
  });
}

export async function copyText(text) {
  const value = String(text ?? "");
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall through to the legacy path.
  }

  const helper = document.createElement("textarea");
  helper.value = value;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.inset = "0 auto auto 0";
  helper.style.opacity = "0";
  document.body.append(helper);
  helper.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  helper.remove();
  return copied;
}

export function downloadText(filename, text, type = "application/x-chess-pgn") {
  const blob = new Blob([text], { type: `${type};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Keep the object URL alive long enough for the WebView to finish writing the
  // file; revoking immediately can cancel slower downloads (notably WebView2).
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
