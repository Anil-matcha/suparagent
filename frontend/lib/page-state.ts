// A bot page's saved data. The page runs in a frame with an opaque origin, so it has no storage of its own: the host
// keeps it, per page id, in localStorage. Pure over a storage-like adapter so it can be tested.
export const STATE_LIMITS = { keyChars: 200, valueBytes: 64 * 1024, totalBytes: 256 * 1024, keys: 200 } as const;

export type StorageLike = Pick<Storage, "getItem" | "setItem">;

export class PageStateError extends Error {}

const bytes = (s: string) => new TextEncoder().encode(s).length;

export function createPageState(storage: StorageLike | null, pageId: string) {
  const slot = `sp-page-state:${pageId}`;
  let memory: Record<string, string> = {};
  const read = (): Record<string, string> => {
    if (!storage) return memory;
    try {
      const raw = storage.getItem(slot);
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch { return memory; }
  };
  const write = (data: Record<string, string>) => {
    memory = data;
    if (!storage) return;
    try { storage.setItem(slot, JSON.stringify(data)); } catch { throw new PageStateError("Could not save: browser storage is full or blocked."); }
  };
  const checkKey = (key: unknown): string => {
    if (typeof key !== "string" || key.length < 1) throw new PageStateError("A state key must be a non-empty string.");
    if (key.length > STATE_LIMITS.keyChars) throw new PageStateError(`A state key can be at most ${STATE_LIMITS.keyChars} characters.`);
    return key;
  };
  return {
    get(key: unknown): unknown {
      const raw = read()[checkKey(key)];
      return raw === undefined ? null : JSON.parse(raw);
    },
    keys(): string[] { return Object.keys(read()); },
    // A value of undefined removes the key.
    set(key: unknown, value: unknown): true {
      const k = checkKey(key);
      const data = { ...read() };
      if (value === undefined) { delete data[k]; write(data); return true; }
      let encoded: string | undefined;
      try { encoded = JSON.stringify(value); } catch { encoded = undefined; }
      if (encoded === undefined) throw new PageStateError("That value cannot be saved as JSON.");
      if (bytes(encoded) > STATE_LIMITS.valueBytes) throw new PageStateError(`A saved value can be at most ${STATE_LIMITS.valueBytes} bytes.`);
      if (!(k in data) && Object.keys(data).length >= STATE_LIMITS.keys) throw new PageStateError(`A page can save at most ${STATE_LIMITS.keys} keys.`);
      data[k] = encoded;
      const total = Object.entries(data).reduce((sum, [name, v]) => sum + bytes(name) + bytes(v), 0);
      if (total > STATE_LIMITS.totalBytes) throw new PageStateError(`A page can save at most ${STATE_LIMITS.totalBytes} bytes in total.`);
      write(data);
      return true;
    },
  };
}
export type PageState = ReturnType<typeof createPageState>;
