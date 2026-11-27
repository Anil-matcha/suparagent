// The "Shadow results" panel for a support worker (BL-173): the plain logic, apart from the component.
// The server compares what the worker would have done (its ranking and drafts, kept in its notes) with what the owner
// actually did, and sends only numbers and thread ids: never any mail text.

export type DisagreementKind = "ranked_high_unanswered" | "answered_fast_ranked_low";
export type Rate = { rate: number | null; n: number };
export type Disagreement = { thread_id: string; kind: DisagreementKind; rank: number; category: string; predicted_at: string };
export type ShadowReport = {
  agent_id: number;
  window_days: number;
  generated_at: string;
  sample: { predictions: number; with_owner_reply: number; drafts: number };
  ranking: { top5_answered_24h: Rate; baseline_answered_24h: Rate; too_few: boolean };
  drafts: { as_is: number; light_edit: number; rewrite: number; no_reply_seen: number; usable_rate: number | null; too_few: boolean };
  disagreements: Disagreement[];
  accounts: string[];
};

const KINDS: readonly string[] = ["ranked_high_unanswered", "answered_fast_ranked_low"];
const count = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0;
const rateOrNull = (value: unknown): value is number | null => value === null || (typeof value === "number" && value >= 0 && value <= 1);
const isRate = (value: unknown): value is Rate => {
  const v = value as Rate | null;
  return typeof v === "object" && v !== null && rateOrNull(v.rate) && count(v.n);
};

// A well-formed answer, or null: anything else is reported as unreadable instead of being drawn half-way.
export function parseShadowReport(raw: unknown): ShadowReport | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const sample = r.sample as Record<string, unknown> | null | undefined;
  const ranking = r.ranking as Record<string, unknown> | null | undefined;
  const drafts = r.drafts as Record<string, unknown> | null | undefined;
  if (!count(r.agent_id) || !count(r.window_days) || typeof r.generated_at !== "string") return null;
  if (!sample || !count(sample.predictions) || !count(sample.with_owner_reply) || !count(sample.drafts)) return null;
  if (!ranking || !isRate(ranking.top5_answered_24h) || !isRate(ranking.baseline_answered_24h) || typeof ranking.too_few !== "boolean") return null;
  if (!drafts || !count(drafts.as_is) || !count(drafts.light_edit) || !count(drafts.rewrite) || !count(drafts.no_reply_seen)
      || !rateOrNull(drafts.usable_rate) || typeof drafts.too_few !== "boolean") return null;
  if (!Array.isArray(r.disagreements) || !Array.isArray(r.accounts)) return null;
  const disagreements: Disagreement[] = [];
  for (const entry of r.disagreements as unknown[]) {
    const item = entry as Record<string, unknown> | null;
    if (typeof item !== "object" || item === null || typeof item.thread_id !== "string" || typeof item.kind !== "string" || !KINDS.includes(item.kind)
        || !count(item.rank) || typeof item.category !== "string" || typeof item.predicted_at !== "string") return null;
    disagreements.push({ thread_id: item.thread_id, kind: item.kind as DisagreementKind, rank: item.rank, category: item.category, predicted_at: item.predicted_at });
  }
  const top5 = ranking.top5_answered_24h, baseline = ranking.baseline_answered_24h;
  return {
    agent_id: r.agent_id, window_days: r.window_days, generated_at: r.generated_at,
    sample: { predictions: sample.predictions, with_owner_reply: sample.with_owner_reply, drafts: sample.drafts },
    ranking: { top5_answered_24h: { rate: top5.rate, n: top5.n }, baseline_answered_24h: { rate: baseline.rate, n: baseline.n }, too_few: ranking.too_few },
    drafts: { as_is: drafts.as_is, light_edit: drafts.light_edit, rewrite: drafts.rewrite, no_reply_seen: drafts.no_reply_seen,
              usable_rate: drafts.usable_rate, too_few: drafts.too_few },
    disagreements, accounts: (r.accounts as unknown[]).filter((account): account is string => typeof account === "string"),
  };
}

// Nothing recorded yet: the worker has not ranked anything in this window.
export const isEmpty = (report: ShadowReport): boolean => report.sample.predictions === 0;

// "62%", or a dash when there is no rate. Never invents a number.
export function percent(rate: number | null): string {
  return rate === null || !Number.isFinite(rate) ? "–" : `${Math.round(rate * 100)}%`;
}

// The rate with its sample size ("62% (n = 13)"), or a plain "too few to judge" in place of a percentage.
export function rateText(value: Rate, tooFew: boolean): string {
  return tooFew || value.rate === null ? `too few to judge (n = ${value.n})` : `${percent(value.rate)} (n = ${value.n})`;
}

const CATEGORIES: Record<string, string> = {
  urgent: "urgent", angry: "angry customer", at_risk: "at risk of leaving", blocked: "blocked",
  billing: "billing", bug: "bug", question: "question", other: "other",
};
export function categoryLabel(category: string): string {
  return CATEGORIES[category] ?? (category.replace(/_/g, " ").slice(0, 40) || "other");
}

// One short plain sentence per disagreement.
export function disagreementText(item: Disagreement): string {
  const what = categoryLabel(item.category);
  return item.kind === "ranked_high_unanswered"
    ? `The worker ranked this #${item.rank} (${what}), but you have not answered it.`
    : `You answered this quickly, but the worker ranked it #${item.rank} (${what}).`;
}

export function sampleCaption(report: ShadowReport): string {
  const { predictions, with_owner_reply } = report.sample;
  const threads = `${predictions} ${predictions === 1 ? "thread" : "threads"}`;
  return `${threads} ranked in the last ${report.window_days} days; you replied to ${with_owner_reply}.`;
}

// The calm message for a refresh that the server limited, else the error's own words.
export function refreshMessage(status: number | undefined, message: string): string {
  return status === 429 ? "Just refreshed. Try again in a minute." : message || "Could not load the shadow results.";
}
