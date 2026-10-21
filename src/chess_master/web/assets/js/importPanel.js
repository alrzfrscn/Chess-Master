/** Import, quick-load and export drawer. */

import { call, detailLines, errorMessage } from "./api.js";
import { exportFilename, player } from "./state.js";
import { copyText, downloadText, toast } from "./ui.js";

const FEN_PLACEHOLDER = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const FORMAT_COPY = {
  pgn: {
    label: "Paste a game",
    hint: "Headers & comments welcome",
    button: "Import PGN",
    busy: "Reading game…",
    placeholder: null,
  },
  fen: {
    label: "Paste a position",
    hint: "Standard FEN · side to move included",
    button: "Load FEN",
    busy: "Setting position…",
    placeholder: FEN_PLACEHOLDER,
  },
};

export class ImportPanel {
  constructor({ onGameChanged, onTabRequested, notify = toast } = {}) {
    this.onGameChanged = onGameChanged || (() => {});
    this.onTabRequested = onTabRequested || (() => {});
    this.notify = notify;
    this.format = "pgn";
    this.samples = [];
    this.game = null;
    this.busy = false;
    this.exportSeq = 0;
    this.sampleIcons = { pgn: "#i-file", fen: "#i-target" };

    this.textarea = document.getElementById("import-text");
    this.feedback = document.getElementById("import-feedback");
    this.feedbackTitle = document.getElementById("import-feedback-title");
    this.feedbackCopy = document.getElementById("import-feedback-copy");
    this.feedbackIcon = document.getElementById("import-feedback-icon");
    this.importButton = document.getElementById("btn-import");
    this.importButtonLabel = document.getElementById("btn-import-label");
    this.importLabel = document.getElementById("import-label");
    this.importHint = document.getElementById("import-hint");
    this.samplesEl = document.getElementById("sample-list");
    this.exportArea = document.getElementById("export-pgn");
    this.exportMeta = document.getElementById("export-meta");

    FORMAT_COPY.pgn.placeholder = this.textarea ? this.textarea.placeholder : "";

    for (const button of document.querySelectorAll("[data-format]")) {
      button.addEventListener("click", () => this.setFormat(button.dataset.format));
    }

    if (this.importButton) {
      this.importButton.addEventListener("click", () => this.importCurrent());
    }
    if (this.textarea) {
      this.textarea.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          event.preventDefault();
          this.importCurrent();
        }
      });
      this.textarea.addEventListener("input", () => this.hideFeedback());
    }

    document.getElementById("btn-copy-pgn")?.addEventListener("click", () => this.copyPgn());
    document.getElementById("btn-download-pgn")?.addEventListener("click", () => this.downloadPgn());
    document.getElementById("btn-copy-fen")?.addEventListener("click", () => this.copyFen());
  }

  setFormat(format) {
    if (!FORMAT_COPY[format] || this.busy) return;
    this.format = format;
    const copy = FORMAT_COPY[format];
    for (const button of document.querySelectorAll("[data-format]")) {
      const selected = button.dataset.format === format;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    if (this.importLabel) this.importLabel.textContent = copy.label;
    if (this.importHint) this.importHint.textContent = copy.hint;
    if (this.importButtonLabel) this.importButtonLabel.textContent = copy.button;
    if (this.textarea) {
      this.textarea.placeholder = copy.placeholder;
      this.textarea.classList.remove("busy");
    }
    this.hideFeedback();
  }

  setSamples(samples) {
    this.samples = Array.isArray(samples) ? samples : [];
    if (!this.samplesEl) return;
    const fragment = document.createDocumentFragment();
    for (const sample of this.samples) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sample-item";
      button.dataset.sample = sample.id;
      button.title = `Load ${sample.label}`;

      const mark = document.createElement("span");
      mark.className = "sample-mark";
      mark.innerHTML = `<svg class="icon" aria-hidden="true"><use href="${this.sampleIcons[sample.kind] || "#i-file"}" /></svg>`;

      const copy = document.createElement("span");
      copy.className = "sample-copy";
      const label = document.createElement("span");
      label.className = "sample-label";
      label.textContent = sample.label;
      const sub = document.createElement("span");
      sub.className = "sample-sub";
      sub.textContent = sample.subtitle || "";
      copy.append(label, sub);

      const kind = document.createElement("span");
      kind.className = "sample-kind";
      kind.textContent = (sample.kind || "pgn").toUpperCase();

      button.append(mark, copy, kind);
      button.addEventListener("click", () => this.importSample(sample));
      fragment.append(button);
    }
    this.samplesEl.replaceChildren(fragment);
  }

  setGame(game) {
    this.game = game;
    if (this.exportMeta && game) {
      this.exportMeta.textContent = `${game.total_plies} ${game.total_plies === 1 ? "ply" : "plies"}`;
    }
  }

  async refreshExport() {
    if (!this.exportArea) return;
    const sequence = ++this.exportSeq;
    try {
      const data = await call("export_pgn");
      if (sequence !== this.exportSeq) return;
      this.exportArea.value = data.pgn || "";
      if (this.exportMeta) {
        this.exportMeta.textContent = `${data.total_plies} ${data.total_plies === 1 ? "ply" : "plies"}`;
      }
    } catch (error) {
      if (sequence === this.exportSeq) {
        console.error("PGN export failed", error);
      }
    }
  }

  async importCurrent() {
    if (!this.textarea || this.busy) return;
    const text = this.textarea.value.trim();
    this.hideFeedback();
    if (!text) {
      this.showFeedback(
        "error",
        this.format === "pgn" ? "Paste a PGN first" : "Paste a FEN first",
        this.format === "pgn"
          ? "A game needs at least a move or two. Try a sample from Quick load."
          : "A FEN describes all 64 squares, the side to move, castling and en passant.",
      );
      this.textarea.focus();
      return;
    }
    await this.loadText(this.format, text);
  }

  async importSample(sample) {
    if (!sample || this.busy) return;
    this.setFormat(sample.kind === "fen" ? "fen" : "pgn");
    if (this.textarea) this.textarea.value = sample.text || "";
    await this.loadText(sample.kind === "fen" ? "fen" : "pgn", sample.text || "", { sample: sample.label });
  }

  async loadText(format, text, { sample = null } = {}) {
    if (this.busy) return;
    this.setBusy(true);
    const copy = FORMAT_COPY[format];
    if (this.importButtonLabel) this.importButtonLabel.textContent = copy.busy;
    if (this.textarea) this.textarea.classList.add("busy");

    try {
      const state = format === "fen" ? await call("load_fen", text) : await call("load_pgn", text);
      this.setBusy(false);
      this.hideFeedback();
      const white = player(state, "white").name;
      const black = player(state, "black").name;
      const title = format === "fen" ? "Position loaded" : "Game imported";
      const description =
        format === "fen"
          ? `${state.mode_label || "Position"} · ${state.turn} to move`
          : `${white} vs ${black} · ${state.total_plies} plies`;

      this.notify({ title, description, kind: "success" });
      if (format === "pgn" && state.import_meta) {
        const meta = state.import_meta;
        if (meta.warnings && meta.warnings.length) {
          this.notify({ title: "Main line only", description: meta.warnings[0], kind: "warning", duration: 6000 });
        } else if (meta.comment_count) {
          this.notify({
            title: "Comments preserved",
            description: `${meta.comment_count} ${meta.comment_count === 1 ? "comment" : "comments"} will survive export.`,
            kind: "info",
            duration: 3400,
          });
        }
      }
      if (sample) {
        this.notify({ title: sample, description: "Loaded into the study board.", kind: "info", duration: 2600 });
      }

      this.onGameChanged(state);
      this.onTabRequested("moves");
    } catch (error) {
      this.setBusy(false);
      const details = detailLines(error);
      this.showFeedback(
        "error",
        format === "fen" ? "That position was rejected" : "That game could not be imported",
        `${errorMessage(error)}${details ? ` · ${details}` : ""}`,
      );
      this.textarea?.focus();
    } finally {
      this.setBusy(false);
      if (this.importButtonLabel) this.importButtonLabel.textContent = FORMAT_COPY[this.format].button;
      if (this.textarea) this.textarea.classList.remove("busy");
    }
  }

  setBusy(busy) {
    this.busy = busy;
    if (this.importButton) this.importButton.disabled = busy;
    if (this.textarea) this.textarea.disabled = busy;
    for (const button of document.querySelectorAll(".sample-item, [data-format]")) {
      button.disabled = busy;
    }
  }

  showFeedback(kind, title, copy) {
    if (!this.feedback) return;
    this.feedback.hidden = false;
    this.feedback.classList.toggle("is-success", kind === "success");
    if (this.feedbackTitle) this.feedbackTitle.textContent = title;
    if (this.feedbackCopy) this.feedbackCopy.textContent = copy || "";
    if (this.feedbackIcon) {
      this.feedbackIcon.innerHTML = `<use href="${kind === "success" ? "#i-check-circle" : "#i-alert"}" />`;
    }
  }

  hideFeedback() {
    if (this.feedback) this.feedback.hidden = true;
  }

  async copyPgn() {
    const pgn = this.exportArea ? this.exportArea.value : "";
    if (!pgn) return;
    const copied = await copyText(pgn);
    this.notify({
      title: copied ? "PGN copied" : "Copy unavailable",
      description: copied ? "The current main line is on your clipboard." : "Select the export field and copy manually.",
      kind: copied ? "success" : "warning",
    });
  }

  copyFen() {
    const fen = this.game ? this.game.fen : "";
    if (!fen) return;
    copyText(fen).then((copied) => {
      this.notify({
        title: copied ? "FEN copied" : "Copy unavailable",
        description: copied ? fen : "Select the position text and copy manually.",
        kind: copied ? "success" : "warning",
      });
    });
  }

  downloadPgn() {
    const pgn = this.exportArea ? this.exportArea.value : "";
    if (!pgn || !this.game) return;
    downloadText(exportFilename(this.game), pgn);
    this.notify({ title: "PGN saved", description: exportFilename(this.game), kind: "success" });
  }
}
