// Which Linux password store Chromium should use, before the app is ready.
//
// Chromium picks the store from the desktop's name. Sessions it does not know
// (Hyprland, sway, i3, ...) silently get basic_text, and Electron's safeStorage
// then reports encryption as unavailable even when a keyring is running. Choose
// the keyring that is actually there instead. KDE is left alone: Chromium knows
// it and starts the right KWallet itself, and guessing another keyring there
// could tie the key to one a later KDE session does not run.
//
// An explicit --password-store from the person launching the app always wins
// (main.js checks for it first).

const fs = require("node:fs");
const path = require("node:path");

const KDE = /kde|plasma/i;

function choosePasswordStore({
  platform = process.platform,
  env = process.env,
  exists = fs.existsSync,
  uid = process.getuid?.(),
} = {}) {
  if (platform !== "linux") return null;

  const desktop = [env.XDG_CURRENT_DESKTOP, env.XDG_SESSION_DESKTOP, env.DESKTOP_SESSION]
    .filter(Boolean)
    .join(":");
  if (KDE.test(desktop)) return null;

  const runtime = env.XDG_RUNTIME_DIR || (uid === undefined ? "" : `/run/user/${uid}`);
  if (!runtime) return null;

  // gnome-keyring's control socket is made by the daemon itself. KWallet's
  // socket is named after its version; the name without one is KWallet 4.
  if (exists(path.join(runtime, "keyring", "control"))) return "gnome-libsecret";
  if (exists(path.join(runtime, "kwalletd6"))) return "kwallet6";
  if (exists(path.join(runtime, "kwalletd5"))) return "kwallet5";
  if (exists(path.join(runtime, "kwalletd"))) return "kwallet";
  return null;
}

module.exports = { choosePasswordStore };
