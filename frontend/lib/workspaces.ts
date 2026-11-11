// The hosted workspace list and switching (the account service's /api/v1/accounts/workspaces). Only a hosted
// deployment has these routes; anywhere else the calls fail and the switcher stays hidden.

export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export type Workspace = {
  id: string;
  name: string;
  role: WorkspaceRole;
  active: boolean;
};

const API = "/api/v1/accounts/workspaces";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Dots-CSRF": "1", ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`workspaces ${response.status}`);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const data = await call<{ workspaces: Workspace[] }>(API);
  return Array.isArray(data.workspaces) ? data.workspaces : [];
}

export async function selectWorkspace(id: string): Promise<void> {
  await call(`${API}/select`, { method: "POST", body: JSON.stringify({ workspaceId: id }) });
}

// The switcher's list: the current workspace first, then recently used ones, then the rest by name. A query narrows
// by name or role (case-insensitive).
export function arrange(workspaces: Workspace[], recent: string[], query = ""): Workspace[] {
  const needle = query.trim().toLowerCase();
  const rank = (w: Workspace) => (w.active ? -1 : recent.includes(w.id) ? recent.indexOf(w.id) : recent.length);
  return workspaces
    .filter((w) => !needle || w.name.toLowerCase().includes(needle) || w.role.includes(needle))
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

export function activeWorkspace(workspaces: Workspace[]): Workspace | null {
  return workspaces.find((w) => w.active) ?? null;
}

export function roleLabel(role: WorkspaceRole): string {
  return role === "owner" ? "Owner" : role === "admin" ? "Admin" : role === "member" ? "Member" : "Viewer";
}

// The keyboard shortcut that opens the switcher: Alt+W (Option+W on a Mac). Matched on the physical key because
// Option changes the character on a Mac.
export function isSwitcherShortcut(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; code: string }): boolean {
  return event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.code === "KeyW";
}
