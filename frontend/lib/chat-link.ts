// Links that open one chat or room (BL-118). No runtime imports, so node tests can load it.
// Web: `<origin>/chat/?thread=3` or `?room=4`. Desktop app: `opendots://chat?thread=3`.
// Only a positive whole number is accepted as an id; anything else yields null.

export const DESKTOP_SCHEME = "opendots";

export type ChatTarget = { kind: "thread" | "room"; id: number };

function parseId(raw: string | null): number | null {
  return raw && /^[1-9]\d{0,14}$/.test(raw) ? Number(raw) : null;
}

/** The target named by a query string, or null. A chat wins when both are present. */
export function chatTargetFromSearch(search: string): ChatTarget | null {
  const params = new URLSearchParams(search);
  const thread = parseId(params.get("thread"));
  if (thread !== null) return { kind: "thread", id: thread };
  const room = parseId(params.get("room"));
  return room !== null ? { kind: "room", id: room } : null;
}

/** The in-app path for a target, as the chat page reads it. */
export function chatPath(target: ChatTarget): string {
  return `/chat/?${target.kind}=${target.id}`;
}

/** A link for the web app, or the same chat on another machine that serves the app. */
export function chatWebLink(origin: string, target: ChatTarget, base = ""): string {
  return `${origin.replace(/\/+$/, "")}${base}${chatPath(target)}`;
}

/** The link the desktop app registers to open. */
export function chatAppLink(target: ChatTarget): string {
  return `${DESKTOP_SCHEME}://chat?${target.kind}=${target.id}`;
}

/** Reads a desktop link or a web link; null for anything that is not a chat or room link. */
export function parseChatLink(raw: string): ChatTarget | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol === `${DESKTOP_SCHEME}:`) {
    if (url.hostname !== "chat" || url.username || url.password) return null;
    return chatTargetFromSearch(url.search);
  }
  if (url.protocol === "http:" || url.protocol === "https:") {
    if (!/^\/(?:[\w.-]+\/)*chat\/?$/.test(url.pathname)) return null;
    return chatTargetFromSearch(url.search);
  }
  return null;
}
