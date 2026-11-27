const assert = require("node:assert/strict");
const { test } = require("node:test");
const { launcherName, chooseLauncherName, appKey, appUserModelId, appLink, graphemes } = require("../src/app-install/names");
const { decodeIconPng, letterTilePng, parsePng, icoFromPng, tileLetter, chooseIconPng, encodePng } = require("../src/app-install/icons");

test("names drop path, control, bidi and zero-width characters", () => {
  assert.equal(launcherName('a\\b/c:d*e?f"g<h>i|j'), "abcdefghij");
  assert.equal(launcherName("Hi\u0000\u0007 there\n"), "Hi there");
  assert.equal(launcherName("a‮b​c⁦d﻿"), "abcd");
  assert.equal(launcherName("Café"), "Café"); // NFC
});

test("leading dots, trailing dots and spaces go; empty becomes App", () => {
  assert.equal(launcherName("..hidden"), "hidden");
  assert.equal(launcherName("name. . "), "name");
  assert.equal(launcherName("  "), "App");
  assert.equal(launcherName("///"), "App");
  assert.equal(launcherName("..."), "App");
  assert.equal(launcherName(undefined), "App");
});

test("Windows reserved device names are not used as they are", () => {
  for (const name of ["CON", "nul", "Com1", "LPT9", "aux.txt", "PRN "]) {
    assert.ok(launcherName(name).endsWith(" App"));
  }
  assert.equal(launcherName("Console"), "Console");
  assert.equal(launcherName("COM10"), "COM10");
});

test("names are at most 64 graphemes and 180 bytes without splitting a character", () => {
  assert.equal(graphemes(launcherName("x".repeat(200))).length, 64);
  const family = "\u{1F468}\u{1F3FD}"; // one grapheme (emoji plus skin tone), 8 bytes
  const long = launcherName(family.repeat(70)); // zero-width joiners are removed, the rest is cut by whole characters
  assert.ok(Buffer.byteLength(long) <= 180);
  assert.equal(long.length % family.length, 0);
  assert.ok(graphemes(long).every((g) => g === family));
  const wide = launcherName("漢".repeat(100));
  assert.ok(Buffer.byteLength(wide) <= 180);
  assert.equal(graphemes(wide).length, 60);
});

test("collisions get 2 to 99, compared without case or accents", () => {
  assert.equal(chooseLauncherName("Notes", []), "Notes");
  assert.equal(chooseLauncherName("Notes", ["NOTES"]), "Notes 2");
  assert.equal(chooseLauncherName("Notes", ["notes", "Notes 2"]), "Notes 3");
  assert.equal(chooseLauncherName("Café", ["Café"]), "Café 2");
  assert.equal(chooseLauncherName("Cafe", ["Caf\u00e9"]), "Cafe"); // an accented name is a different name
  const many = ["Notes", ...Array.from({ length: 98 }, (_, i) => `Notes ${i + 2}`)];
  assert.throws(() => chooseLauncherName("Notes", many));
});

test("a launcher is reused only when it is this app's own", () => {
  assert.equal(chooseLauncherName("Notes", ["Notes"], (c) => c === "Notes"), "Notes");
  assert.equal(chooseLauncherName("Notes", ["Notes", "Notes 2"], (c) => c === "Notes 2"), "Notes 2");
  assert.equal(chooseLauncherName("Notes", ["Notes"], () => false), "Notes 2");
});

test("suffixed names stay inside the limits", () => {
  const taken = ["x".repeat(64)];
  const picked = chooseLauncherName("x".repeat(100), taken);
  assert.ok(graphemes(picked).length <= 64);
  assert.ok(picked.endsWith(" 2"));
});

test("key, bundle id and link are stable", () => {
  const id = "sa_AbCdEf0123456789";
  assert.match(appKey(id), /^[0-9a-f]{16}$/);
  assert.equal(appKey(id), appKey(id));
  assert.notEqual(appKey(id), appKey("sa_AbCdEf012345678A"));
  assert.equal(appUserModelId(id), `app.suparagent.desktop.localapp.${appKey(id)}`);
  assert.equal(appLink(id), `suparagent://app/${id}?window=1`);
  assert.equal(appLink(id, { window: false }), `suparagent://app/${id}`);
});

test("the letter tile is a valid PNG, coloured from the id, and fits an .ico", () => {
  const a = letterTilePng("sa_AbCdEf0123456789", "Zed");
  assert.deepEqual(parsePng(a), { width: 256, height: 256 });
  assert.deepEqual(letterTilePng("sa_AbCdEf0123456789", "Zed"), a);
  assert.notDeepEqual(letterTilePng("sa_ZZZZZZZZZZZZZZZZ", "Zed"), a);
  assert.notDeepEqual(letterTilePng("sa_AbCdEf0123456789", "Quill"), a);
  const ico = icoFromPng(a);
  assert.equal(ico.readUInt16LE(2), 1);
  assert.equal(ico[6], 0);
  assert.equal(ico.readUInt32LE(14), a.length);
  assert.ok(ico.subarray(22).equals(a));
  assert.equal(tileLetter("éclair"), "E");
  assert.equal(tileLetter("漢字"), "#");
  assert.equal(tileLetter("  9 lives"), "9");
});

test("supplied icons are validated", () => {
  const tile = letterTilePng("sa_AbCdEf0123456789", "A");
  assert.ok(decodeIconPng(tile.toString("base64")).equals(tile));
  for (const bad of ["", "not base64!", Buffer.from("hello world, not a png at all, no").toString("base64"), "A".repeat(2_796_208), 5, null]) {
    assert.throws(() => decodeIconPng(bad), String(bad).slice(0, 20));
  }
  const corrupt = Buffer.from(tile);
  corrupt[40] ^= 0xff;
  assert.throws(() => parsePng(corrupt));
  assert.throws(() => parsePng(tile.subarray(0, tile.length - 5)));
  assert.equal(chooseIconPng({ id: "sa_AbCdEf0123456789", name: "A", iconPng: corrupt }).generated, true);
  assert.equal(chooseIconPng({ id: "sa_AbCdEf0123456789", name: "A", iconPng: tile }).generated, false);
  const big = encodePng(300, 300, Buffer.alloc(300 * 300 * 4));
  assert.throws(() => icoFromPng(big));
});
