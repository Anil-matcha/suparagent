const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");

const { choosePasswordStore } = require("../src/password-store");

const runtime = "/run/user/1000";
const inRuntime = (name) => path.join(runtime, name);

// `files` are the runtime-folder entries that exist; nothing touches the disk.
function choose({ desktop, files = [], env = {}, ...options } = {}) {
  const present = new Set(files);
  return choosePasswordStore({
    platform: "linux",
    env: { XDG_RUNTIME_DIR: runtime, ...(desktop ? { XDG_CURRENT_DESKTOP: desktop } : {}), ...env },
    exists: (file) => present.has(file),
    uid: 1000,
    ...options,
  });
}

test("Windows and macOS have nothing to choose", () => {
  for (const platform of ["win32", "darwin"]) {
    assert.equal(choose({ platform, files: [inRuntime("keyring/control")] }), null);
  }
});

test("a running GNOME Keyring is chosen on a desktop Chromium does not know", () => {
  assert.equal(choose({ desktop: "Hyprland", files: [inRuntime("keyring/control")] }), "gnome-libsecret");
  assert.equal(choose({ desktop: "sway", files: [inRuntime("keyring/control")] }), "gnome-libsecret");
});

test("KWallet is chosen by the version its socket names", () => {
  assert.equal(choose({ desktop: "Hyprland", files: [inRuntime("kwalletd6")] }), "kwallet6");
  assert.equal(choose({ desktop: "sway", files: [inRuntime("kwalletd5")] }), "kwallet5");
  assert.equal(choose({ desktop: "i3", files: [inRuntime("kwalletd")] }), "kwallet");
});

test("GNOME Keyring wins over KWallet outside KDE", () => {
  assert.equal(choose({ desktop: "Hyprland", files: [inRuntime("keyring/control"), inRuntime("kwalletd6")] }), "gnome-libsecret");
});

test("KDE is left to Chromium, which starts the right KWallet itself", () => {
  assert.equal(choose({ desktop: "KDE", files: [inRuntime("keyring/control"), inRuntime("kwalletd6")] }), null);
  assert.equal(choose({ desktop: "plasma", files: [inRuntime("keyring/control")] }), null);
  assert.equal(choose({ env: { XDG_SESSION_DESKTOP: "kde6" }, files: [inRuntime("keyring/control")] }), null);
});

test("with no keyring running there is no choice to make", () => {
  assert.equal(choose({ desktop: "Hyprland" }), null);
});

test("the runtime folder falls back to /run/user/<uid>", () => {
  const files = [path.join("/run/user/1000", "keyring", "control")];
  const env = { XDG_RUNTIME_DIR: "" };
  assert.equal(choose({ desktop: "Hyprland", env, files }), "gnome-libsecret");
});

test("without a runtime folder and a uid there is nothing to look at", () => {
  assert.equal(choose({ env: { XDG_RUNTIME_DIR: "" }, uid: undefined }), null);
});
