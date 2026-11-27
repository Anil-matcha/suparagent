export type EditorSection =
  | "overview"
  | "basics"
  | "model"
  | "permissions"
  | "apps"
  | "memory"
  | "advanced";

export type SettingsSectionMeta = {
  id: EditorSection;
  label: string;
  keywords: string[];
  requiresSaved?: boolean;
};

export const SETTINGS_SECTIONS: SettingsSectionMeta[] = [
  {
    id: "overview",
    label: "Overview",
    requiresSaved: true,
    keywords: ["overview", "summary", "checklist", "setup", "prompt preview", "tokens", "bytes", "status"],
  },
  {
    id: "basics",
    label: "Basics",
    keywords: ["name", "title", "description", "instructions", "workspace", "folder", "browse", "reply style", "conversational", "tone"],
  },
  {
    id: "model",
    label: "Model & behavior",
    keywords: ["model", "engine", "provider", "effort", "fallback", "team", "lead", "claude account", "approval", "check work", "verify"],
  },
  {
    id: "permissions",
    label: "Permissions & safety",
    keywords: ["permissions", "approvals", "tool scope", "allow", "deny", "tools off", "outbound", "email", "command rules", "allowlist", "bash"],
  },
  {
    id: "apps",
    label: "Connected apps",
    keywords: ["apps", "connectors", "composio", "gmail", "accounts", "shares", "labels"],
  },
  {
    id: "memory",
    label: "Memory & notes",
    keywords: ["memory", "notes", "starter notes", "memory.md", "recall"],
  },
  {
    id: "advanced",
    label: "Advanced",
    keywords: ["advanced", "cache", "keep warm", "tools off", "helper", "compaction"],
  },
];

export function sectionMatchesQuery(section: SettingsSectionMeta, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (section.label.toLowerCase().includes(q)) return true;
  return section.keywords.some((kw) => kw.includes(q));
}
