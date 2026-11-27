// suparagent://chat?thread=<id> and ...?room=<id> open one chat or room (BL-118); suparagent://app/<id> opens a saved app
// (add ?window=1 for its own window). The link only picks a page
// inside the app's own backend origin: it never runs anything, and the page's own sign-in check still applies.
const SCHEME = "suparagent";

function parseId(raw) {
  return typeof raw === "string" && /^[1-9]\d{0,14}$/.test(raw) ? raw : null;
}

const APP_ID = /^sa_[A-Za-z0-9_-]{16}$/;
const APP_LINK_MAX = 256;
const CHAT_LINK_MAX = 200;

/**
 * What a desktop link asks for: `{ path }` for a page of the main window, plus `appWindowId` when the link is a
 * saved-app link with `?window=1` (it opens in its own window). Null if it is not exactly one of the known links.
 */
function deepLinkTarget(raw) {
  if (typeof raw !== "string" || raw.length > APP_LINK_MAX) return null;
  const trimmed = raw.trim();
  let url;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== `${SCHEME}:` || url.username || url.password || url.port || url.hash) return null;
  if (url.hostname === "app") {
    const match = /^\/([^/]+)\/?$/.exec(url.pathname);
    if (!match || !APP_ID.test(match[1])) return null;
    if (url.search === "") return { path: `/apps/view/?id=${match[1]}` };
    if (url.search === "?window=1") return { path: `/apps/view/?id=${match[1]}&window=1`, appWindowId: match[1] };
    return null;
  }
  if (url.hostname !== "chat" || trimmed.length > CHAT_LINK_MAX) return null;
  const thread = parseId(url.searchParams.get("thread"));
  if (thread) return { path: `/chat/?thread=${thread}` };
  const room = parseId(url.searchParams.get("room"));
  return room ? { path: `/chat/?room=${room}` } : null;
}

/** The in-app path for a desktop link, or null if it is not one of the known links. */
function deepLinkPath(raw) {
  return deepLinkTarget(raw)?.path ?? null;
}

/** The first desktop link in a command line (Windows and Linux pass the link as an argument). */
function deepLinkFromArgv(argv) {
  return (Array.isArray(argv) ? argv : []).find((arg) => typeof arg === "string" && arg.startsWith(`${SCHEME}://`) && deepLinkPath(arg)) ?? null;
}

/**
 * Holds a link until the app can show it. `open(link)` shows it now if the window and backend are ready, else
 * keeps the newest one; `ready()` shows the kept one. `navigate(path)` loads a page in the main window;
 * `openAppWindow(id)` opens a saved app in its own window (it does not bring the main window forward).
 */
function createDeepLinkHandler({ navigate, focus, openAppWindow }) {
  let isReady = false;
  let pending = null;
  const show = (target) => {
    try {
      if (target.appWindowId && openAppWindow) {
        void Promise.resolve(openAppWindow(target.appWindowId)).catch(() => {});
        return;
      }
      focus?.();
      void Promise.resolve(navigate(target.path)).catch(() => {});
    } catch {
      // The window may be closing; nothing to show.
    }
  };
  return {
    open(link) {
      const target = deepLinkTarget(link);
      if (!target) return false;
      if (isReady) show(target);
      else pending = target;
      return true;
    },
    ready() {
      isReady = true;
      if (pending) {
        const target = pending;
        pending = null;
        show(target);
      }
    },
  };
}

module.exports = { SCHEME, APP_ID, deepLinkPath, deepLinkTarget, deepLinkFromArgv, createDeepLinkHandler };
