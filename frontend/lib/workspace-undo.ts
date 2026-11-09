// Undo a task's file changes (BL-102). The backend takes a snapshot before each turn
// (id "turn-<id>") and one before each undo (id "undo-…"); this picks what "undo" rewinds to.

export type WorkspaceSnapshot = { id: string; label: string; created: number; files: number };
export type RestoreResult = { snapshot: string; restored: number; removed: number; unchanged: number; undo_snapshot?: string | null };

// The newest snapshot taken before a task ran (the list arrives newest first, but do not rely on it).
// Snapshots taken before an undo are not task starts, so they are never offered.
export function latestTaskSnapshot(snapshots: readonly WorkspaceSnapshot[]): WorkspaceSnapshot | null {
  let best: WorkspaceSnapshot | null = null;
  for (const s of snapshots) {
    if (!s.id.startsWith("turn-")) continue;
    if (best === null || s.created > best.created) best = s;
  }
  return best;
}

// Undo is offered only when a conversation exists, nothing is running, and no request is in flight.
export function canUndo(opts: { threadId: number | null; turnRunning: boolean; busy: boolean }): boolean {
  return opts.threadId !== null && !opts.turnRunning && !opts.busy;
}

// How many files an undo touches, for the confirm text.
export function undoImpact(result: Pick<RestoreResult, "restored" | "removed">): number {
  return result.restored + result.removed;
}
