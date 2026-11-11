// Which hosted workspace this browser last worked in, so things kept in browser storage (unsent drafts, the last
// open page) stay with that workspace and never leak into another company's. Read synchronously from storage:
// the active workspace is only known to the server (an HttpOnly cookie), and drafts load before any request returns.
// Empty everywhere that is not a hosted workspace (desktop app, self-hosting), which keeps the old keys.

const KEY = "suparagent:workspace";
const LAST_PREFIX = "suparagent:last-page:";
const RECENT_KEY = "suparagent:recent-workspaces";
const RECENT_MAX = 8;

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function defaultStore(): Store | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

// Ids are control-plane workspace ids (u_ plus hex). Anything else is ignored rather than put into a storage key.
const ID = /^[A-Za-z0-9_-]{1,64}$/;

export function workspaceScope(store: Store | null = defaultStore()): string {
  try {
    const value = store?.getItem(KEY) ?? "";
    return ID.test(value) ? value : "";
  } catch {
    return "";
  }
}

export function setWorkspaceScope(id: string, store: Store | null = defaultStore()): void {
  if (!store || !ID.test(id)) return;
  try {
    store.setItem(KEY, id);
  } catch {
    // storage blocked: drafts simply stay under the shared key
  }
}

// The page a person was on in a workspace, so coming back to it reopens the same conversation. Only paths inside
// the app are kept.
export function rememberPage(id: string, path: string, base: string, store: Store | null = defaultStore()): void {
  if (!store || !ID.test(id) || !(path === base || path.startsWith(`${base}/`)) || path.length > 512) return;
  try {
    store.setItem(LAST_PREFIX + id, path);
  } catch {
    // ignore
  }
}

export function lastPage(id: string, base: string, store: Store | null = defaultStore()): string | null {
  if (!store || !ID.test(id)) return null;
  try {
    const path = store.getItem(LAST_PREFIX + id);
    return path && (path === base || path.startsWith(`${base}/`)) && !path.includes("//") ? path : null;
  } catch {
    return null;
  }
}

export function recentWorkspaces(store: Store | null = defaultStore()): string[] {
  try {
    const parsed = JSON.parse(store?.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string" && ID.test(id)) : [];
  } catch {
    return [];
  }
}

export function noteRecent(id: string, store: Store | null = defaultStore()): void {
  if (!store || !ID.test(id)) return;
  try {
    const next = [id, ...recentWorkspaces(store).filter((other) => other !== id)].slice(0, RECENT_MAX);
    store.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // ignore
  }
}
