// Session helpers with no runtime imports, so node tests can load them (docs/AUTH.md).

import type { PairingScope } from "./access-label";

export type AuthStatus = {
  owner_configured: boolean;
  setup_allowed: boolean;
  desktop: boolean;
  signed_in: boolean;
};

export type SessionInfo = {
  kind: "desktop" | "loopback" | "browser" | "device";
  scopes: string[];
  label: string | null;
  session_id: number | null;
  user_id: string | null;
  csrf_token: string | null;
  owner_configured: boolean;
  // Set when a signed-in member (not a paired device) is acting.
  person_id?: number | null;
  role?: PersonRole | null;
  display_name?: string | null;
};

export type PersonRole = "owner" | "admin" | "member" | "viewer";

// The invitation arrives in the URL fragment (#token=…&email=…), which never reaches a server log.
export function inviteFromHash(hash: string): { token: string; email: string } | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const token = params.get("token")?.trim();
  return token ? { token, email: params.get("email")?.trim() ?? "" } : null;
}

export type PairingCode = {
  id: number;
  code: string;
  display_code: string;
  scope: PairingScope;
  label: string;
  expires_at: string;
};

export type Device = {
  id: number;
  kind: "browser" | "device";
  label: string;
  scopes: string[];
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
};

// The key older versions of the web UI kept the identity under.
export const LEGACY_IDENTITY_KEY = "vadoo_user_id";

// Moves an identity an older web UI left in this browser to the server, once.
// The server keeps its own if it already has one; the local copy goes either way.
export async function migrateLegacyIdentity(
  session: SessionInfo,
  storage: Pick<Storage, "getItem" | "removeItem">,
  setIdentity: (body: { legacy_user_id: string }) => Promise<{ user_id: string | null }>,
): Promise<string | null> {
  let legacy: string | null = null;
  try {
    legacy = storage.getItem(LEGACY_IDENTITY_KEY);
  } catch {
    return session.user_id;
  }
  if (!legacy || !session.scopes.includes("admin")) return session.user_id;
  try {
    const { user_id } = session.user_id ? { user_id: session.user_id } : await setIdentity({ legacy_user_id: legacy });
    storage.removeItem(LEGACY_IDENTITY_KEY);
    return user_id;
  } catch (err) {
    // An id the server does not accept is not worth keeping either.
    if ((err as { status?: number } | null)?.status === 422) storage.removeItem(LEGACY_IDENTITY_KEY);
    return session.user_id;
  }
}

// Only a same-site path: never send someone to another site after sign-in.
// `base` is the prefix the pages are served under (lib/paths.ts), "" unless a deployment sets one.
export function safeNextPath(raw: string | null, base = ""): string {
  const chat = `${base}/chat`;
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return chat;
  const inside = base && raw.startsWith(`${base}/`) ? raw.slice(base.length) : raw;
  if (inside.startsWith("/login") || inside.startsWith("/pair") || inside.startsWith("/join")) return chat;
  return raw;
}

/** A link an invited person opens. The secret rides in the fragment, as pairing codes do. */
export function inviteLink(origin: string, basePath: string, invite: { token: string; email: string }): string {
  return `${origin}${basePath}/join/#token=${encodeURIComponent(invite.token)}&email=${encodeURIComponent(invite.email)}`;
}

export function pairingCodeFromHash(hash: string): string {
  const match = /(?:^#|&)code=([^&]*)/.exec(hash);
  return match ? decodeURIComponent(match[1]).slice(0, 32) : "";
}
