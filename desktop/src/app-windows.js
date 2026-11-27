// A saved app in its own window (suparagent://app/<id>?window=1). It is a second window onto the same local
// backend: same preload, same token-injecting session and the same navigation guards as the main window. It
// is not the main window: closing it never quits the app, and the tray and "all windows closed" logic in
// main.js treat it as an ordinary extra window.
const nodePath = require("node:path");
const { attachTokenTo, lockDown } = require("./origin");
const { APP_ID, appUserModelId } = require("./app-install/names");

const DEFAULT_SIZE = { width: 1100, height: 760 };
const MIN_SIZE = { width: 420, height: 360 };
const MAX_REMEMBERED = 100;
const SIZES_FILE = "app-window-sizes.json";
const RESIZE_SAVE_MS = 400;

/** A window size kept within the minimum and the screen's usable area. */
function clampSize(size, workArea) {
  const limit = (value, min, max, fallback) => {
    const number = Number.isFinite(value) ? Math.round(value) : fallback;
    return Math.max(min, Math.min(number, Math.max(min, max)));
  };
  return {
    width: limit(size?.width, MIN_SIZE.width, workArea?.width ?? Infinity, DEFAULT_SIZE.width),
    height: limit(size?.height, MIN_SIZE.height, workArea?.height ?? Infinity, DEFAULT_SIZE.height),
  };
}

/** Remembered window sizes per saved app: the newest 100 are kept, written atomically with mode 0600. */
function createSizeStore({ fs, dir, now = () => Date.now(), limit = MAX_REMEMBERED }) {
  const file = nodePath.join(dir, SIZES_FILE);
  const read = () => {
    try {
      const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
      if (!parsed || parsed.version !== 1 || !parsed.sizes || typeof parsed.sizes !== "object") return {};
      const sizes = {};
      for (const [id, value] of Object.entries(parsed.sizes)) {
        if (APP_ID.test(id) && Number.isFinite(value?.width) && Number.isFinite(value?.height)) {
          sizes[id] = { width: value.width, height: value.height, updatedAt: Number.isFinite(value.updatedAt) ? value.updatedAt : 0 };
        }
      }
      return sizes;
    } catch {
      return {};
    }
  };
  return {
    get: (id) => read()[id] ?? null,
    set(id, size) {
      if (!APP_ID.test(id) || !Number.isFinite(size?.width) || !Number.isFinite(size?.height)) return;
      const sizes = read();
      sizes[id] = { width: Math.round(size.width), height: Math.round(size.height), updatedAt: now() };
      const newest = Object.entries(sizes).sort((a, b) => b[1].updatedAt - a[1].updatedAt).slice(0, limit);
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      const temporary = `${file}.${process.pid}.tmp`;
      try {
        fs.writeFileSync(temporary, JSON.stringify({ version: 1, sizes: Object.fromEntries(newest) }), { mode: 0o600 });
        fs.chmodSync(temporary, 0o600);
        fs.renameSync(temporary, file);
      } catch {
        try {
          fs.rmSync(temporary, { force: true });
        } catch {
          /* nothing to clean */
        }
      }
    },
  };
}

function createAppWindows({ BrowserWindow, screen, fs, userData, preloadPath, getOrigin, getToken, platform = process.platform, iconPath, onWebContents }) {
  const store = createSizeStore({ fs, dir: userData });
  const open = new Map();

  function create(id, origin) {
    const workArea = screen?.getPrimaryDisplay?.().workAreaSize;
    const size = clampSize(store.get(id) ?? DEFAULT_SIZE, workArea);
    const win = new BrowserWindow({
      ...size,
      minWidth: MIN_SIZE.width,
      minHeight: MIN_SIZE.height,
      title: "Suparagent",
      backgroundColor: "#ffffff",
      ...(iconPath ? { icon: iconPath } : {}),
      webPreferences: {
        preload: preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    const contents = win.webContents;
    const token = getToken();
    if (token) attachTokenTo(contents.session, origin, token);
    lockDown(contents, origin, contents.session);
    onWebContents?.(contents, win);
    if (platform === "win32" && typeof win.setAppDetails === "function") {
      const appIconPath = nodePath.join(userData, "app-icons", `suparagent-app-${id}.ico`);
      try {
        win.setAppDetails({ appId: appUserModelId(id), ...(fs.existsSync(appIconPath) ? { appIconPath, appIconIndex: 0 } : {}) });
      } catch {
        // The taskbar grouping is a nicety.
      }
    }
    const remember = () => {
      try {
        const bounds = typeof win.getNormalBounds === "function" ? win.getNormalBounds() : win.getBounds();
        store.set(id, bounds);
      } catch {
        // Closing: no size to keep.
      }
    };
    // Also keep the size a moment after each resize, so a window that goes away without a close event (a crash, a forced
    // quit, a close from inside the page) still comes back at the size it had.
    let resizeTimer = null;
    win.on("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(remember, RESIZE_SAVE_MS);
    });
    win.on("close", () => {
      clearTimeout(resizeTimer);
      remember();
    });
    win.on("closed", () => {
      clearTimeout(resizeTimer);
      if (open.get(id) === win) open.delete(id);
    });
    open.set(id, win);
    void Promise.resolve(win.loadURL(`${origin}/apps/view/?id=${id}&window=1`)).catch(() => {});
    return win;
  }

  return {
    /** Opens the window for a saved app, or brings the one that is already open forward. */
    open(id) {
      const origin = getOrigin();
      if (typeof id !== "string" || !APP_ID.test(id) || !origin) return false;
      const existing = open.get(id);
      if (existing && !existing.isDestroyed()) {
        if (existing.isMinimized()) existing.restore();
        if (!existing.isVisible()) existing.show();
        existing.focus();
        return true;
      }
      create(id, origin);
      return true;
    },
    has: (id) => Boolean(open.get(id) && !open.get(id).isDestroyed()),
    closeAll() {
      for (const win of [...open.values()]) {
        try {
          if (!win.isDestroyed()) win.destroy();
        } catch {
          /* already gone */
        }
      }
      open.clear();
    },
    isAppWindow: (win) => [...open.values()].includes(win),
  };
}

module.exports = { DEFAULT_SIZE, MIN_SIZE, MAX_REMEMBERED, clampSize, createSizeStore, createAppWindows };
