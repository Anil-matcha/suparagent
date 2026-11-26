// Learning from the owner's past replies (BL-174): the plain logic of the opt-in control, the "What the worker learned"
// review panel and the connect card's offer, apart from the components. The server answers with settings and short
// lessons only (never mail, ids or a read cursor); anything else in an answer is dropped here, never shown.
import { accountsOf, type ComposioAccount } from "./connected-apps.ts";

export const STAGE = 30;
export const DEFAULT_BOUND = 100;
export const MONTHS = 6;
export const QUERY_MAX = 200;
export const TEXT_MAX = 600;

export type LessonType = "template" | "tone" | "question" | "policy";
const TYPES: readonly string[] = ["template", "tone", "question", "policy"];

export type HistoryAccount = {
  account_id: string; enabled: boolean; declined: boolean; query: string; max_threads: number; months: number; allowed: number;
  threads_read: number; stage_done: boolean; finished: boolean; started_at: string | null; last_read_at: string | null;
};
export type HistoryLearning = { agent_id: number; accounts: HistoryAccount[]; pending: number; accepted: number };
export type Lesson = {
  id: string; type: LessonType; pattern: string; text: string; count: number; newest: string; oldest: string; status: string;
  destination: string | null; entry_id: string | null;
};
export type Lessons = { pending: Lesson[]; accepted: Lesson[] };
export type ForgetResult = { entries_removed: number; lessons_removed: number };
export type AccountUpdate = {
  enabled?: boolean; declined?: boolean; query?: string; max_threads?: number; months?: number; extend?: number; restart?: boolean;
};

const whole = (value: unknown, max = 10_000_000): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= max;
const text = (value: unknown, max: number): value is string => typeof value === "string" && value.length <= max;
const orNull = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export function parseAccount(raw: unknown): HistoryAccount | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (!text(r.account_id, 300) || !r.account_id || typeof r.enabled !== "boolean" || typeof r.declined !== "boolean" || !text(r.query, QUERY_MAX)) return null;
  if (!whole(r.max_threads, 1000) || !whole(r.months, 24) || !whole(r.allowed, 1000) || !whole(r.threads_read, 100_000)) return null;
  if (typeof r.stage_done !== "boolean" || typeof r.finished !== "boolean") return null;
  return {
    account_id: r.account_id, enabled: r.enabled, declined: r.declined, query: r.query, max_threads: r.max_threads, months: r.months,
    allowed: r.allowed, threads_read: r.threads_read, stage_done: r.stage_done, finished: r.finished,
    started_at: orNull(r.started_at), last_read_at: orNull(r.last_read_at),
  };
}

export function parseHistoryLearning(raw: unknown): HistoryLearning | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.agent_id !== "number" || !Number.isInteger(r.agent_id) || !whole(r.pending, 1000) || !whole(r.accepted, 1000)) return null;
  if (!Array.isArray(r.accounts) || r.accounts.length > 20) return null;
  const accounts: HistoryAccount[] = [];
  for (const item of r.accounts) {
    const parsed = parseAccount(item);
    if (!parsed) return null;
    accounts.push(parsed);
  }
  return { agent_id: r.agent_id, accounts, pending: r.pending, accepted: r.accepted };
}

export function parseLesson(raw: unknown): Lesson | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string" || !r.id || r.id.length > 64 || typeof r.type !== "string" || !TYPES.includes(r.type)) return null;
  if (!text(r.pattern, 200) || !text(r.text, TEXT_MAX + 100) || !whole(r.count, 100_000) || !text(r.newest, 10) || !text(r.oldest, 10)) return null;
  if (typeof r.status !== "string") return null;
  return {
    id: r.id, type: r.type as LessonType, pattern: r.pattern, text: r.text, count: r.count, newest: r.newest, oldest: r.oldest,
    status: r.status, destination: orNull(r.destination), entry_id: orNull(r.entry_id),
  };
}

export function parseLessons(raw: unknown): Lessons | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.pending) || !Array.isArray(r.accepted) || r.pending.length > 100 || r.accepted.length > 200) return null;
  const pending: Lesson[] = [];
  const accepted: Lesson[] = [];
  for (const [source, into] of [[r.pending, pending], [r.accepted, accepted]] as const) {
    for (const item of source) {
      const lesson = parseLesson(item);
      if (!lesson) return null;
      into.push(lesson);
    }
  }
  return { pending, accepted };
}

export function parseForget(raw: unknown): ForgetResult | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  return whole(r.entries_removed) && whole(r.lessons_removed) ? { entries_removed: r.entries_removed, lessons_removed: r.lessons_removed } : null;
}

// -- what the owner is offered ---------------------------------------------------------------------------------------------------

export const OFFER_QUESTION = "Also learn from my past replies?";
export const OFFER_SHORT = `Read-only, one time, last ${DEFAULT_BOUND} threads.`;
export const OFFER_DETAIL =
  `It reads up to the last ${DEFAULT_BOUND} threads or ${MONTHS} months of your sent replies in your support mail, ${STAGE} at a time, and shows ` +
  "you what it learned before using any of it. Your emails are not stored, only short notes you approve. The text of those replies goes to " +
  "the engine the worker runs on, as in any chat.";
export const SEARCH_HELP = "The Gmail label or search that finds your support mail, for example label:support. It never reads the whole mailbox.";
export const CHECK_NOTE = "Names and addresses are not always removed automatically, so read each one before you accept it.";
export const POLICY_NOTE = "A policy observation is only a suggestion: accepting it sends it to your suggestions to decide there. It never becomes a rule.";

// The offer is only for the support worker and only for an app that has a mailbox share (Gmail).
export const canOffer = (supportWorker: boolean, supportsMailbox: boolean): boolean => supportWorker && supportsMailbox;

export function cleanQuery(value: string): string | null {
  const query = value.replace(/\s+/g, " ").trim();
  return query && query.length <= QUERY_MAX ? query : null;
}

// The accounts the offer applies to: the one just connected, else every active account of the app (as the grant does).
export function learnAccountIds(found: readonly ComposioAccount[], app: string, connectedId?: string | null): string[] {
  return connectedId ? [connectedId] : accountsOf([...found], app).filter((account) => account.status === "ACTIVE").map((account) => account.id);
}

// -- the opt-in control ------------------------------------------------------------------------------------------------------------

export type Phase = "off" | "declined" | "reading" | "stage_done" | "finished";
export function phaseOf(account: HistoryAccount): Phase {
  if (!account.enabled) return account.declined ? "declined" : "off";
  if (account.finished) return "finished";
  return account.stage_done ? "stage_done" : "reading";
}

export function progressText(account: HistoryAccount): string {
  if (!account.enabled) return "Not reading your past replies.";
  const read = `${account.threads_read} of ${account.allowed} allowed threads read (up to ${account.max_threads} in all)`;
  const phase = phaseOf(account);
  if (phase === "finished") return `Done: ${account.threads_read} threads read. Nothing more is read unless you start again.`;
  if (phase === "stage_done") return `This stage is finished: ${read}. The worker will not read more until you allow it.`;
  return `${read}. The worker reads a batch when you next ask it to learn from your replies.`;
}

export type ExtendChoice = { extend: number; label: string };
// More threads the owner may allow: +30, +70, or all the way to the limit. Never past the owner's own bound.
export function extendChoices(account: HistoryAccount): ExtendChoice[] {
  if (!account.enabled || account.finished) return [];
  const room = account.max_threads - account.allowed;
  if (room <= 0) return [];
  const choices: ExtendChoice[] = [];
  for (const step of [STAGE, 70]) if (step < room) choices.push({ extend: step, label: `Allow ${step} more` });
  choices.push({ extend: room, label: room === STAGE ? `Allow ${room} more` : `Allow ${room} more (to the limit)` });
  return choices;
}

// -- the review panel --------------------------------------------------------------------------------------------------------------

export const lessonLabel = (type: LessonType): string =>
  ({ template: "Answer template", tone: "Tone", question: "Recurring question", policy: "Policy observation" })[type];

export function lessonCaption(lesson: Lesson): string {
  const times = lesson.count === 1 ? "once" : `${lesson.count} times`;
  return lesson.newest === lesson.oldest ? `Seen ${times}, on ${lesson.newest}` : `Seen ${times}, from ${lesson.oldest} to ${lesson.newest}`;
}

export function destinationText(lesson: Lesson): string {
  if (lesson.destination === "answer-library") return "Added to the answer library";
  if (lesson.destination === "suggestions") return "Sent to your suggestions";
  return "Accepted";
}

// Pending lessons by kind, in a fixed order, so the list does not jump about.
export function groupLessons(lessons: readonly Lesson[]): { type: LessonType; items: Lesson[] }[] {
  const order: LessonType[] = ["template", "question", "tone", "policy"];
  return order.map((type) => ({ type, items: lessons.filter((lesson) => lesson.type === type) })).filter((group) => group.items.length > 0);
}

export function acceptText(lesson: Lesson): string {
  return lesson.type === "policy" ? "Send to my suggestions" : "Accept";
}

export function forgetQuestion(): string {
  return "Remove everything learned from your past replies? That deletes the waiting lessons and the notes marked 'from history' in the answer library, and it can be undone from the notes history.";
}

export function forgetResultText(result: ForgetResult): string {
  return `Removed ${result.entries_removed} note${result.entries_removed === 1 ? "" : "s"} and ${result.lessons_removed} lesson${result.lessons_removed === 1 ? "" : "s"}.`;
}

export function errorText(status: number | undefined, message: string): string {
  if (status === 403) return "Only the owner can manage this, from the web app.";
  if (status === 404) return "That agent or lesson is no longer here.";
  if (status === 409 || status === 413) return message || "The notes could not take that. Try again.";
  return message || "That did not work. Try again.";
}
