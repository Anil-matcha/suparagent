// Feedback on what a support worker predicted, and the suggestion to raise or lower its share of the mailbox (BL-175): the plain
// logic, apart from the components. The server sends numbers and thread ids, never mail text, and the suggestion is only a
// proposal: nothing here changes a share without the owner pressing the button.
import { sharesToSend, shareOf, withShare } from "./connected-apps.ts";
import type { AccountScopes, Scopes, Shares } from "./connected-apps.ts";

export type FeedbackKind = "draft" | "ranking";
export type FeedbackReason = "wrong_tone" | "wrong_policy" | "missing_context" | "wrong_priority" | "other";
export type FeedbackBody = { thread_id: string; kind: FeedbackKind; reason: FeedbackReason; note?: string };
export type FeedbackResult = { thread_id: string; kind: FeedbackKind; reason: FeedbackReason; recorded: true };

export const FEEDBACK_REASONS: readonly { value: FeedbackReason; label: string }[] = [
  { value: "wrong_tone", label: "Wrong tone" },
  { value: "wrong_policy", label: "Wrong policy" },
  { value: "missing_context", label: "Missing context" },
  { value: "wrong_priority", label: "Wrong priority" },
  { value: "other", label: "Something else" },
];
const REASONS: readonly string[] = FEEDBACK_REASONS.map((entry) => entry.value);

export const reasonLabel = (reason: string): string => FEEDBACK_REASONS.find((entry) => entry.value === reason)?.label ?? "Something else";

export function parseFeedbackResult(raw: unknown): FeedbackResult | null {
  const r = raw as Record<string, unknown> | null;
  if (typeof r !== "object" || r === null || typeof r.thread_id !== "string" || !r.thread_id || r.recorded !== true) return null;
  if (r.kind !== "draft" && r.kind !== "ranking") return null;
  if (typeof r.reason !== "string" || !REASONS.includes(r.reason)) return null;
  return { thread_id: r.thread_id, kind: r.kind, reason: r.reason as FeedbackReason, recorded: true };
}

export type SuggestionState = "none" | "raise" | "lower";
export type SuggestionEvidence = {
  evaluated: number; usable: number; usable_rate: number | null; lower: number | null; upper: number | null; days: number; wrong_policy: number;
};
export type ShareSuggestion = {
  agent_id: number; state: SuggestionState; account_id: string | null; current_percent: number; suggested_percent: number | null;
  evidence: SuggestionEvidence; reason: string;
};

const whole = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0;
const percentValue = (value: unknown): value is number => whole(value) && value >= 1 && value <= 100;
const unit = (value: unknown): value is number | null => value === null || (typeof value === "number" && value >= 0 && value <= 1);

// A well-formed suggestion, or null. A raise must point upward and a lower downward, and either needs the account to act on:
// anything else is refused, so a card can never offer a change the server did not mean.
export function parseShareSuggestion(raw: unknown): ShareSuggestion | null {
  const r = raw as Record<string, unknown> | null;
  if (typeof r !== "object" || r === null || !whole(r.agent_id) || typeof r.reason !== "string") return null;
  if (r.state !== "none" && r.state !== "raise" && r.state !== "lower") return null;
  if (!(r.account_id === null || (typeof r.account_id === "string" && r.account_id))) return null;
  if (!percentValue(r.current_percent)) return null;
  const e = r.evidence as Record<string, unknown> | null;
  if (typeof e !== "object" || e === null || !whole(e.evaluated) || !whole(e.usable) || !unit(e.usable_rate) || !unit(e.lower) || !unit(e.upper)
      || !whole(e.days) || !whole(e.wrong_policy)) return null;
  let suggested: number | null = null;
  if (r.state !== "none") {
    if (!percentValue(r.suggested_percent) || r.account_id === null) return null;
    if (r.state === "raise" ? r.suggested_percent <= r.current_percent : r.suggested_percent >= r.current_percent) return null;
    suggested = r.suggested_percent;
  }
  return {
    agent_id: r.agent_id, state: r.state, account_id: r.account_id as string | null, current_percent: r.current_percent, suggested_percent: suggested,
    evidence: { evaluated: e.evaluated, usable: e.usable, usable_rate: e.usable_rate, lower: e.lower, upper: e.upper, days: e.days, wrong_policy: e.wrong_policy },
    reason: r.reason,
  };
}

export const isActionable = (s: ShareSuggestion | null): s is ShareSuggestion & { suggested_percent: number; account_id: string } =>
  s !== null && s.state !== "none" && s.suggested_percent !== null && s.account_id !== null;

const pct = (value: number) => `${Math.round(value * 100)}%`;

// "64% to 95%": the range the rate could really be, so a single number is never shown alone.
export function rangeText(e: SuggestionEvidence): string | null {
  return e.lower === null || e.upper === null ? null : `${pct(e.lower)} to ${pct(e.upper)}`;
}

export function suggestionHeadline(s: ShareSuggestion): string {
  const e = s.evidence;
  if (s.state === "raise") {
    return `Your worker's drafts were used as written or lightly edited ${e.usable} of ${e.evaluated} times. You could raise its share from ${s.current_percent}% to ${s.suggested_percent}%.`;
  }
  if (s.state === "lower") {
    return `Only ${e.usable} of ${e.evaluated} of your worker's drafts were used as written or lightly edited. You could lower its share from ${s.current_percent}% to ${s.suggested_percent}%.`;
  }
  return s.reason;
}

export const actionLabel = (s: ShareSuggestion): string => `${s.state === "raise" ? "Raise" : "Lower"} to ${s.suggested_percent}%`;

// "Not now" is remembered in this browser for two weeks, per agent, account and proposed step, so the same card is not repeated.
export const DISMISS_DAYS = 14;
export const dismissalKey = (s: ShareSuggestion): string => `share-suggestion-dismissed:${s.agent_id}:${s.account_id}:${s.state}:${s.suggested_percent}`;
export function isDismissed(storage: Pick<Storage, "getItem"> | null | undefined, s: ShareSuggestion, now = Date.now()): boolean {
  try {
    const at = Number(storage?.getItem(dismissalKey(s)));
    return Number.isFinite(at) && at > 0 && now - at < DISMISS_DAYS * 86_400_000;
  } catch { return false; }
}
export function dismiss(storage: Pick<Storage, "setItem"> | null | undefined, s: ShareSuggestion, now = Date.now()): void {
  try { storage?.setItem(dismissalKey(s), String(now)); } catch { /* the card simply shows again */ }
}

type AgentShares = { provider: string; connector_scopes?: Scopes | null; connector_account_scopes?: AccountScopes | null; connector_shares?: Shares | null };

// What to send to the agent update to carry out an accepted suggestion: the agent's own shares with ONE account's percent moved,
// in the same form the settings control saves (the server keeps the seed, so the threads already in the share stay in it).
// Refuses, in plain words, when the agent no longer matches what the suggestion was worked out for.
export function sharePayload(agent: AgentShares, s: ShareSuggestion): { ok: true; connector_shares: Shares } | { ok: false; message: string } {
  if (!isActionable(s)) return { ok: false, message: "There is nothing to change." };
  if (!(agent.connector_account_scopes?.gmail ?? []).includes(s.account_id)) return { ok: false, message: "This worker no longer uses that Gmail account." };
  if (shareOf(agent.connector_shares, "gmail", s.account_id) !== s.current_percent) {
    return { ok: false, message: "The share changed since this was worked out. Refresh and look again." };
  }
  const next = withShare(agent.connector_shares, "gmail", s.account_id, s.suggested_percent);
  return { ok: true, connector_shares: sharesToSend(next, agent.connector_account_scopes, agent.connector_scopes, agent.provider) };
}

// A calm message for a failed send.
export function feedbackError(status: number | undefined, message: string): string {
  if (status === 403) return "That thread is outside this worker's current share, so feedback on it is not possible.";
  if (status === 404) return "The worker has no recorded prediction for that thread.";
  return message || "Could not send that feedback.";
}
