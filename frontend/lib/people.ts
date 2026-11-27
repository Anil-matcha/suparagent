// Members of this workspace: roster, invitations, roles and the audit log.
import { request } from "./api";
import type { Permission } from "./permissions";
import type { PersonRole } from "./session";

export type Person = {
  id: number;
  display_name: string;
  role: PersonRole;
  // What they may do (chat, files, approve, rules, agents); admins and the owner have all of them.
  permissions: Permission[];
  // Only admins get addresses and sign-in times.
  email: string | null;
  joined_at: string;
  last_login_at: string | null;
  is_you: boolean;
};

export type InviteRole = Exclude<PersonRole, "owner">;

export type Invite = {
  id: number;
  email: string;
  role: InviteRole;
  permissions: Permission[];
  invited_by: string;
  created_at: string;
  expires_at: string;
  // The link secret: present only in the response that created the invitation.
  token: string | null;
};

export type AuditRow = { id: number; action: string; actor: string; subject: string; detail: string; at: string };

export const ROLE_LABELS: Record<PersonRole, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

export const ROLE_HELP: Record<PersonRole, string> = {
  owner: "Set this workspace up. Cannot be changed or removed.",
  admin: "Manages people, devices and settings, and reaches everything.",
  member: "Chats with agents, uses rooms and files, and creates agents. Cannot change settings or people.",
  viewer: "Can read conversations and rooms but not send or change anything.",
};

export const AUDIT_LABELS: Record<string, string> = {
  "invite.created": "invited",
  "invite.revoked": "revoked the invitation for",
  "member.joined": "joined",
  "member.role_changed": "changed the role of",
  "member.permissions_changed": "changed what they may do for",
  "member.removed": "removed",
  "member.password_changed": "changed the password of",
  "owner.credentials_set": "set up email sign-in for",
  "ownership.transferred": "handed the workspace to",
};

export const people = {
  list: () => request<Person[]>("/people"),
  rename: (display_name: string) => request<Person>("/people/me", { method: "PATCH", body: JSON.stringify({ display_name }) }),
  invites: () => request<Invite[]>("/people/invites"),
  // `permissions` only for a member; leave it out for the usual set.
  invite: (email: string, role: InviteRole, permissions?: Permission[]) =>
    request<Invite>("/people/invites", { method: "POST", body: JSON.stringify({ email, role, ...(permissions ? { permissions } : {}) }) }),
  revokeInvite: (id: number) => request<void>(`/people/invites/${id}`, { method: "DELETE" }),
  update: (id: number, change: { role?: InviteRole; permissions?: Permission[] }) =>
    request<Person>(`/people/${id}`, { method: "PATCH", body: JSON.stringify(change) }),
  changePassword: (body: { new_password: string; current_password?: string; passphrase?: string; email?: string }) =>
    request<void>("/people/me/password", { method: "POST", body: JSON.stringify(body) }),
  transfer: (person_id: number, passphrase: string) =>
    request<Person>("/people/transfer", { method: "POST", body: JSON.stringify({ person_id, passphrase }) }),
  remove: (id: number) => request<void>(`/people/${id}`, { method: "DELETE" }),
  audit: () => request<AuditRow[]>("/people/audit"),
};
