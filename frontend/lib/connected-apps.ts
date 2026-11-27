// Connected apps (Composio): the parts of the Settings flow that are plain
// logic, kept apart from the component so they can be tested.

export type ComposioApp = { slug: string; label: string };
export type ComposioAccount = {
  id: string;
  toolkit: string;
  alias: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
};
export type ComposioLink = { redirect_url: string; account_id: string; expires_in_seconds: number };

export const POLL_INTERVAL_MS = 3000;

const WAITING = new Set(["INITIATED", "INITIALIZING"]);
const FAILED = new Set(["FAILED", "EXPIRED"]);

export function statusLabel(status: string): string {
  if (status === "ACTIVE") return "Connected";
  if (WAITING.has(status)) return "Waiting for you to finish in your browser";
  if (status === "FAILED") return "Connection failed";
  if (status === "EXPIRED") return "Link expired";
  if (status === "INACTIVE") return "Inactive";
  return status.charAt(0) + status.slice(1).toLowerCase();
}

export type Outcome = "connected" | "waiting" | "failed" | "gone";

// What the account we are waiting for looks like in the latest listing.
export function outcomeFor(accounts: ComposioAccount[], accountId: string): Outcome {
  const found = accounts.find((account) => account.id === accountId);
  if (!found) return "gone";
  if (found.status === "ACTIVE") return "connected";
  if (FAILED.has(found.status)) return "failed";
  return "waiting";
}

// Only an https link on composio.dev is ever opened; the server checks too.
export function isComposioLink(raw: unknown): raw is string {
  if (typeof raw !== "string" || raw.length > 4096 || /\s/.test(raw)) return false;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && !url.username && !url.password && url.port === ""
      && (url.hostname === "composio.dev" || url.hostname.endsWith(".composio.dev"));
  } catch {
    return false;
  }
}

export type LinkBridge = { openComposioLink?: (url: string) => Promise<void> };
export type TabWindow = { opener: unknown; closed?: boolean; location: { href: string }; close(): void };
export type Opener = (url: string, target: string) => TabWindow | null;

export type ReservedPage = { navigate(url: string): Promise<void>; cancel(): void };

// A browser blocks a window opened after an await, so the tab is opened during
// the click and sent to the link once the server has made it. The desktop app
// has no tabs: its shell opens the link in the system browser.
export function reservePage(bridge: LinkBridge | undefined, open: Opener): ReservedPage {
  if (bridge?.openComposioLink) {
    const openLink = bridge.openComposioLink;
    return {
      navigate: async (url) => { if (!isComposioLink(url)) throw new Error("This connect link is not from Composio."); await openLink(url); },
      cancel: () => {},
    };
  }
  const tab = open("about:blank", "_blank");
  if (tab) tab.opener = null;
  return {
    navigate: async (url) => {
      if (!isComposioLink(url)) { tab?.close(); throw new Error("This connect link is not from Composio."); }
      if (!tab || tab.closed) throw new Error("Your browser blocked the new tab. Allow pop-ups for this page and try again.");
      tab.location.href = url;
    },
    cancel: () => { tab?.close(); },
  };
}

// Waiting stops at the link's life (a little over, so a late success is seen).
export function pollDeadline(expiresInSeconds: number, now: number): number {
  const seconds = Number.isFinite(expiresInSeconds) && expiresInSeconds > 0 ? Math.min(expiresInSeconds, 3600) : 600;
  return now + (seconds + 15) * 1000;
}

export function accountsOf(accounts: ComposioAccount[], toolkit: string): ComposioAccount[] {
  return accounts.filter((account) => account.toolkit === toolkit);
}

// The apps and engines an agent can be allowed to use in this version.
// `labels` (Gmail only, BL-173) is the middle level: read, plus adding the app's own labels under Worker/.
export const AGENT_APPS: { slug: string; label: string; read: string; write: string | null; labels?: string | null }[] = [
  {
    slug: "github", label: "GitHub",
    read: "Read your profile, repositories, issues, pull requests, commits and files, and search.",
    write: "Also create issues, comment, and close or reopen issues. You approve each change first.",
  },
  {
    slug: "gmail", label: "Gmail",
    read: "Search messages, read message threads, and see the connected account profile.",
    labels: "Read mail and add its own labels under Worker/. It cannot send, draft, remove, archive or delete anything.",
    write: "Also create a plain-text draft reply in an existing thread. You approve each draft; it is never sent.",
  },
  {
    slug: "googlecalendar", label: "Google Calendar",
    read: "List calendars, search events by words, and read event summaries in a date window.",
    write: "Also add an event to a calendar. No guests are invited and nothing is emailed. You approve each event first.",
  },
  {
    slug: "googleads", label: "Google Ads",
    read: "Read campaign, ad group and ad performance reports. It cannot change, pause or spend anything.",
    write: null,
  },
  {
    slug: "metaads", label: "Meta Ads",
    read: "Read ad accounts, campaigns and performance for Facebook and Instagram. It cannot change, pause or spend anything.",
    write: null,
  },
];
const APPS_ENGINES = new Set(["claude", "codex"]);

export function supportsConnectedApps(provider: string): boolean {
  return APPS_ENGINES.has(provider);
}

export type Level = "off" | "read" | "labels" | "write";
export type Scopes = Record<string, string>;
export type AccountScopes = Record<string, string[]>;

// Only an app with curated labelling (Gmail today) has the "labels" level; for any other app it is never offered, read or kept.
export const supportsLabels = (slug: string): boolean => Boolean(AGENT_APPS.find((app) => app.slug === slug)?.labels);

export function levelOf(scopes: Scopes | null | undefined, slug: string): Level {
  const level = scopes?.[slug];
  if (level === "labels") return supportsLabels(slug) ? "labels" : "off";
  return level === "read" || level === "write" ? level : "off";
}

// The scopes after setting one app's level. "off" removes it; the others are untouched. Asking for "labels" on an app
// that has none changes nothing.
export function withApp(scopes: Scopes | null | undefined, slug: string, level: Level): Scopes {
  const next: Scopes = { ...(scopes ?? {}) };
  if (level === "labels" && !supportsLabels(slug)) return next;
  if (level === "off") delete next[slug]; else next[slug] = level;
  return next;
}

export function withAccount(scopes: AccountScopes | null | undefined, slug: string,
                            accountId: string, allowed: boolean): AccountScopes {
  const next = { ...(scopes ?? {}) };
  const ids = new Set(Array.isArray(next[slug]) ? next[slug] : []);
  if (allowed) ids.add(accountId); else ids.delete(accountId);
  if (ids.size) next[slug] = [...ids].sort(); else delete next[slug];
  return next;
}

// The share of a mailbox an agent may see (BL-172), per app and connected account. No entry means the whole mailbox.
// Only Gmail has one. The server keeps the sampling seed; the page never sees or sends it.
export type Share = { percent: number; pins?: string[] };
export type Shares = Record<string, Record<string, Share>>;
export const SHARE_PRESETS: readonly number[] = [100, 50, 25, 10, 5];
export const supportsShare = (app: string): boolean => app === "gmail";
export const validPercent = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 100;

// The percent for one account: 100 (all mail) when it has no valid entry.
export function shareOf(shares: Shares | null | undefined, app: string, accountId: string): number {
  const percent = shares?.[app]?.[accountId]?.percent;
  return validPercent(percent) ? percent : 100;
}

// The shares after setting one account's percent. Pins stay. All mail on an account with no entry adds none, since
// that is already what no entry means; an invalid percent changes nothing.
export function withShare(shares: Shares | null | undefined, app: string, accountId: string, percent: number): Shares {
  if (!validPercent(percent) || !supportsShare(app)) return { ...(shares ?? {}) };
  const current = shares?.[app]?.[accountId];
  if (!current && percent === 100) return { ...(shares ?? {}) };
  const entry: Share = { ...(current ?? {}), percent };
  return { ...(shares ?? {}), [app]: { ...(shares?.[app] ?? {}), [accountId]: entry } };
}

// What to send when saving: the shares that still mean something. An account that is no longer selected, an app whose
// level is Off, and an engine that cannot use apps send none; an invalid entry is dropped, never repaired.
export function sharesToSend(shares: Shares | null | undefined, accountScopes: AccountScopes | null | undefined,
                             appScopes: Scopes | null | undefined, provider: string): Shares {
  if (!supportsConnectedApps(provider)) return {};
  const out: Shares = {};
  for (const [app, byAccount] of Object.entries(shares ?? {})) {
    if (!supportsShare(app) || levelOf(appScopes, app) === "off" || typeof byAccount !== "object" || byAccount === null) continue;
    const selected = new Set(accountScopes?.[app] ?? []);
    for (const [accountId, entry] of Object.entries(byAccount)) {
      if (!selected.has(accountId) || !entry || !validPercent(entry.percent)) continue;
      const pins = Array.isArray(entry.pins) ? entry.pins.filter((pin) => typeof pin === "string" && pin.length > 0 && pin.length <= 300).slice(0, 200) : [];
      (out[app] ??= {})[accountId] = pins.length ? { percent: entry.percent, pins } : { percent: entry.percent };
    }
  }
  return out;
}

export function accountScopesToSend(accountScopes: AccountScopes | null | undefined,
                                    appScopes: Scopes | null | undefined,
                                    provider: string): AccountScopes {
  if (!supportsConnectedApps(provider)) return {};
  return Object.fromEntries(Object.entries(accountScopes ?? {}).flatMap(([app, ids]) => {
    if (levelOf(appScopes, app) === "off" || !Array.isArray(ids)) return [];
    const selected = [...new Set(ids.filter((id) => typeof id === "string" && id.length > 0 && id.length <= 300))].sort();
    return selected.length ? [[app, selected]] : [];
  }));
}

// An app name as Composio writes it (lower case, letters, digits, - and _). It says nothing about the app existing:
// the server asks Composio about any app that is new to an agent.
const APP_SLUG = /^[a-z0-9][a-z0-9_-]{0,63}$/;

// What to send when saving an agent: nothing for an engine that cannot use apps
// (this also clears them if the engine was changed), else the levels that make sense.
// An app with curated tools can be changed only if it has a change level; any other app
// has both levels (every one of its actions asks first).
export function scopesToSend(scopes: Scopes | null | undefined, provider: string): Scopes | null {
  if (!supportsConnectedApps(provider)) return null;
  const known = Object.fromEntries(Object.entries(scopes ?? {}).filter(([slug, level]) => {
    if (!APP_SLUG.test(slug) || (level !== "read" && level !== "write" && level !== "labels")) return false;
    const curated = AGENT_APPS.find((candidate) => candidate.slug === slug);
    if (level === "labels") return Boolean(curated?.labels);
    return curated ? level === "read" || Boolean(curated.write) : true;
  }));
  return Object.keys(known).length ? known : null;
}

export type AgentAppOption = { slug: string; label: string; read: string; write: string | null; labels: string | null; generic: boolean };

export const GENERIC_READ = "Look up which actions the app has. Nothing runs and nothing changes.";
export const GENERIC_WRITE = "Also run any action of the app. You approve every call first, and each card shows the call in full.";

// "google_sheets" -> "Google sheets": a name for an app the page has no catalog entry for.
export function appLabel(slug: string): string {
  const words = slug.replace(/[-_]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : slug;
}

// The apps an agent can be given: the ones with curated tools, then every other app that has an account here or is
// already allowed for the agent (so one that was disconnected stays visible and can be turned off).
export function agentAppOptions(accounts: ComposioAccount[] | null | undefined, scopes: Scopes | null | undefined, labels: Record<string, string> = {}): AgentAppOption[] {
  const curated: AgentAppOption[] = AGENT_APPS.map((app) => ({ ...app, labels: app.labels ?? null, generic: false }));
  const known = new Set(curated.map((app) => app.slug));
  const others = new Set<string>();
  for (const account of accounts ?? []) if (APP_SLUG.test(account.toolkit) && !known.has(account.toolkit)) others.add(account.toolkit);
  for (const slug of Object.keys(scopes ?? {})) if (APP_SLUG.test(slug) && !known.has(slug)) others.add(slug);
  const generic: AgentAppOption[] = [...others].sort().map((slug) => ({
    slug, label: labels[slug] ?? appLabel(slug), read: GENERIC_READ, write: GENERIC_WRITE, labels: null, generic: true,
  }));
  return [...curated, ...generic];
}
