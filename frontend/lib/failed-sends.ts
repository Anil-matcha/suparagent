import type { CitationAttachment, ReplyTarget } from "./citations";

export type FailedSend = {
  id: string;
  threadId: number | null;
  rawText: string;
  displayText: string;
  error: string;
  skills: string[];
  attachmentIds: string[];
  replyTo: ReplyTarget | null;
  citations: CitationAttachment[];
  createdAt: string;
};

const FAILED_SENDS_CHANGED_EVENT = "suparagent:failed-sends-changed";
const MAX_FAILED_SENDS = 10;

export function getFailedSendsStorageKey(threadId: number | null | undefined): string {
  return `suparagent:failed-sends:v1:${threadId ?? "new"}`;
}

export function newFailedSend(input: {
  threadId: number | null;
  rawText: string;
  displayText: string;
  error: string;
  skills?: string[];
  attachmentIds?: string[];
  replyTo?: ReplyTarget | null;
  citations?: CitationAttachment[];
}): FailedSend {
  return {
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    threadId: input.threadId,
    rawText: input.rawText,
    displayText: input.displayText,
    error: input.error,
    skills: input.skills ? [...input.skills] : [],
    attachmentIds: input.attachmentIds ? [...input.attachmentIds] : [],
    replyTo: input.replyTo ?? null,
    citations: input.citations ? [...input.citations] : [],
    createdAt: new Date().toISOString(),
  };
}

function normalizeFailedSends(value: unknown): FailedSend[] {
  if (!Array.isArray(value)) return [];
  const result: FailedSend[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    if (
      typeof rec.id !== "string" ||
      !rec.id ||
      typeof rec.rawText !== "string" ||
      !rec.rawText ||
      typeof rec.displayText !== "string" ||
      typeof rec.error !== "string" ||
      typeof rec.createdAt !== "string"
    ) {
      continue;
    }
    result.push({
      id: rec.id,
      threadId: typeof rec.threadId === "number" ? rec.threadId : null,
      rawText: rec.rawText,
      displayText: rec.displayText,
      error: rec.error,
      skills: Array.isArray(rec.skills) ? rec.skills.filter((s): s is string => typeof s === "string") : [],
      attachmentIds: Array.isArray(rec.attachmentIds)
        ? rec.attachmentIds.filter((s): s is string => typeof s === "string")
        : [],
      replyTo:
        rec.replyTo && typeof rec.replyTo === "object" && typeof (rec.replyTo as ReplyTarget).itemKey === "string"
          ? (rec.replyTo as ReplyTarget)
          : null,
      citations: Array.isArray(rec.citations) ? (rec.citations as CitationAttachment[]) : [],
      createdAt: rec.createdAt,
    });
  }
  return result.slice(-MAX_FAILED_SENDS);
}

export function loadFailedSends(threadId: number | null | undefined): FailedSend[] {
  if (typeof window === "undefined" || !window.localStorage) return [];
  try {
    const raw = window.localStorage.getItem(getFailedSendsStorageKey(threadId));
    if (!raw) return [];
    return normalizeFailedSends(JSON.parse(raw));
  } catch {
    return [];
  }
}

export function saveFailedSends(
  threadId: number | null | undefined,
  items: readonly FailedSend[],
): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  const key = getFailedSendsStorageKey(threadId);
  try {
    if (items.length === 0) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, JSON.stringify(items.slice(-MAX_FAILED_SENDS)));
    }
    window.dispatchEvent(new CustomEvent(FAILED_SENDS_CHANGED_EVENT, { detail: { key } }));
  } catch {
    // ignore storage errors
  }
}

export function addFailedSend(
  threadId: number | null | undefined,
  entry: FailedSend,
): FailedSend[] {
  const current = loadFailedSends(threadId).filter((item) => item.id !== entry.id);
  const next = [...current, entry].slice(-MAX_FAILED_SENDS);
  saveFailedSends(threadId, next);
  return next;
}

export function updateFailedSendError(
  threadId: number | null | undefined,
  id: string,
  error: string,
): FailedSend[] {
  const current = loadFailedSends(threadId);
  const next = current.map((item) => (item.id === id ? { ...item, error } : item));
  saveFailedSends(threadId, next);
  return next;
}

export function removeFailedSend(
  threadId: number | null | undefined,
  id: string,
): FailedSend[] {
  const current = loadFailedSends(threadId);
  const next = current.filter((item) => item.id !== id);
  if (next.length !== current.length) {
    saveFailedSends(threadId, next);
  }
  return next;
}

export function subscribeFailedSends(
  threadId: number | null | undefined,
  listener: () => void,
): () => void {
  if (typeof window === "undefined") return () => {};
  const key = getFailedSendsStorageKey(threadId);
  const onCustom = (event: Event) => {
    const detail = (event as CustomEvent<{ key?: string }>).detail;
    if (!detail?.key || detail.key === key) listener();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === key || event.key === null) listener();
  };
  window.addEventListener(FAILED_SENDS_CHANGED_EVENT, onCustom);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(FAILED_SENDS_CHANGED_EVENT, onCustom);
    window.removeEventListener("storage", onStorage);
  };
}
