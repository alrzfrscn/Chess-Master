/**
 * Copies the exact browser builds of Chessground and chess.js into the Python
 * package, and extracts the cburnett piece SVGs for captured-piece strips and
 * the promotion chooser. Run `npm run build` after changing the versions in
 * package.json.
 */
import { mkdir, readFile, writeFile, copyFile, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = path.join(root, "src", "chess_master", "web");
const vendor = path.join(web, "assets", "vendor");
const pieces = path.join(web, "assets", "pieces");

const chessground = path.join(root, "node_modules", "chessground");
const chessjs = path.join(root, "node_modules", "chess.js");
const stockfish = path.join(root, "node_modules", "stockfish", "bin");

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

async function copy(from, to) {
  await mkdir(path.dirname(to), { recursive: true });
  await copyFile(from, to);
  console.log(`  ${path.relative(root, to)}`);
}

await mkdir(vendor, { recursive: true });
await mkdir(pieces, { recursive: true });

console.log("Copying Chessground");
await copy(path.join(chessground, "dist", "chessground.min.js"), path.join(vendor, "chessground", "chessground.min.js"));
await copy(path.join(chessground, "assets", "chessground.base.css"), path.join(vendor, "chessground", "chessground.base.css"));
await copy(path.join(chessground, "assets", "chessground.cburnett.css"), path.join(vendor, "chessground", "chessground.cburnett.css"));
if (await exists(path.join(chessground, "LICENSE"))) {
  await copy(path.join(chessground, "LICENSE"), path.join(vendor, "chessground", "LICENSE"));
}

console.log("Copying chess.js");
await copy(path.join(chessjs, "dist", "esm", "chess.js"), path.join(vendor, "chess.js", "chess.js"));
if (await exists(path.join(chessjs, "LICENSE"))) {
  await copy(path.join(chessjs, "LICENSE"), path.join(vendor, "chess.js", "LICENSE"));
}

// Stockfish 19 "lite single": one 1.8 MB NNUE build that runs in every modern
// browser without COOP/COEP SharedArrayBuffer headers. The worker script finds
// its wasm by replacing `.js` with `.wasm`, so both names must stay in step.
console.log("Copying Stockfish Lite WASM");
const engineJs = path.join(stockfish, "stockfish-19-lite-single.js");
const engineWasm = path.join(stockfish, "stockfish-19-lite-single.wasm");
for (const file of [engineJs, engineWasm]) {
  if (!(await exists(file))) {
    throw new Error(
      `Missing ${path.relative(root, file)}. Run \`npm install\` before \`npm run vendor\`.`,
    );
  }
}
await copy(engineJs, path.join(vendor, "stockfish", "stockfish-lite.js"));
await copy(engineWasm, path.join(vendor, "stockfish", "stockfish-lite.wasm"));
await copy(path.join(root, "node_modules", "stockfish", "Copying.txt"), path.join(vendor, "stockfish", "LICENSE"));

// Extract the embedded cburnett SVGs. These are the same source pieces used by
// the board itself, so captured pieces and promotion choices match exactly.
console.log("Extracting captured-piece SVGs");
const cburnett = await readFile(path.join(vendor, "chessground", "chessground.cburnett.css"), "utf8");
const roles = { pawn: "P", knight: "N", bishop: "B", rook: "R", queen: "Q", king: "K" };
const pattern = /piece\.(\w+)\.(white|black)\s*\{\s*background-image:\s*url\('data:image\/svg\+xml;base64,([^']+)'\)/g;
let match;
let count = 0;
while ((match = pattern.exec(cburnett)) !== null) {
  const [, role, color, base64] = match;
  const letter = roles[role];
  if (!letter) continue;
  const prefix = color === "white" ? "w" : "b";
  const svg = Buffer.from(base64, "base64").toString("utf8");
  const file = path.join(pieces, `${prefix}${letter}.svg`);
  await writeFile(file, svg, "utf8");
  count += 1;
}
console.log(`  ${count} piece SVGs`);

console.log("Vendor sync complete.");
