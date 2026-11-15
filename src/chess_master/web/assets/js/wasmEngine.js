/**
 * Stockfish Lite (WASM) running in this tab.
 *
 * The vendored worker is the single-threaded Stockfish 19 Lite build: no
 * SharedArrayBuffer, no COOP/COEP headers, no server CPU. This module owns the
 * UCI handshake, the one-search-at-a-time queue, stale-search cancellation and
 * the same White-POV / Elo / pacing rules as the Python worker.
 *
 * Evaluation math mirrors `analysis/winprob.py` and `engine/stockfish.py`;
 * scheduling mirrors `engine/coordinator.py`. Keep the constants in step.
 */

import { Chess } from "../vendor/chess.js/chess.js";

export const WORKER_URL = "assets/vendor/stockfish/stockfish-lite.js";
export const WINPROB_K = 0.00368208;
export const EVAL_CLAMP_CP = 1500;
export const MATE_CP = 10000;

const PACES = {
  instant: [0, 0],
  bullet: [350, 650],
  blitz: [750, 1350],
  rapid: [1500, 2500],
};

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

export function winProbability(cp) {
  const score = clamp(Number(cp) || 0, -EVAL_CLAMP_CP, EVAL_CLAMP_CP);
  return clamp(100 / (1 + Math.exp(-WINPROB_K * score)), 0, 100);
}

export function formatScore(cp, mate) {
  if (mate != null) return `${mate > 0 ? "M" : "-M"}${Math.abs(mate)}`;
  if (cp == null) return "0.0";
  const pawns = cp / 100;
  const text = Math.abs(pawns).toFixed(1);
  return cp > 0 ? `+${text}` : cp < 0 ? `-${text}` : text;
}

/** Exact calibration from `engine/stockfish.py::elo_profile`. */
export function eloProfile(elo) {
  const wanted = clamp(Math.round(Number(elo) || 400), 400, 3000);
  if (wanted >= 3000) {
    return {
      mode: "full",
      limitStrength: false,
      uciElo: null,
      skill: 20,
      multipv: 1,
      depth: 18,
      nodes: null,
      movetime: 1200,
    };
  }
  if (wanted >= 1320) {
    const ratio = (wanted - 1320) / (3000 - 1320);
    return {
      mode: "limit_strength",
      limitStrength: true,
      uciElo: clamp(wanted, 1320, 3190),
      skill: 20,
      multipv: 1,
      depth: 10 + Math.round(ratio * 6),
      nodes: null,
      movetime: Math.round((0.25 + ratio * 0.75) * 1000),
    };
  }
  const ratio = (wanted - 400) / (1320 - 400);
  return {
    mode: "skill_weighted",
    limitStrength: false,
    uciElo: null,
    skill: clamp(Math.round(ratio * 10), 0, 10),
    multipv: 3,
    depth: 3 + Math.round(ratio * 7),
    nodes: Math.round(2000 + ratio * 180000),
    movetime: Math.round((0.08 + ratio * 0.22) * 1000),
  };
}

/** Randomized human-like delay, clamped when the bot's clock runs low. */
export function pacingDelay(botPace, remainingMs) {
  let [low, high] = PACES[botPace] || PACES.instant;
  if (remainingMs != null && botPace !== "instant") {
    if (remainingMs < 10000) [low, high] = [180, 260];
    else if (remainingMs < 25000) [low, high] = [350, Math.min(high, 550)];
  }
  return low + Math.random() * Math.max(0, high - low);
}

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, Math.max(0, ms)));

function tokenKey(token) {
  if (!token) return "";
  return [token.game_id, token.revision, token.ply, token.fen].join("|");
}

export function fenFor(startFen, moves = []) {
  const chess = new Chess(startFen || undefined);
  for (const uci of moves) {
    chess.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
  }
  return chess.fen();
}

function parseInfo(line) {
  const tokens = line.trim().split(/\s+/);
  if (tokens[0] !== "info") return null;
  const info = { depth: 0, seldepth: 0, multipv: 1, nodes: 0, time: 0, cp: null, mate: null, pv: [] };
  for (let index = 1; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "depth") info.depth = Number(tokens[++index]) || 0;
    else if (token === "seldepth") info.seldepth = Number(tokens[++index]) || 0;
    else if (token === "multipv") info.multipv = Number(tokens[++index]) || 1;
    else if (token === "nodes") info.nodes = Number(tokens[++index]) || 0;
    else if (token === "time") info.time = Number(tokens[++index]) || 0;
    else if (token === "score") {
      const kind = tokens[++index];
      const value = Number(tokens[++index]) || 0;
      if (kind === "cp") info.cp = value;
      else if (kind === "mate") info.mate = value;
    } else if (token === "pv") {
      info.pv = tokens.slice(index + 1);
      break;
    }
  }
  return info.pv.length ? info : null;
}

function describeMove(fen, uci, chess) {
  if (!uci) return null;
  const from = uci.slice(0, 2);
  const to = uci.slice(2, 4);
  const promotion = uci[4] || undefined;
  let san = null;
  try {
    const move = chess.move({ from, to, promotion });
    if (move) san = move.san;
  } catch {
    san = null;
  }
  return { uci, from, to, promotion: promotion || null, san };
}

function normalizeSearch(fen, infos, fallbackDepth) {
  if (!infos.length) return null;
  const board = new Chess(fen);
  const sign = board.turn() === "w" ? 1 : -1;
  const ordered = [...infos].sort((a, b) => a.multipv - b.multipv);
  const lines = [];
  for (const info of ordered) {
    let cp = info.cp == null ? null : info.cp * sign;
    const mate = info.mate == null ? null : info.mate * sign;
    if (mate != null && cp == null) {
      cp = sign * (MATE_CP - Math.abs(info.mate) * 100);
    }
    const win =
      mate != null ? (mate > 0 ? 100 : 0) : cp == null ? 50 : winProbability(cp);
    const probe = new Chess(fen);
    const pvSan = [];
    for (const uci of info.pv) {
      const described = describeMove(fen, uci, probe);
      if (!described || !described.san) break;
      pvSan.push(described.san);
    }
    const best = describeMove(fen, info.pv[0], new Chess(fen));
    lines.push({
      rank: info.multipv,
      uci: info.pv[0] || null,
      san: best ? best.san : null,
      score_cp: cp,
      mate,
      win_prob_white: Math.round(win * 100) / 100,
      score_text: formatScore(cp, mate),
      pv_uci: [...info.pv],
      pv_san: pvSan,
      best_move: best,
      depth: info.depth || fallbackDepth,
      nodes: info.nodes || 0,
      time_ms: info.time || 0,
    });
  }
  const best = lines[0];
  return {
    lines,
    depth: Math.max(...lines.map((line) => line.depth)),
    seldepth: Math.max(...ordered.map((info) => info.seldepth || 0)),
    multipv: lines.length,
    nodes: Math.max(...ordered.map((info) => info.nodes || 0)),
    time_ms: Math.max(...ordered.map((info) => info.time || 0)),
    score_cp: best.score_cp,
    mate: best.mate,
    score_text: best.score_text,
    win_prob_white: best.win_prob_white,
    best_move: best.best_move,
    pv: [...best.pv_uci],
    pv_san: [...best.pv_san],
  };
}

function pickWeightedMove(lines, elo, turnWhite) {
  const moverWin = (line) =>
    turnWhite ? Number(line.win_prob_white) : 100 - Number(line.win_prob_white);
  const best = Math.max(...lines.map(moverWin));
  const ratio = clamp((Number(elo) - 400) / (1320 - 400), 0, 1);
  const temperature = 2 + (1 - ratio) * 10;
  const weights = lines.map((line) => Math.exp(-Math.max(0, best - moverWin(line)) / temperature));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  let roll = Math.random() * total;
  for (let index = 0; index < lines.length; index += 1) {
    roll -= weights[index];
    if (roll <= 0) return lines[index];
  }
  return lines[lines.length - 1];
}

export class WasmEngineClient {
  constructor({ workerUrl = WORKER_URL, name = "Stockfish Lite (WASM)", onStatus = null } = {}) {
    this.workerUrl = workerUrl;
    this.name = name;
    this.onStatus = onStatus;
    this.worker = null;
    this.readyPromise = null;
    this.status = "idle";
    this.lastError = null;
    this.version = null;
    this.handshake = null;
    this.current = null;
    this.jobs = [];
    this.running = null;
    this.latestAnalyzeKey = "";
    this.cache = new Map();
    this.cacheLimit = 96;
  }

  get ready() {
    return this.status === "ready";
  }

  statusPayload() {
    return {
      mode: "client_wasm",
      engine: this.name,
      available: this.status === "ready",
      status: this.status === "ready" ? "ready" : this.status === "error" ? "error" : "starting",
      version: this.version,
      note:
        this.status === "ready"
          ? "Stockfish Lite runs in this browser tab; the server does no searching."
          : this.lastError || "Loading the browser engine…",
    };
  }

  _setStatus(status, error = null) {
    this.status = status;
    this.lastError = error;
    if (this.onStatus) this.onStatus(this.statusPayload());
  }

  _send(command) {
    if (this.worker) this.worker.postMessage(command);
  }

  start() {
    if (this.readyPromise) return this.readyPromise;
    this._setStatus("loading");
    this.readyPromise = new Promise((resolve, reject) => {
      let settled = false;
      const settle = (error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        if (error) {
          this._setStatus("error", error);
          this.readyPromise = null;
          reject(new Error(error));
        } else {
          this._setStatus("ready");
          resolve(true);
        }
      };
      const timer = window.setTimeout(
        () => settle("The browser engine did not finish loading."),
        30000,
      );
      let worker;
      try {
        worker = new Worker(this.workerUrl);
      } catch (error) {
        settle(error?.message || "The browser engine could not be created.");
        return;
      }
      this.worker = worker;
      this.handshake = "uci";
      worker.onerror = (event) => {
        settle(event?.message || "The browser engine failed to load.");
      };
      worker.onmessage = (event) => {
        const line = typeof event.data === "string" ? event.data : String(event.data?.data ?? "");
        if (line.startsWith("id name ")) this.version = line.slice(8).trim();
        if (this.handshake === "uci" && line.startsWith("uciok")) {
          this.handshake = "ready";
          this._send("setoption name Threads value 1");
          this._send("setoption name Hash value 64");
          this._send("isready");
          return;
        }
        if (this.handshake === "ready" && line.startsWith("readyok")) {
          this.handshake = "done";
          settle(null);
          return;
        }
        this._onLine(line);
      };
      this._send("uci");
    });
    return this.readyPromise;
  }

  _onLine(line) {
    if (line.startsWith("info ")) {
      if (!this.current) return;
      const info = parseInfo(line);
      if (!info) return;
      const existing = this.current.infos.get(info.multipv);
      if (!existing || info.depth >= existing.depth) this.current.infos.set(info.multipv, info);
      return;
    }
    if (!line.startsWith("bestmove")) return;
    const current = this.current;
    if (!current) return;
    this.current = null;
    if (current.stopped) {
      current.resolve(null);
      return;
    }
    const infos = [...current.infos.values()];
    current.resolve(normalizeSearch(current.fen, infos, current.depth));
  }

  _cacheGet(key) {
    const hit = this.cache.get(key);
    if (!hit) return null;
    this.cache.delete(key);
    this.cache.set(key, hit);
    return hit;
  }

  _cachePut(key, payload) {
    if (!payload) return;
    this.cache.delete(key);
    this.cache.set(key, payload);
    while (this.cache.size > this.cacheLimit) {
      this.cache.delete(this.cache.keys().next().value);
    }
  }

  _enqueue(kind, priority, task) {
    return new Promise((resolve) => {
      if (priority <= 1) {
        const kept = [];
        for (const job of this.jobs) {
          if (job.priority <= 1) {
            job.resolve(null);
          } else {
            kept.push(job);
          }
        }
        this.jobs = kept;
      }
      this.jobs.push({ kind, priority, task, resolve });
      this.jobs.sort((a, b) => b.priority - a.priority);
      if (
        this.running &&
        !this.running.stopped &&
        (priority > this.running.priority || (priority === 1 && this.running.priority === 1))
      ) {
        this.running.stopped = true;
        this._send("stop");
      }
      this._pump();
    });
  }

  async _pump() {
    if (this.running || !this.jobs.length || this.status !== "ready") return;
    const job = this.jobs.shift();
    this.running = job;
    try {
      job.resolve(await job.task());
    } catch (error) {
      console.warn("Browser engine job failed", error);
      job.resolve(null);
    } finally {
      this.running = null;
      this._pump();
    }
  }

  /** Run one UCI search; resolves `null` when it was superseded or stopped. */
  _search(fen, { depth, multipv = 1, movetime = null, nodes = null, strength = null, useCache = false }) {
    const strengthTag = strength ? `${strength.limitStrength}:${strength.skill}:${strength.uciElo}` : "full";
    const key = `${fen}|${depth}|${multipv}|${movetime}|${nodes}|${strengthTag}`;
    if (useCache) {
      const cached = this._cacheGet(key);
      if (cached) return Promise.resolve({ ...cached, cached: true });
    }
    return new Promise((resolve) => {
      const current = { fen, depth, infos: new Map(), resolve, stopped: false };
      this.current = current;
      this._send("setoption name Threads value 1");
      this._send("setoption name Hash value 64");
      this._send(`setoption name MultiPV value ${Math.max(1, multipv)}`);
      this._send(
        `setoption name UCI_LimitStrength value ${strength?.limitStrength ? "true" : "false"}`,
      );
      this._send(`setoption name Skill Level value ${strength?.skill ?? 20}`);
      if (strength?.uciElo != null) this._send(`setoption name UCI_Elo value ${strength.uciElo}`);
      this._send(`position fen ${fen}`);
      let command = `go depth ${Math.max(1, depth)}`;
      if (movetime) command += ` movetime ${Math.max(1, Math.round(movetime))}`;
      if (nodes) command += ` nodes ${Math.max(1, Math.round(nodes))}`;
      this._send(command);
      if (useCache) {
        const original = current.resolve;
        current.resolve = (payload) => {
          this._cachePut(key, payload);
          original(payload);
        };
      }
    });
  }

  /** Live position analysis: White POV payload shaped like the Python worker. */
  async analyzePosition(token, startFen, moves, depth = 12, multipv = 3) {
    await this.start();
    const fen = token?.fen || fenFor(startFen, moves);
    const key = tokenKey(token);
    this.latestAnalyzeKey = key;
    const result = await this._enqueue("analysis", 1, () => {
      if (this.latestAnalyzeKey !== key) return null;
      return this._search(fen, {
        depth: Math.max(1, Number(depth) || 12),
        multipv: Math.max(1, Number(multipv) || 3),
        useCache: true,
      });
    });
    if (!result || this.latestAnalyzeKey !== key) return null;
    return {
      available: true,
      mode: "client_wasm",
      engine: this.name,
      engine_version: this.version,
      depth: result.depth,
      seldepth: result.seldepth,
      multipv: result.multipv,
      nodes: result.nodes,
      time_ms: result.time_ms,
      cached: Boolean(result.cached),
      model: "lichess-logistic",
      model_version: 2,
      updated_at: new Date().toISOString(),
      score_cp: result.score_cp,
      mate: result.mate,
      score_text: result.score_text,
      win_prob_white: result.win_prob_white,
      best_move: result.best_move,
      pv: result.pv,
      pv_san: result.pv_san,
      lines: result.lines,
      terminal: null,
      token,
    };
  }

  /** One fixed-depth MultiPV search for the two-pass Game Review pipeline. */
  async runReviewSearch(boardFenOrMoves, depth = 14, multipv = 3) {
    await this.start();
    const fen =
      typeof boardFenOrMoves === "string"
        ? boardFenOrMoves
        : boardFenOrMoves.fen || fenFor(boardFenOrMoves.start_fen, boardFenOrMoves.moves || []);
    const result = await this._enqueue("review", 5, () =>
      this._search(fen, {
        depth: Math.max(1, Number(depth) || 14),
        multipv: Math.max(1, Number(multipv) || 3),
        useCache: true,
      }),
    );
    if (!result) return null;
    return {
      lines: result.lines,
      depth: result.depth,
      multipv: result.multipv,
      cached: Boolean(result.cached),
      nodes: result.nodes,
      time_ms: result.time_ms,
      terminal: null,
    };
  }

  /** Bot move with Python's Elo calibration and pacing. */
  async computeBotMove(token, startFen, moves, elo, botPace = "blitz", remainingMs = null) {
    await this.start();
    const profile = eloProfile(elo);
    const fen = token?.fen || fenFor(startFen, moves);
    const result = await this._enqueue("bot", 6, () =>
      this._search(fen, {
        depth: profile.depth,
        multipv: profile.multipv,
        movetime: profile.movetime,
        nodes: profile.nodes,
        strength: profile,
        useCache: false,
      }),
    );
    if (!result || !result.lines.length) return null;
    const turnWhite = new Chess(fen).turn() === "w";
    const chosen =
      profile.mode === "skill_weighted" && result.lines.length > 1
        ? pickWeightedMove(result.lines, elo, turnWhite)
        : result.lines[0];
    const delay = pacingDelay(botPace, remainingMs);
    if (delay > 0) await sleep(delay);
    return chosen.uci;
  }

  /** Cancel the active search and every queued job. */
  stop() {
    for (const job of this.jobs) job.resolve(null);
    this.jobs = [];
    if (this.current) {
      this.current.stopped = true;
      this._send("stop");
    }
  }

  dispose() {
    this.stop();
    if (this.worker) this.worker.terminate();
    this.worker = null;
    this.readyPromise = null;
    this._setStatus("idle");
  }
}
