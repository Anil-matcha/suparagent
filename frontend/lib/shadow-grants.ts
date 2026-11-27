// The shadow run (BL-173): a routine that reads support mail, records how it ranks it and adds its own labels under
// Worker/, on a schedule or when new mail arrives, and can do nothing else. The plain logic behind the routine editor's
// "Shadow run" choices; the server enforces the same rules when the routine is saved and again on every call.

export type ShadowGrant = { tool: string; account_id: string };

export const MARK_TOOL = "gmail_triage_mark";
export const LABEL_TOOL = "gmail_apply_worker_label";
export const LIST_LABELS_TOOL = "gmail_list_labels";
export const SHADOW_READ_TOOLS = ["gmail_get_profile", "gmail_fetch_emails", "gmail_fetch_thread", "gmail_fetch_message"] as const;

// What a shadow run may hold: Gmail reads, the ranking record, and the label tool. Anything else makes it a different routine.
const ALLOWED = new Set<string>([...SHADOW_READ_TOOLS, MARK_TOOL, LIST_LABELS_TOOL, LABEL_TOOL]);

export const SHADOW_TOOL_LABELS: Record<string, string> = {
  [MARK_TOOL]: "Record how it ranks each thread (stored in this app only)",
  [LABEL_TOOL]: "Add its own labels under Worker/ (never removes, archives, deletes or sends)",
};

export type ShadowOption = { tool: string; label: string; allowed: boolean; note: string | null };

const canRead = (level: string | undefined) => level === "read" || level === "labels" || level === "write";
const canLabel = (level: string | undefined) => level === "labels" || level === "write";

// The two shadow actions as the editor lists them for an agent's Gmail level: recording needs only read access, labelling needs
// the labels level (or more). A choice the level does not allow is still shown, with the reason.
export function shadowOptions(level: string | undefined): ShadowOption[] {
  return [
    { tool: MARK_TOOL, label: SHADOW_TOOL_LABELS[MARK_TOOL] as string, allowed: canRead(level), note: canRead(level) ? null : "allow this agent to read Gmail first" },
    { tool: LABEL_TOOL, label: SHADOW_TOOL_LABELS[LABEL_TOOL] as string, allowed: canLabel(level),
      note: canLabel(level) ? null : "allow this agent to read and label mail under Worker/ in its settings first" },
  ];
}

export function isShadowTool(tool: string): boolean {
  return tool === MARK_TOOL || tool === LABEL_TOOL;
}

export function isShadowSelection(grants: readonly ShadowGrant[]): boolean {
  return grants.some((grant) => isShadowTool(grant.tool));
}

// The grants a shadow run starts with for one Gmail account: the reads, plus the ranking record, plus the label tool when the
// agent's level allows it. It replaces every other grant, because a shadow run holds nothing else.
export function shadowPreset(level: string | undefined, accountId: string): ShadowGrant[] {
  if (!accountId || !canRead(level)) return [];
  const tools: string[] = [...SHADOW_READ_TOOLS, MARK_TOOL];
  if (canLabel(level)) tools.push(LABEL_TOOL);
  return tools.map((tool) => ({ tool, account_id: accountId }));
}

// Why these grants cannot be saved as a shadow run, in plain words, or null. Mirrors the server's rule.
export function shadowProblem(grants: readonly ShadowGrant[]): string | null {
  if (!isShadowSelection(grants)) return null;
  if (grants.some((grant) => !ALLOWED.has(grant.tool))) {
    return "A shadow run holds only Gmail reads, the ranking record and its Worker/ labels. Remove the other actions, such as sending, drafting or another app, or remove the shadow actions.";
  }
  if (new Set(grants.map((grant) => grant.account_id)).size > 1) {
    return "A shadow run uses one Gmail account. Use the same account for every action.";
  }
  return null;
}

// Whether to suggest the shadow run: a routine of the Customer support worker that holds no app actions yet.
export function suggestShadow(supportWorker: boolean, grants: readonly ShadowGrant[], level: string | undefined): boolean {
  return supportWorker && grants.length === 0 && canRead(level);
}

// The two shadow actions as [tool, label] pairs, the shape the editor's other tool lists use.
export const SHADOW_TOOL_PAIRS: readonly (readonly [string, string])[] = [
  [MARK_TOOL, SHADOW_TOOL_LABELS[MARK_TOOL] as string],
  [LABEL_TOOL, SHADOW_TOOL_LABELS[LABEL_TOOL] as string],
];
