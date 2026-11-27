// What a member may do, chosen when they are invited and editable later. No runtime imports, so node tests
// can load this. The server is the authority; this only draws the choices and hides what would be refused.

export type Permission = "chat" | "files" | "approve" | "rules" | "agents";
export type MemberRole = "admin" | "member" | "viewer";

export const PERMISSION_INFO: readonly { id: Permission; label: string; help: string }[] = [
  { id: "chat", label: "Reply", help: "Send messages to agents and in rooms. Without it, they can only read." },
  { id: "files", label: "Read files", help: "Open the files in a conversation's workspace." },
  { id: "approve", label: "Answer commands", help: "Allow or deny a command an agent asks about, one at a time." },
  { id: "rules", label: "Save standing rules", help: "Tick “always allow”, which applies to everyone in the workspace. Only the owner can give this." },
  { id: "agents", label: "Manage agents", help: "Create and edit agents and rooms." },
];

export const ALL_PERMISSIONS: readonly Permission[] = PERMISSION_INFO.map((p) => p.id);

// What a member gets when nobody chose (matches the server's default).
export const DEFAULT_MEMBER_PERMISSIONS: readonly Permission[] = ["chat", "files", "approve", "agents"];

export type AccessPreset = { id: "member" | "reply" | "viewer" | "admin"; label: string; help: string; role: MemberRole; permissions: readonly Permission[] };

export const ACCESS_PRESETS: readonly AccessPreset[] = [
  { id: "member", label: "Member", help: "Chat, read files, answer commands and manage agents.", role: "member", permissions: DEFAULT_MEMBER_PERMISSIONS },
  { id: "reply", label: "Reply only", help: "Can talk to agents and in rooms. Cannot answer commands, read files or manage agents.", role: "member", permissions: ["chat"] },
  { id: "viewer", label: "Viewer", help: "Can read conversations and rooms but not send or change anything.", role: "viewer", permissions: [] },
  { id: "admin", label: "Admin", help: "Everything, including people and settings.", role: "admin", permissions: ALL_PERMISSIONS },
];

const sameSet = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x) => b.includes(x));

/** The preset that matches a role and permission set, or "custom" for a member with their own mix. */
export function presetOf(role: string, permissions: readonly string[]): AccessPreset["id"] | "custom" {
  if (role === "admin" || role === "owner") return "admin";
  if (role === "viewer") return "viewer";
  const found = ACCESS_PRESETS.find((p) => p.role === "member" && sameSet(p.permissions, permissions));
  return found ? found.id : "custom";
}

export function togglePermission(current: readonly Permission[], permission: Permission): Permission[] {
  const next = current.includes(permission) ? current.filter((p) => p !== permission) : [...current, permission];
  return ALL_PERMISSIONS.filter((p) => next.includes(p));
}

/** Whether a signed-in member may use a permission. Sessions that are not members (permissions null or
 * missing: a paired device, the desktop app) keep the access their scopes give them. */
export function can(permissions: readonly string[] | null | undefined, permission: Permission): boolean {
  return permissions == null || permissions.includes(permission);
}

export function describePermissions(role: string, permissions: readonly string[]): string {
  if (role === "owner") return "Everything";
  const preset = presetOf(role, permissions);
  if (preset !== "custom") return ACCESS_PRESETS.find((p) => p.id === preset)!.label;
  return permissions.length ? PERMISSION_INFO.filter((p) => permissions.includes(p.id)).map((p) => p.label).join(", ") : "Read only";
}
