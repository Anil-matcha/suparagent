// The labels a support worker added under Worker/ (BL-173): the plain logic of the "Worker labels" panel, apart from the component.
// The server answers with counts and label names only; anything else in an answer is dropped here, never shown.

export type WorkerLabelCount = { name: string; count: number };
export type WorkerLabels = {
  agent_id: number;
  applied: number;
  threads: number;
  labels: WorkerLabelCount[];
  last_applied_at: string | null;
  undoable: number;
  accounts: string[];
};
export type UndoResult = { removed: number; already_removed: number; failed: number };

const count = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 10_000_000;
const MAX_LABELS = 50;
const MAX_NAME = 200;

// A well-formed answer, or null: anything else is an error message, never a half-drawn panel.
export function parseWorkerLabels(raw: unknown): WorkerLabels | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.agent_id !== "number" || !Number.isInteger(r.agent_id) || !count(r.applied) || !count(r.threads) || !count(r.undoable)) return null;
  if (!Array.isArray(r.labels) || r.labels.length > MAX_LABELS) return null;
  const labels: WorkerLabelCount[] = [];
  for (const item of r.labels) {
    if (typeof item !== "object" || item === null) return null;
    const { name, count: n } = item as Record<string, unknown>;
    if (typeof name !== "string" || !name || name.length > MAX_NAME || !count(n)) return null;
    labels.push({ name, count: n });
  }
  const accounts = Array.isArray(r.accounts) ? r.accounts.filter((a): a is string => typeof a === "string" && a.length > 0 && a.length <= 300).slice(0, 20) : [];
  const last = typeof r.last_applied_at === "string" && r.last_applied_at ? r.last_applied_at : null;
  return { agent_id: r.agent_id, applied: r.applied, threads: r.threads, labels, last_applied_at: last, undoable: r.undoable, accounts };
}

export function parseUndoResult(raw: unknown): UndoResult | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (!count(r.removed) || !count(r.already_removed) || !count(r.failed)) return null;
  return { removed: r.removed, already_removed: r.already_removed, failed: r.failed };
}

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

export const isEmpty = (report: WorkerLabels): boolean => report.applied === 0;

// Labels with the most threads first, then by name, so the list does not jump about between refreshes.
export function labelRows(report: WorkerLabels): WorkerLabelCount[] {
  return [...report.labels].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

// When the last label was added, or null for a missing or unreadable time.
export function lastApplied(report: WorkerLabels): Date | null {
  if (!report.last_applied_at) return null;
  const at = new Date(report.last_applied_at);
  return Number.isNaN(at.getTime()) ? null : at;
}

// The question shown inside the panel before anything is removed. It says exactly what Undo touches.
export function undoQuestion(report: WorkerLabels): string {
  const n = report.undoable > 0 ? report.undoable : report.applied;
  return `Remove ${plural(n, "label")} from ${plural(report.threads, "thread")}? Only the Worker/ labels this worker added are removed. Nothing else changes.`;
}

// What happened, in the words the owner needs: what is gone, what was already gone, and what to retry.
export function undoResultText(result: UndoResult): string {
  const base = `Removed ${result.removed}, already gone ${result.already_removed}`;
  return result.failed > 0 ? `${base}, could not remove ${result.failed}; press Undo again to retry those.` : `${base}.`;
}

export function undoErrorText(status: number | undefined, message: string): string {
  if (status === 404) return "That agent is no longer here.";
  return message || "Could not remove the labels. Try again in a moment.";
}

export const loadErrorText = (status: number | undefined, message: string): string =>
  status === 404 ? "That agent is no longer here." : message || "Could not read the worker's labels.";
