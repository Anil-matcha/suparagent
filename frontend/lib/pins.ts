export type PinnedMessage = {
  itemKey: string;
  role: "user" | "assistant";
  excerpt: string;
  pinnedAt: string;
};

const PINS_CHANGED_EVENT = "suparagent:pins-changed";

export function getPinsStorageKey(threadId: number | null | undefined): string {
  return `suparagent:pins:v1:${threadId ?? "global"}`;
}

export function makePinExcerpt(rawText: string, maxLength = 120): string {
  const cleaned = rawText
    .replace(/\[[^\]]+\]\([^)]*\)/g, (match) => match)
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "(message)";
  if (cleaned.length <= maxLength) return cleaned;
  return `${cleaned.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
}

function normalizePinnedList(value: unknown): PinnedMessage[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: PinnedMessage[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    if (
      typeof rec.itemKey !== "string" ||
      !rec.itemKey ||
      (rec.role !== "user" && rec.role !== "assistant") ||
      typeof rec.excerpt !== "string" ||
      typeof rec.pinnedAt !== "string"
    ) {
      continue;
    }
    if (seen.has(rec.itemKey)) continue;
    seen.add(rec.itemKey);
    result.push({
      itemKey: rec.itemKey,
      role: rec.role,
      excerpt: rec.excerpt,
      pinnedAt: rec.pinnedAt,
    });
  }
  return result;
}

export function loadThreadPins(threadId: number | null | undefined): PinnedMessage[] {
  if (typeof window === "undefined" || !window.localStorage) return [];
  try {
    const raw = window.localStorage.getItem(getPinsStorageKey(threadId));
    if (!raw) return [];
    return normalizePinnedList(JSON.parse(raw));
  } catch {
    return [];
  }
}

export function saveThreadPins(
  threadId: number | null | undefined,
  pins: readonly PinnedMessage[],
): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  const key = getPinsStorageKey(threadId);
  try {
    if (pins.length === 0) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, JSON.stringify(pins));
    }
    window.dispatchEvent(new CustomEvent(PINS_CHANGED_EVENT, { detail: { key } }));
  } catch {
    // ignore storage quota / private mode failures
  }
}

export function toggleThreadPin(
  threadId: number | null | undefined,
  entry: { itemKey: string; role: "user" | "assistant"; excerpt: string },
): PinnedMessage[] {
  const current = loadThreadPins(threadId);
  const exists = current.some((p) => p.itemKey === entry.itemKey);
  const next: PinnedMessage[] = exists
    ? current.filter((p) => p.itemKey !== entry.itemKey)
    : [
        {
          itemKey: entry.itemKey,
          role: entry.role,
          excerpt: makePinExcerpt(entry.excerpt),
          pinnedAt: new Date().toISOString(),
        },
        ...current,
      ];
  saveThreadPins(threadId, next);
  return next;
}

export function removeThreadPin(
  threadId: number | null | undefined,
  itemKey: string,
): PinnedMessage[] {
  const current = loadThreadPins(threadId);
  const next = current.filter((p) => p.itemKey !== itemKey);
  if (next.length !== current.length) {
    saveThreadPins(threadId, next);
  }
  return next;
}

export function isMessagePinned(
  pins: readonly PinnedMessage[],
  itemKey: string | undefined,
): boolean {
  if (!itemKey) return false;
  return pins.some((p) => p.itemKey === itemKey);
}

export function subscribeThreadPins(
  threadId: number | null | undefined,
  listener: () => void,
): () => void {
  if (typeof window === "undefined") return () => {};
  const key = getPinsStorageKey(threadId);
  const onCustom = (event: Event) => {
    const detail = (event as CustomEvent<{ key?: string }>).detail;
    if (!detail?.key || detail.key === key) listener();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === key || event.key === null) listener();
  };
  window.addEventListener(PINS_CHANGED_EVENT, onCustom);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(PINS_CHANGED_EVENT, onCustom);
    window.removeEventListener("storage", onStorage);
  };
}
