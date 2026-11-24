// In-app updates. Release builds are signed and notarized, so the app can replace
// itself: it asks electron-updater (GitHub releases) for a newer version,
// downloads it on request, and restarts into it. The electron-updater object is
// passed in so this state machine is testable without Electron or the network.
// It has the same surface as the assisted updater in ./updater.js.

const { describeError } = require("./updater");

const RELEASES_PAGE = "https://github.com/Anil-matcha/suparagent/releases/tag/";

function createAutoUpdater({ autoUpdater, currentVersion, beforeInstall = async () => {}, onState = () => {} }) {
  let state = { status: "idle", current: currentVersion };
  let busy = false;
  let manualCheck = false;

  const set = (patch) => {
    state = { ...state, ...patch };
    onState(state);
  };

  // Releases are published as GitHub pre-releases, and the user decides when to download.
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowPrerelease = true;
  autoUpdater.allowDowngrade = false;

  autoUpdater.on("update-available", (info) => {
    const version = String(info?.version ?? "");
    set({
      status: "available",
      version,
      notesUrl: version ? `${RELEASES_PAGE}v${version}` : undefined,
      canDownload: true,
      message: undefined,
      percent: undefined,
      checkedAt: Date.now(),
    });
  });
  autoUpdater.on("update-not-available", () => {
    set({ status: "idle", version: undefined, notesUrl: undefined, canDownload: undefined, message: manualCheck ? "You have the latest version." : undefined, checkedAt: Date.now() });
  });
  autoUpdater.on("download-progress", (progress) => {
    const percent = Math.min(99, Math.max(0, Math.floor(Number(progress?.percent) || 0)));
    if (percent !== state.percent) set({ status: "downloading", percent });
  });
  autoUpdater.on("update-downloaded", () => {
    set({ status: "downloaded", percent: 100, verified: true, message: undefined });
  });

  async function check({ manual = false } = {}) {
    if (busy || ["downloading", "downloaded", "installing"].includes(state.status)) return state;
    busy = true;
    manualCheck = manual;
    if (manual) set({ status: "checking", message: undefined });
    try {
      await autoUpdater.checkForUpdates();
    } catch (error) {
      // Background checks stay quiet (offline is normal); a click gets the reason.
      if (manual) set({ status: "error", message: describeError(error) });
      else if (state.status === "checking") set({ status: "idle" });
    } finally {
      busy = false;
    }
    return state;
  }

  async function download() {
    if (busy || !(state.status === "available" || (state.status === "error" && state.canDownload))) return state;
    busy = true;
    try {
      set({ status: "downloading", percent: 0, message: undefined });
      await autoUpdater.downloadUpdate();
    } catch (error) {
      set({ status: "error", percent: undefined, canDownload: true, message: describeError(error) });
    } finally {
      busy = false;
    }
    return state;
  }

  async function install() {
    if (busy || state.status !== "downloaded") return state;
    busy = true;
    try {
      set({ status: "installing", message: "Restarting to finish the update…" });
      await beforeInstall();
      autoUpdater.quitAndInstall(false, true);
    } catch (error) {
      set({ status: "error", message: describeError(error) });
      busy = false;
    }
    return state;
  }

  function reset() {
    if (state.status === "error" || state.status === "installing") set({ status: "idle", message: undefined, percent: undefined });
    return state;
  }

  return { check, download, install, reset, getState: () => state };
}

module.exports = { createAutoUpdater };
