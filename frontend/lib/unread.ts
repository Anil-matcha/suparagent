// Which conversations have moved since the person last looked. The backend keeps no read
// state, so this is per browser: a last-seen time for each conversation, kept in localStorage.
// Limitation: it is not shared between devices, and a cleared browser starts everything as read.
// No runtime imports so node tests can load it.

export type SeenMap = Record<string, string>;
type ThreadLike = { id: number; updated_at: string; archived_at: string | null };

const KEY = "suparagent:thread-seen";
export const THREAD_LIST_LIMIT = 100;

export function loadSeen(): SeenMap {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as SeenMap) : {};
  } catch {
    return {};
  }
}

export function saveSeen(seen: SeenMap): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(seen));
  } catch {
    // Private window or blocked storage: unread simply resets on reload.
  }
}

// Moves the map forward for the threads now listed: one never seen before is taken as read (so
// nothing is unread on first run), the open one is read now, and absent entries are dropped only
// when the list is shorter than the request limit and is therefore complete.
// Returns the same object when nothing changed.
export function reconcileSeen(threads: readonly ThreadLike[], seen: SeenMap, selectedId: number | null, selectedThread: ThreadLike | null = null): SeenMap {
  const next: SeenMap = threads.length < THREAD_LIST_LIMIT ? {} : { ...seen };
  let changed = false;
  for (const thread of threads) {
    const key = String(thread.id);
    const known = seen[key];
    const selected = thread.id === selectedId;
    if (selected && selectedThread?.id !== selectedId) {
      if (known !== undefined) next[key] = known;
      continue;
    }
    const viewed = selected ? selectedThread!.updated_at : thread.updated_at;
    const value = known === undefined ? (selected ? viewed : thread.updated_at)
      : selected && Date.parse(viewed) > Date.parse(known) ? viewed : known;
    next[key] = value;
    if (value !== known) changed = true;
  }
  if (selectedThread?.id === selectedId && !threads.some((thread) => thread.id === selectedId)) {
    const key = String(selectedThread.id);
    const previous = seen[key];
    const value = previous && Date.parse(previous) > Date.parse(selectedThread.updated_at) ? previous : selectedThread.updated_at;
    next[key] = value;
    if (value !== previous) changed = true;
  }
  if (Object.keys(seen).length !== Object.keys(next).length) changed = true;
  return changed ? next : seen;
}

export function isUnread(thread: ThreadLike, seen: SeenMap, selectedId: number | null): boolean {
  if (thread.archived_at !== null || thread.id === selectedId) return false;
  const last = seen[String(thread.id)];
  return last !== undefined && Date.parse(thread.updated_at) > Date.parse(last);
}
