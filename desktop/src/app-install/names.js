// Names for the launchers of saved apps: the file name a person sees in Applications, the Start Menu or a
// launcher grid. Pure functions, no file access.
const crypto = require("node:crypto");

const APP_ID = /^sa_[A-Za-z0-9_-]{16}$/;
const BUNDLE_PREFIX = "app.suparagent.desktop.localapp.";
const MAX_GRAPHEMES = 64;
const MAX_BYTES = 180;
const MAX_SUFFIX = 99;
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
// Control characters, bidi controls, zero-width and byte-order characters, and the characters Windows forbids.
const UNSAFE = /[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁠-⁤⁦-⁯﻿\\/:*?"<>|]/gu;

let segmenter = null;
function graphemes(text) {
  segmenter ??= new Intl.Segmenter(undefined, { granularity: "grapheme" });
  return Array.from(segmenter.segment(text), (part) => part.segment);
}

/** First 16 hex characters of sha256("suparagent-local-app\n" + id): the stable key of one saved app. */
function appKey(id) {
  return crypto.createHash("sha256").update(`suparagent-local-app\n${id}`).digest("hex").slice(0, 16);
}

function appUserModelId(id) {
  return `${BUNDLE_PREFIX}${appKey(id)}`;
}

function appLink(id, { window = true } = {}) {
  return `suparagent://app/${id}${window ? "?window=1" : ""}`;
}

/** Cuts a list of graphemes to the limits, never splitting a character. */
function fit(parts, maxGraphemes, maxBytes) {
  const kept = [];
  let bytes = 0;
  for (const part of parts) {
    const size = Buffer.byteLength(part, "utf8");
    if (kept.length >= maxGraphemes || bytes + size > maxBytes) break;
    kept.push(part);
    bytes += size;
  }
  return kept.join("");
}

/** A safe launcher name (without extension) from a saved app's name. */
function launcherName(name, { reserve = 0 } = {}) {
  let text = typeof name === "string" ? name.normalize("NFC") : "";
  text = text.replace(UNSAFE, "").replace(/\s+/g, " ").trim();
  text = text.replace(/^\.+/, "").replace(/[. ]+$/, "").trim();
  text = fit(graphemes(text), MAX_GRAPHEMES - reserve, MAX_BYTES - reserve);
  text = text.replace(/[. ]+$/, "");
  if (!text) return "App";
  // Windows treats "con", "con.txt" and "CON " alike, so look at the part before the first dot.
  if (RESERVED.test(text.split(".")[0].trim())) text = `${text} App`;
  return text;
}

/** Case- and accent-insensitive form used to compare names on file systems that fold them. */
function foldName(text) {
  return String(text).normalize("NFD").toLowerCase();
}

/**
 * Picks the name to use among `taken` (names already in the folder, without extension). A name is free if no
 * taken name folds to the same text, or if `owned(candidate)` says the existing launcher is this app's own.
 * Collisions get " 2" to " 99".
 */
function chooseLauncherName(name, taken, owned = () => false) {
  const base = launcherName(name);
  const shorter = launcherName(name, { reserve: 3 });
  const folded = new Set(Array.from(taken, foldName));
  for (let n = 1; n <= MAX_SUFFIX; n += 1) {
    const candidate = n === 1 ? base : `${shorter} ${n}`;
    if (!folded.has(foldName(candidate)) || owned(candidate)) return candidate;
  }
  throw new Error("Too many launchers share this name.");
}

module.exports = { APP_ID, BUNDLE_PREFIX, appKey, appUserModelId, appLink, launcherName, foldName, chooseLauncherName, graphemes };
