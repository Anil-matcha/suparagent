// Edit-and-branch: which transcript items can be branched from.
export type BranchableItem = { kind: string; turnId?: number | null; text?: string };

// A user message that came from a turn and has text can be edited into a branch.
export function canBranchFrom(item: BranchableItem): boolean {
  return item.kind === "user" && typeof item.turnId === "number" && !!item.text?.trim();
}
