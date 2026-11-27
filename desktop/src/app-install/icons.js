// Icons for launchers, with no native dependencies: validates a supplied PNG, or draws a rounded coloured
// letter tile when there is none, and wraps a PNG as an .ico for Windows shortcuts.
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const { graphemes } = require("./names");

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const MAX_ICON_BYTES = 2 * 1024 * 1024;
const MAX_BASE64_CHARS = Math.ceil(MAX_ICON_BYTES / 3) * 4;
const MAX_DIMENSION = 4096;
const TILE_SIZE = 256;

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** Checks the PNG structure (signature, chunk lengths and checksums, IHDR first, IEND last). */
function parsePng(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 33 || buffer.length > MAX_ICON_BYTES) throw new Error("The icon is not a valid PNG.");
  if (!buffer.subarray(0, 8).equals(SIGNATURE)) throw new Error("The icon is not a valid PNG.");
  let offset = 8;
  let first = true;
  let size = null;
  while (offset + 12 <= buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("latin1", offset + 4, offset + 8);
    const end = offset + 12 + length;
    if (end > buffer.length) break;
    if (crc32(buffer.subarray(offset + 4, offset + 8 + length)) !== buffer.readUInt32BE(offset + 8 + length)) break;
    if (first) {
      if (type !== "IHDR" || length !== 13) break;
      size = { width: buffer.readUInt32BE(offset + 8), height: buffer.readUInt32BE(offset + 12) };
      first = false;
    }
    if (type === "IEND") {
      const ok = end === buffer.length && size && size.width > 0 && size.height > 0 && size.width <= MAX_DIMENSION && size.height <= MAX_DIMENSION;
      if (ok) return size;
      break;
    }
    offset = end;
  }
  throw new Error("The icon is not a valid PNG.");
}

/** Decodes the base64 text the page sends; throws unless it is a valid PNG of at most 2 MB. */
function decodeIconPng(text) {
  if (typeof text !== "string" || text.length === 0 || text.length > MAX_BASE64_CHARS) throw new Error("The icon is too large or not a PNG.");
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(text) || text.length % 4 !== 0) throw new Error("The icon is not valid base64.");
  const buffer = Buffer.from(text, "base64");
  parsePng(buffer);
  return buffer;
}

// 5x7 letters and digits.
const GLYPHS = {
  A: ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
  B: ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
  C: ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
  D: ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
  E: ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
  F: ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
  G: ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
  H: ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
  I: ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
  J: ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
  K: ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
  L: ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
  M: ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
  N: ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
  O: ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
  P: ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
  Q: ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
  R: ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
  S: ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
  T: ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
  U: ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
  V: ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
  W: ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
  X: ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
  Y: ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
  Z: ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
  0: ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
  1: ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
  2: ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
  3: ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
  4: ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
  5: ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
  6: ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
  7: ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
  8: ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
  9: ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
  "#": ["01010", "01010", "11111", "01010", "11111", "01010", "01010"],
};

/** The character drawn on the tile: the first letter or digit of the name (accents dropped), else "#". */
function tileLetter(name) {
  for (const part of graphemes(typeof name === "string" ? name : "")) {
    const plain = part.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();
    if (/^[A-Z0-9]$/.test(plain)) return plain;
  }
  return "#";
}

function hslToRgb(h, s, l) {
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

/** The tile colour for an app, always the same for the same id. */
function tileColor(id) {
  const digest = crypto.createHash("sha256").update(`suparagent-tile\n${id}`).digest();
  return hslToRgb(digest.readUInt16BE(0) % 360, 0.55, 0.42);
}

function encodePng(width, height, rgba) {
  const rows = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y += 1) rgba.copy(rows, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  const chunk = (type, data) => {
    const head = Buffer.alloc(8);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, "latin1");
    const tail = Buffer.alloc(4);
    tail.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
    return Buffer.concat([head, data, tail]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  return Buffer.concat([SIGNATURE, chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(rows)), chunk("IEND", Buffer.alloc(0))]);
}

/** A 256x256 rounded tile in the app's colour with the first letter of its name in white. */
function letterTilePng(id, name) {
  const size = TILE_SIZE;
  const radius = size * 0.22;
  const [r, g, b] = tileColor(id);
  const glyph = GLYPHS[tileLetter(name)];
  const cell = 24;
  const left = Math.floor((size - 5 * cell) / 2);
  const top = Math.floor((size - 7 * cell) / 2);
  const rgba = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      // Distance outside the rounded square (negative inside) gives a soft one-pixel edge.
      const dx = Math.max(Math.abs(x + 0.5 - size / 2) - (size / 2 - radius), 0);
      const dy = Math.max(Math.abs(y + 0.5 - size / 2) - (size / 2 - radius), 0);
      const coverage = Math.max(0, Math.min(1, radius - Math.hypot(dx, dy) + 0.5));
      if (coverage === 0) continue;
      const gx = Math.floor((x - left) / cell);
      const gy = Math.floor((y - top) / cell);
      const ink = gx >= 0 && gx < 5 && gy >= 0 && gy < 7 && glyph[gy][gx] === "1";
      const at = (y * size + x) * 4;
      rgba[at] = ink ? 255 : r;
      rgba[at + 1] = ink ? 255 : g;
      rgba[at + 2] = ink ? 255 : b;
      rgba[at + 3] = Math.round(coverage * 255);
    }
  }
  return encodePng(size, size, rgba);
}

/** The PNG to use: the supplied one if it is a valid PNG, else the letter tile. */
function chooseIconPng({ id, name, iconPng }) {
  if (iconPng) {
    try {
      parsePng(iconPng);
      return { png: iconPng, generated: false };
    } catch {
      // Fall through to the tile.
    }
  }
  return { png: letterTilePng(id, name), generated: true };
}

/** An .ico holding one PNG image (Windows reads PNG inside .ico). Sizes above 256 are not valid there. */
function icoFromPng(png) {
  const { width, height } = parsePng(png);
  if (width > 256 || height > 256) throw new Error("An .ico image can be at most 256 pixels.");
  const header = Buffer.alloc(22);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2); // icon
  header.writeUInt16LE(1, 4); // one image
  header[6] = width === 256 ? 0 : width;
  header[7] = height === 256 ? 0 : height;
  header.writeUInt16LE(1, 10); // planes
  header.writeUInt16LE(32, 12); // bits per pixel
  header.writeUInt32LE(png.length, 14);
  header.writeUInt32LE(22, 18);
  return Buffer.concat([header, png]);
}

module.exports = { MAX_ICON_BYTES, parsePng, decodeIconPng, letterTilePng, tileLetter, tileColor, chooseIconPng, icoFromPng, encodePng };
