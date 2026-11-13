const KEY = "suparagent.staged-uploads";

export type StagedUpload = { threadId: number; id: string };

export function newUploadId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function loadStagedUploads(storage: Storage): StagedUpload[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is StagedUpload =>
      typeof item?.threadId === "number" && Number.isSafeInteger(item.threadId) && item.threadId > 0 &&
      typeof item.id === "string" && /^[0-9a-f]{32}$/.test(item.id)) : [];
  } catch { return []; }
}

export function saveStagedUploads(storage: Storage, items: StagedUpload[]): void {
  storage.setItem(KEY, JSON.stringify(items));
}
