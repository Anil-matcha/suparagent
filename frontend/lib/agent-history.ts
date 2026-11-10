// Stored versions of an agent's profile (BL-128): types and the wording for a version's summary.

export type AgentRevision = {
  number: number;
  source: "baseline" | "edit" | "restore" | string;
  actor: string;
  changed_fields: string[];
  redacted: string[];
  restored_from: number | null;
  created_at: string;
};

const FIELD_LABELS: Record<string, string> = {
  name: "name",
  title: "role",
  description: "description",
  instructions: "instructions",
  provider: "engine",
  model: "model",
  effort: "effort",
  workspace_path: "workspace folder",
  approval_mode: "approval mode",
  pinned: "pinned",
  memory_enabled: "notes",
  recall_enabled: "recall",
  memory_upkeep: "memory upkeep",
  compaction_summary: "conversation summaries",
  use_helper: "helper model",
  fallback_provider: "fallback engine",
  fallback_model: "fallback model",
  connector_scopes: "connected apps",
  connector_account_scopes: "connected accounts",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll("_", " ");
}

// "Changed name, instructions and model"; the first version has nothing changed.
export function describeRevision(revision: AgentRevision): string {
  if (revision.source === "baseline") return "Profile before the first recorded change";
  const names = revision.changed_fields.map(fieldLabel);
  const list = names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  const base = `${revision.source === "restore" ? "Restored" : "Changed"} ${list || "profile"}`;
  return revision.source === "restore" && revision.restored_from ? `${base} (from version ${revision.restored_from})` : base;
}

// The newest version is what the agent has now, so it cannot be restored.
export function canRestore(revision: AgentRevision, newest: number | null): boolean {
  return newest !== null && revision.number !== newest;
}
