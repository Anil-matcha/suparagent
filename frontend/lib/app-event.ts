// The "when new mail arrives" trigger of a routine (BL-177): plain logic apart from the component.
// New mail wakes a routine only when the mail provider reports it; there is no polling anywhere.
import type { ConnectedAppGrant } from "./api";
import { accountsOf } from "./connected-apps.ts";
import type { ComposioAccount } from "./connected-apps.ts";

export type AppEventConfig = {
  app: "gmail";
  account_id: string;
  query?: string;
  debounce_minutes: number;
  max_runs_per_day: number;
  quiet?: { from: string; to: string };
};

export type AppEventState = "active" | "paused" | "error" | "removed";
export const COUNTERS = ["received", "started", "merged", "duplicate", "outside_share", "rejected", "unrouted", "dropped_cap", "dropped_quiet"] as const;
export type AppEventCounter = (typeof COUNTERS)[number];

export type AppEventStatus = {
  state: AppEventState;
  last_event_at: string | null;
  last_error: string | null;
  today: Record<AppEventCounter, number>;
  can_receive: boolean;
  reason: string | null;
};

export const DEFAULT_DEBOUNCE = 5;
export const DEFAULT_MAX_RUNS = 12;
export const QUERY_MAX = 200;
const STATES: readonly string[] = ["active", "paused", "error", "removed"];
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

const whole = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value);
const text = (value: unknown): string | null => (typeof value === "string" ? value : null);

// What the server says about the trigger, or null for anything off-shape. Unknown fields are dropped.
export function parseAppEvent(raw: unknown): AppEventConfig | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (data.app !== "gmail" || typeof data.account_id !== "string" || !data.account_id
      || !whole(data.debounce_minutes) || !whole(data.max_runs_per_day)) return null;
  const config: AppEventConfig = { app: "gmail", account_id: data.account_id, debounce_minutes: data.debounce_minutes, max_runs_per_day: data.max_runs_per_day };
  if (typeof data.query === "string" && data.query) config.query = data.query.slice(0, QUERY_MAX);
  const quiet = data.quiet as Record<string, unknown> | null | undefined;
  if (quiet && typeof quiet.from === "string" && typeof quiet.to === "string" && TIME.test(quiet.from) && TIME.test(quiet.to)) {
    config.quiet = { from: quiet.from, to: quiet.to };
  }
  return config;
}

export function parseAppEventStatus(raw: unknown): AppEventStatus | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (typeof data.state !== "string" || !STATES.includes(data.state) || typeof data.can_receive !== "boolean") return null;
  const today = (data.today && typeof data.today === "object" ? data.today : {}) as Record<string, unknown>;
  const counters = Object.fromEntries(COUNTERS.map((name) => [name, whole(today[name]) && (today[name] as number) >= 0 ? (today[name] as number) : 0])) as Record<AppEventCounter, number>;
  return {
    state: data.state as AppEventState, last_event_at: text(data.last_event_at), last_error: text(data.last_error),
    today: counters, can_receive: data.can_receive, reason: text(data.reason),
  };
}

// -- the editor's form state ---------------------------------------------------------------------------------------

export type AppEventDraft = {
  enabled: boolean;
  hadTrigger: boolean; // the saved routine already has one, so switching it off must say so
  accountId: string;
  query: string;
  debounce: string;
  maxRuns: string;
  quietEnabled: boolean;
  quietFrom: string;
  quietTo: string;
};

export function emptyAppEventDraft(): AppEventDraft {
  return { enabled: false, hadTrigger: false, accountId: "", query: "", debounce: String(DEFAULT_DEBOUNCE), maxRuns: String(DEFAULT_MAX_RUNS),
    quietEnabled: false, quietFrom: "20:00", quietTo: "07:00" };
}

export function appEventDraftOf(config: AppEventConfig | null | undefined): AppEventDraft {
  const parsed = parseAppEvent(config);
  if (!parsed) return emptyAppEventDraft();
  return {
    enabled: true, hadTrigger: true, accountId: parsed.account_id, query: parsed.query ?? "",
    debounce: String(parsed.debounce_minutes), maxRuns: String(parsed.max_runs_per_day),
    quietEnabled: Boolean(parsed.quiet), quietFrom: parsed.quiet?.from ?? "20:00", quietTo: parsed.quiet?.to ?? "07:00",
  };
}

// Why the trigger cannot be saved as it is, in plain words, or null.
export function appEventProblem(draft: AppEventDraft): string | null {
  if (!draft.enabled) return null;
  if (!draft.accountId) return "Choose the Gmail account the mail arrives in.";
  if (draft.query.length > QUERY_MAX) return `Keep the search to ${QUERY_MAX} characters or fewer.`;
  const wait = Number(draft.debounce);
  if (!draft.debounce.trim() || !Number.isInteger(wait) || wait < 1 || wait > 60) return "Wait between 1 and 60 minutes.";
  const runs = Number(draft.maxRuns);
  if (!draft.maxRuns.trim() || !Number.isInteger(runs) || runs < 1 || runs > 48) return "Allow between 1 and 48 runs a day.";
  if (draft.quietEnabled) {
    if (!TIME.test(draft.quietFrom) || !TIME.test(draft.quietTo)) return "Quiet hours need times like 20:00 and 07:00.";
    if (draft.quietFrom === draft.quietTo) return "Quiet hours need different start and end times.";
  }
  return null;
}

// The trigger to send: the config when on, null to remove an existing one, undefined to leave the routine's trigger alone.
export function appEventInput(draft: AppEventDraft): AppEventConfig | null | undefined {
  if (!draft.enabled) return draft.hadTrigger ? null : undefined;
  const config: AppEventConfig = { app: "gmail", account_id: draft.accountId, debounce_minutes: Number(draft.debounce), max_runs_per_day: Number(draft.maxRuns) };
  const query = draft.query.trim();
  if (query) config.query = query;
  if (draft.quietEnabled) config.quiet = { from: draft.quietFrom, to: draft.quietTo };
  return config;
}

// -- who may be chosen, and whether the trigger can work --------------------------------------------------------------

type AgentAccess = { connector_scopes?: Record<string, string> | null; connector_account_scopes?: Record<string, string[]> | null } | null | undefined;

// The Gmail accounts this routine's agent may read, active ones only.
export function gmailChoices(agent: AgentAccess, accounts: ComposioAccount[] | null): ComposioAccount[] {
  const scope = agent?.connector_scopes?.gmail;
  if (!accounts || (scope !== "read" && scope !== "labels" && scope !== "write")) return [];
  const allowed = agent?.connector_account_scopes?.gmail ?? [];
  return accountsOf(accounts, "gmail").filter((account) => account.status === "ACTIVE" && allowed.includes(account.id));
}

// The account the routine's own Gmail grants are bound to, when there is one.
export function grantedGmailAccount(grants: ConnectedAppGrant[]): string | null {
  return grants.find((grant) => grant.tool.startsWith("gmail_"))?.account_id ?? null;
}

export type Check = { key: "agent" | "grant" | "receive"; ok: boolean | null; text: string };

// ok null = not known yet (decided by the server when the routine is saved).
export function readiness(input: {
  accountId: string; choices: ComposioAccount[]; grants: ConnectedAppGrant[]; uninterruptedAccess: boolean; status: AppEventStatus | null;
}): Check[] {
  const agentOk = Boolean(input.accountId) && input.choices.some((account) => account.id === input.accountId);
  const bound = input.grants.filter((grant) => grant.tool.startsWith("gmail_"));
  const grantOk = Boolean(input.accountId) && (input.uninterruptedAccess || (bound.length > 0 && bound.every((grant) => grant.account_id === input.accountId)));
  return [
    { key: "agent", ok: agentOk, text: agentOk ? "The agent may read this Gmail account." : "The agent has not been allowed to read this Gmail account. Allow it in the agent's settings." },
    { key: "grant", ok: grantOk, text: grantOk ? "The routine can read Gmail for this account." : "The routine needs a Gmail read action for this same account, or full unattended access, in the sections above." },
    { key: "receive", ok: input.status ? input.status.can_receive : null,
      text: input.status ? (input.status.can_receive ? "This computer can receive new-mail reports." : (input.status.reason || "This computer cannot receive new-mail reports yet, so the routine keeps its schedule."))
        : "Whether this computer can receive new-mail reports is checked when you save." },
  ];
}

// -- how the trigger is doing, in plain words ----------------------------------------------------------------------------

export const STATE_LABELS: Record<AppEventState, string> = {
  active: "Waiting for new mail", paused: "Paused with the routine", error: "Needs attention", removed: "Being removed",
};
export const stateLabel = (state: AppEventState): string => STATE_LABELS[state];

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

// Today's counters as short sentences; counters at zero are left out.
export function todayLines(today: Record<AppEventCounter, number>): string[] {
  const lines: string[] = [];
  if (today.received) lines.push(`${plural(today.received, "new-mail report", "new-mail reports")} came in`);
  if (today.started) lines.push(`${plural(today.started, "run", "runs")} started`);
  if (today.merged) lines.push(`${plural(today.merged, "report", "reports")} added to a run that was already waiting`);
  if (today.duplicate) lines.push(`${plural(today.duplicate, "repeat", "repeats")} ignored`);
  if (today.outside_share) lines.push(`${plural(today.outside_share, "report", "reports")} skipped because the thread is outside this agent's share of the mailbox`);
  if (today.rejected) lines.push(`${plural(today.rejected, "report", "reports")} ignored as unreadable`);
  if (today.unrouted) lines.push(`${plural(today.unrouted, "report", "reports")} matched no routine`);
  if (today.dropped_cap) lines.push(`${plural(today.dropped_cap, "report", "reports")} left to the schedule because the daily limit was reached`);
  if (today.dropped_quiet) lines.push(`${plural(today.dropped_quiet, "report", "reports")} held for the end of quiet hours`);
  return lines;
}

export function lastEventText(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "No new mail reported yet";
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return "No new mail reported yet";
  const minutes = Math.max(0, Math.round((now - at) / 60_000));
  if (minutes < 1) return "Last new mail reported just now";
  if (minutes < 60) return `Last new mail reported ${plural(minutes, "minute", "minutes")} ago`;
  if (minutes < 1440) return `Last new mail reported ${plural(Math.round(minutes / 60), "hour", "hours")} ago`;
  return `Last new mail reported ${plural(Math.round(minutes / 1440), "day", "days")} ago`;
}

export const EXPLANATION = "New mail wakes this routine only when your mail provider reports it; the worker is told which threads and reads them itself. "
  + "It never receives the email text from the notification. If events cannot reach this computer, the routine just keeps its schedule.";
