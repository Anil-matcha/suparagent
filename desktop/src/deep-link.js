// opendots://chat?thread=<id> and ...?room=<id> open one chat or room (BL-118). The link only picks a page
// inside the app's own backend origin: it never runs anything, and the page's own sign-in check still applies.
const SCHEME = "opendots";

function parseId(raw) {
  return typeof raw === "string" && /^[1-9]\d{0,14}$/.test(raw) ? raw : null;
}

/** The in-app path for a desktop link, or null if it is not exactly a chat or room link. */
function deepLinkPath(raw) {
  if (typeof raw !== "string" || raw.length > 200) return null;
  let url;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol !== `${SCHEME}:` || url.hostname !== "chat" || url.username || url.password || url.port) return null;
  const thread = parseId(url.searchParams.get("thread"));
  if (thread) return `/chat/?thread=${thread}`;
  const room = parseId(url.searchParams.get("room"));
  return room ? `/chat/?room=${room}` : null;
}

/** The first desktop link in a command line (Windows and Linux pass the link as an argument). */
function deepLinkFromArgv(argv) {
  return (Array.isArray(argv) ? argv : []).find((arg) => typeof arg === "string" && arg.startsWith(`${SCHEME}://`) && deepLinkPath(arg)) ?? null;
}

/**
 * Holds a link until the app can show it. `open(link)` shows it now if the window and backend are ready, else
 * keeps the newest one; `ready()` shows the kept one. `navigate(path)` loads a page in the main window.
 */
function createDeepLinkHandler({ navigate, focus }) {
  let isReady = false;
  let pending = null;
  const show = (path) => {
    try {
      focus?.();
      void Promise.resolve(navigate(path)).catch(() => {});
    } catch {
      // The window may be closing; nothing to show.
    }
  };
  return {
    open(link) {
      const path = deepLinkPath(link);
      if (!path) return false;
      if (isReady) show(path);
      else pending = path;
      return true;
    },
    ready() {
      isReady = true;
      if (pending) {
        const path = pending;
        pending = null;
        show(path);
      }
    },
  };
}

module.exports = { SCHEME, deepLinkPath, deepLinkFromArgv, createDeepLinkHandler };
