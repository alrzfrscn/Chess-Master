/**
 * Bridge client.
 *
 * In the desktop window the Python object is exposed as
 * `window.pywebview.api`. During development the same envelope is available
 * over `POST /api/<method>`; this module hides the difference from the rest of
 * the UI.
 */

export class ChessMasterError extends Error {
  constructor({ code = "unknown_error", message = "Something went wrong.", details = null } = {}) {
    super(message);
    this.name = "ChessMasterError";
    this.code = code;
    this.details = details;
  }
}

export class BridgeUnavailableError extends Error {
  constructor(message = "The Python bridge did not answer.") {
    super(message);
    this.name = "BridgeUnavailableError";
    this.code = "bridge_unavailable";
    this.details = null;
  }
}

const bridge = {
  mode: null,
  info: null,
};

const isDesktop = new URLSearchParams(window.location.search).get("desktop") === "1";

const CLIENT_KEY = "chess_master_client_id";
const SESSION_KEY = "chess_master_session_id";

function randomToken() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID().replace(/-/g, "");
  }
  const bytes = new Uint8Array(16);
  if (window.crypto && typeof window.crypto.getRandomValues === "function") {
    window.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.random() * 256;
  }
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function storedToken(storage, key) {
  try {
    const existing = storage.getItem(key);
    if (existing) return existing;
    const created = randomToken();
    storage.setItem(key, created);
    return created;
  } catch {
    return randomToken();
  }
}

/** Stable per browser; archive scoping and share links key off this. */
export const clientId = storedToken(window.localStorage, CLIENT_KEY);
/** Per tab, so two tabs never share a board. */
export const sessionId = storedToken(window.sessionStorage, SESSION_KEY);

function sessionHeaders(extra = {}) {
  return {
    Accept: "application/json",
    "X-Chess-Session": sessionId,
    "X-Chess-Client": clientId,
    ...extra,
  };
}

export function bridgeMode() {
  return bridge.mode;
}

export function bridgeInfo() {
  return bridge.info;
}

function nativeApi() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

function waitForNative(timeoutMs = 6000) {
  if (nativeApi()) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("pywebviewready", onReady);
      clearInterval(poll);
      clearTimeout(timer);
      resolve(value);
    };
    const onReady = () => finish(Boolean(nativeApi()));
    const poll = setInterval(() => {
      if (nativeApi()) finish(true);
    }, 40);
    const timer = setTimeout(() => finish(Boolean(nativeApi())), timeoutMs);
    window.addEventListener("pywebviewready", onReady);
  });
}

async function httpHealth() {
  const response = await fetch("/api/health", {
    method: "GET",
    headers: sessionHeaders({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Health check failed (${response.status}).`);
  return unwrap(await response.json());
}

/**
 * Pick the native bridge when present, otherwise use the local development
 * HTTP API. `desktop=1` is added by the PyWebView window so the page never
 * races the bridge injection on first paint.
 */
export async function connect() {
  if (nativeApi()) {
    bridge.mode = "native";
    return bridge.mode;
  }

  if (isDesktop) {
    const ready = await waitForNative();
    if (ready) {
      bridge.mode = "native";
      return bridge.mode;
    }
  }

  try {
    bridge.info = await httpHealth();
    bridge.mode = "http";
    return bridge.mode;
  } catch (error) {
    throw new BridgeUnavailableError(
      isDesktop
        ? "The desktop bridge did not start. Close this window and relaunch Chess Master."
        : "The local API is not available. Start the app with --serve for development.",
    );
  }
}

function unwrap(payload) {
  if (!payload || typeof payload !== "object") {
    throw new ChessMasterError({
      code: "invalid_response",
      message: "The bridge returned an unreadable response.",
    });
  }
  if (payload.ok === true) return payload.data;
  const error = payload.error || {};
  throw new ChessMasterError({
    code: error.code || "bridge_error",
    message: error.message || "The bridge rejected the request.",
    details: error.details ?? null,
  });
}

async function httpCall(method, args) {
  const response = await fetch(`/api/${encodeURIComponent(method)}`, {
    method: "POST",
    headers: sessionHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ args, kwargs: {} }),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ChessMasterError({
      code: "http_error",
      message: `The local API returned HTTP ${response.status}.`,
    });
  }
  return unwrap(await response.json());
}

/**
 * Invoke an API method. Always resolves with the `data` payload and throws a
 * `ChessMasterError` carrying a stable code and details on failure.
 */
export async function call(method, ...args) {
  const api = nativeApi();
  if (api && typeof api[method] === "function") {
    let payload;
    try {
      payload = await api[method](...args);
    } catch (error) {
      throw new ChessMasterError({
        code: "bridge_failure",
        message: error && error.message ? error.message : `The bridge could not run ${method}.`,
      });
    }
    return unwrap(payload);
  }

  if (bridge.mode === "http" || !isDesktop) {
    return httpCall(method, args);
  }

  throw new BridgeUnavailableError();
}

export function errorMessage(error) {
  if (!error) return "Something went wrong.";
  if (error.message) return error.message;
  if (typeof error === "string") return error;
  return "Something went wrong.";
}

export function detailLines(error) {
  const details = error && error.details;
  if (!details) return "";
  if (Array.isArray(details.errors)) return details.errors.slice(0, 3).join(" · ");
  if (typeof details.reason === "string") return details.reason;
  if (typeof details.ply === "number") return `Ply ${details.ply}`;
  return "";
}

/**
 * Subscribe to the server's state fingerprint. Returns an unsubscribe function.
 * Desktop and browsers without EventSource simply poll as before.
 */
export function subscribeEvents(handler) {
  if (bridge.mode !== "http" || typeof window.EventSource !== "function") {
    return () => {};
  }
  let source;
  try {
    source = new EventSource(`/api/events?session=${encodeURIComponent(sessionId)}`);
  } catch {
    return () => {};
  }
  source.addEventListener("state", (event) => {
    try {
      handler(JSON.parse(event.data));
    } catch {
      // A malformed frame only costs one fallback poll.
    }
  });
  return () => source.close();
}

/** The archived game id a share link pointed at, if any. */
export function requestedGameId() {
  return new URLSearchParams(window.location.search).get("game");
}

/** Absolute share URL for an archived game, on whatever origin serves the page. */
export function shareGameUrl(gameId) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("game", String(gameId));
  return url.toString();
}
