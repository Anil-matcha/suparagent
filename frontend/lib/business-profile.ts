import type { BusinessProfile, ProfileProposal } from "./api";

// The order the form shows the fields in; the server's list is the source of truth for labels and limits.
export const PROFILE_FIELD_ORDER = ["sells", "audience", "tone", "links", "hours", "never_say"] as const;

const HINTS: Record<string, string> = {
  sells: "For example: hand-poured soy candles, sold online and at weekend markets.",
  audience: "For example: gift buyers and people who like a calm home.",
  tone: "For example: warm and plain, no hype.",
  links: "Your website and the pages you want workers to use.",
  hours: "For example: orders ship Monday to Friday; based in Portland.",
  never_say: "Words, claims or topics to avoid. One per line.",
};

export function profileHint(field: string): string {
  return HINTS[field] ?? "";
}

// The fields in form order, then any the server adds later.
export function profileFieldNames(profile: BusinessProfile): string[] {
  const known = PROFILE_FIELD_ORDER.filter((name) => name in profile.fields);
  const extra = Object.keys(profile.fields).filter((name) => !(PROFILE_FIELD_ORDER as readonly string[]).includes(name));
  return [...known, ...extra];
}

// True when the draft differs from what was saved, ignoring surrounding spaces the server trims.
export function profileDirty(saved: Record<string, string>, draft: Record<string, string>): boolean {
  const names = new Set([...Object.keys(saved), ...Object.keys(draft)]);
  for (const name of names) if ((saved[name] ?? "").trim() !== (draft[name] ?? "").trim()) return true;
  return false;
}

export function profileHasContent(fields: Record<string, string>): boolean {
  return Object.values(fields).some((value) => value.trim().length > 0);
}

// "120 left", or how far over the limit a field is.
export function charsLeftLabel(value: string, limit: number): string {
  const left = limit - value.length;
  return left >= 0 ? `${left.toLocaleString()} left` : `${(-left).toLocaleString()} over`;
}

export function overLimitFields(profile: BusinessProfile, draft: Record<string, string>): string[] {
  return Object.keys(profile.limits).filter((name) => (draft[name] ?? "").length > profile.limits[name]);
}

// "Business profile (2)" while suggestions wait, plain "Business profile" otherwise.
export function profileTabLabel(pending: number): string {
  return pending > 0 ? `Business profile (${pending})` : "Business profile";
}

// "SEO writer suggests a change to Hours and place", or "A worker suggests ..." when the name is missing.
export function proposalTitle(item: ProfileProposal): string {
  return `${item.agent_name.trim() || "A worker"} suggests a change to ${item.label}`;
}

// What the person is about to replace: the field as it was when the suggestion was made, or "(empty)".
export function proposalBefore(item: ProfileProposal): string {
  return item.base_text.trim() || "(empty)";
}

// Oldest first, as the server lists them; an unknown shape gives an empty list.
export function pendingProposals(items: ProfileProposal[] | null | undefined): ProfileProposal[] {
  return Array.isArray(items) ? [...items].sort((a, b) => a.created_at - b.created_at) : [];
}
