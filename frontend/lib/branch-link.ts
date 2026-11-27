// Edit-and-branch: which transcript items can be branched from.
export type BranchableItem = { kind: string; turnId?: number | null; text?: string; incomplete?: boolean };

// A user message that came from a turn and has text can be edited into a branch.
export function canBranchFrom(item: BranchableItem): boolean {
  return item.kind === "user" && typeof item.turnId === "number" && !!item.text?.trim();
}

// Any completed user or assistant message tied to a turn can be branched side-by-side.
export function canForkFrom(item: BranchableItem): boolean {
  if (typeof item.turnId !== "number") return false;
  if (item.kind === "user") return !!item.text?.trim();
  if (item.kind === "assistant") return !item.incomplete && !!item.text?.trim();
  return false;
}
