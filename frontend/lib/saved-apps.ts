// Saved apps: a page an agent made, kept so it can be opened again on its own. Types, the API calls, and the small
// pure helpers the chat card, the apps list and the app view share.
import { request } from "./api.ts";

export type SavedAppVersion = { rev: number; at: string };
export type SavedApp = {
  id: string;
  name: string;
  agent_id: number;
  agent_name: string | null;
  thread_id: number | null;
  page_id: string;
  rev: number;
  pinned_rev: number | null;
  versions: SavedAppVersion[];
  uses: string[];
  created_at: string;
  updated_at: string;
  chat: boolean;
};
export type SavedAppDocument = {
  id: string;
  name: string;
  rev: number;
  title: string;
  html: string;
  height: number;
  frameless: boolean;
  uses: string[];
  versions: SavedAppVersion[];
  pinned_rev: number | null;
  chat: boolean;
};

export const SAVED_APP_ID = /^sa_[A-Za-z0-9_-]{16}$/;
export const isSavedAppId = (value: unknown): value is string => typeof value === "string" && SAVED_APP_ID.test(value);

const enc = encodeURIComponent;

export const listSavedApps = () => request<{ apps: SavedApp[] }>("/saved-apps").then((r) => r.apps);
export const saveApp = (body: { thread_id: number; page_id: string }) =>
  request<{ app: SavedApp }>("/saved-apps", { method: "POST", body: JSON.stringify(body) }).then((r) => r.app);
export const getSavedApp = (id: string) => request<{ app: SavedApp }>(`/saved-apps/${enc(id)}`).then((r) => r.app);
export const getSavedAppDocument = (id: string, rev?: number) =>
  request<SavedAppDocument>(`/saved-apps/${enc(id)}/document${rev === undefined ? "" : `?rev=${rev}`}`);
export const renameSavedApp = (id: string, name: string) =>
  request<{ app: SavedApp }>(`/saved-apps/${enc(id)}`, { method: "PATCH", body: JSON.stringify({ name }) }).then((r) => r.app);
// null follows the newest version again.
export const pinSavedAppRev = (id: string, pinned_rev: number | null) =>
  request<{ app: SavedApp }>(`/saved-apps/${enc(id)}`, { method: "PATCH", body: JSON.stringify({ pinned_rev }) }).then((r) => r.app);
export const deleteSavedApp = (id: string) => request<{ ok: true }>(`/saved-apps/${enc(id)}`, { method: "DELETE" });

// Connected-app requests a page makes through the bridge. `engaged` is the host's own click check, never the page's.
export type PageRpcBody = { method: "connectors/list" | "connectors/call"; params?: Record<string, unknown>; engaged: boolean };
export const pageRpc = (threadId: number, pageId: string, body: PageRpcBody) =>
  request<unknown>(`/threads/${threadId}/pages/${enc(pageId)}/rpc`, { method: "POST", body: JSON.stringify(body) });
export const savedAppRpc = (id: string, body: PageRpcBody) =>
  request<unknown>(`/saved-apps/${enc(id)}/rpc`, { method: "POST", body: JSON.stringify(body) });

// "gmail:GMAIL_FETCH_EMAILS" -> "Gmail"; the toolkits a page declared, for display only.
export function usesToolkits(uses: readonly string[]): string[] {
  const out: string[] = [];
  for (const u of uses) {
    const i = u.indexOf(":");
    if (i <= 0) continue;
    const name = u.slice(0, i).replace(/[_-]+/g, " ");
    const label = name.charAt(0).toUpperCase() + name.slice(1);
    if (!out.includes(label)) out.push(label);
  }
  return out;
}

// The address of the app view (a query string, not a route segment, so the static export works).
export const appViewPath = (id: string, opts: { window?: boolean } = {}) =>
  `/apps/view/?id=${enc(id)}${opts.window ? "&window=1" : ""}`;
export const chatPath = (threadId: number) => `/chat/?thread=${threadId}`;

// Saved apps by the page they came from, so a card in a chat knows it is already saved.
export function savedByPage(apps: readonly SavedApp[], threadId: number | null): Map<string, SavedApp> {
  const map = new Map<string, SavedApp>();
  for (const app of apps) if (threadId !== null && app.thread_id === threadId) map.set(app.page_id, app);
  return map;
}

// A stable tint for the letter mark, from a hash of the id.
export function appHue(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % 360;
}
export const appTint = (id: string) => ({ background: `hsl(${appHue(id)} 55% 88%)`, color: `hsl(${appHue(id)} 45% 28%)` });
export const appInitial = (name: string) => (Array.from(name.trim())[0] ?? "?").toUpperCase();

export const newestRev = (versions: readonly SavedAppVersion[]) => versions.reduce((max, v) => Math.max(max, v.rev), 0);

// What the version menu shows: each version, newest first, with the one on screen marked and the pinned one named.
export function versionChoices(versions: readonly SavedAppVersion[], current: number, pinned: number | null) {
  return [...versions].sort((a, b) => b.rev - a.rev).map((v) => ({
    rev: v.rev, at: v.at, current: v.rev === current, pinned: v.rev === pinned, newest: v.rev === newestRev(versions),
  }));
}

// A name is trimmed and kept short; null when nothing usable is left.
export function cleanName(value: string, max = 80): string | null {
  const name = value.trim().replace(/\s+/g, " ").slice(0, max).trim();
  return name || null;
}

// What the host can do with an installed launcher (desktop only; absent in a browser).
export type AppInstallBridge = {
  install: (app: { id: string; name: string; iconPng?: string }) => Promise<unknown>;
  uninstall: (id: string) => Promise<unknown>;
  list: () => Promise<string[]>;
};
export function appInstallBridge(win: unknown = typeof window === "undefined" ? undefined : window): AppInstallBridge | null {
  const b = (win as { suparagent?: { appInstall?: Partial<AppInstallBridge> } } | undefined)?.suparagent?.appInstall;
  return b && typeof b.install === "function" && typeof b.uninstall === "function" && typeof b.list === "function" ? (b as AppInstallBridge) : null;
}

export const APP_CHAT_NOTE = "Open the chat to talk to the agent.";
export const APP_NO_CHAT_MESSAGE = "This app's chat is no longer available, so it cannot send messages to the agent.";

// The bridge's send adapter for the app view. A page asking to message the agent is refused with a clear reason when the
// chat is gone; otherwise nothing is sent (v1) and the person is pointed to the chat. The refusal reaches the page as the error.
export function appViewSend({ chat, onNote }: { chat: boolean; onNote: () => void }) {
  return async (): Promise<boolean> => {
    if (!chat) throw new Error(APP_NO_CHAT_MESSAGE);
    onNote();
    throw new Error(APP_CHAT_NOTE);
  };
}

// The text of a failed save, by cause.
export function saveErrorMessage(err: unknown): string {
  const status = (err as { status?: number } | null)?.status;
  const message = err instanceof Error && err.message ? err.message : "";
  if (status === 404) return "That page is no longer in the conversation, so it cannot be saved.";
  if (status === 409) return message || "There is no room for another saved app.";
  if (status === 507) return message || "There is no storage left to save this app.";
  return message ? `Could not save the app. ${message}` : "Could not save the app.";
}
