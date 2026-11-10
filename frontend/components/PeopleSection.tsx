"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { BASE_PATH } from "@/lib/paths";
import {
  AUDIT_LABELS,
  people,
  ROLE_HELP,
  ROLE_LABELS,
  type AuditRow,
  type Invite,
  type InviteRole,
  type Person,
} from "@/lib/people";
import { inviteLink } from "@/lib/session";
import { useSession } from "./AuthGate";
import { Badge } from "@/components/ui/Badge";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import { SkeletonList } from "@/components/ui/Skeleton";

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

function failure(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

const INVITE_ROLES: readonly InviteRole[] = ["member", "viewer", "admin"];

// Shown once, right after an invitation is made: the secret is not kept in readable form.
function NewInvite({ invite, onDone }: { invite: Invite & { token: string }; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const link = inviteLink(window.location.origin, BASE_PATH, invite);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="space-y-3 rounded-md border border-line p-4" aria-live="polite" data-testid="new-invite">
      <p className="text-sm">
        Send this link to <strong>{invite.email}</strong>. It works once, only for that address, and expires {when(invite.expires_at)}.
        It is shown only now.
      </p>
      <p className="break-all rounded bg-inset p-2 font-mono text-xs" data-testid="invite-link">{link}</p>
      <div className="flex gap-2">
        <Button type="button" onClick={copy}>{copied ? "Copied" : "Copy link"}</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Done</Button>
      </div>
    </div>
  );
}

// Everyone who works in this workspace. All members see names and roles; admins invite and manage.
export function PeopleSection() {
  const { session } = useSession();
  const [roster, setRoster] = useState<Person[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [created, setCreated] = useState<(Invite & { token: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InviteRole>("member");
  const [showAudit, setShowAudit] = useState(false);

  const admin = session !== null && session.kind !== "desktop" && session.scopes.includes("admin");
  const isOwner = session?.role === "owner" || (session !== null && session.role == null && admin);
  const present = session !== null && session.kind !== "desktop";

  const load = useCallback(async () => {
    try {
      setRoster(await people.list());
      if (admin) setInvites(await people.invites());
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setUnavailable(true);
      else setError(failure(err, "Could not load the people in this workspace."));
    }
  }, [admin]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (present) void load();
  }, [present, load]);

  if (!present || unavailable) return null;

  const act = async (work: () => Promise<void>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      await load();
      if (showAudit) setAudit(await people.audit());
    } catch (err) {
      setError(failure(err, fallback));
    } finally {
      setBusy(false);
    }
  };

  const invite = () => act(async () => {
    const made = await people.invite(email, role);
    setCreated(made as Invite & { token: string });
    setEmail("");
  }, "Could not create the invitation.");

  const toggleAudit = async () => {
    const next = !showAudit;
    setShowAudit(next);
    if (next) {
      try { setAudit(await people.audit()); } catch (err) { setError(failure(err, "Could not load the history.")); }
    }
  };

  const remove = (person: Person) => {
    if (confirming !== person.id) { setConfirming(person.id); return; }
    setConfirming(null);
    void act(() => people.remove(person.id), `Could not remove ${person.display_name}.`);
  };

  // An admin changes members and viewers; only the owner touches admins.
  const canManage = (person: Person) => admin && !person.is_you && person.role !== "owner" && (person.role !== "admin" || isOwner);

  return (
    <section className="space-y-4 rounded-lg border border-line p-5" aria-labelledby="people-heading">
      <h3 id="people-heading" className="font-semibold">People</h3>
      <p className="text-xs text-muted">
        Everyone here works in the same workspace and sees the same agents, rooms and conversations.
        {session?.display_name ? ` You are signed in as ${session.display_name}${session.role ? ` (${ROLE_LABELS[session.role]})` : ""}.` : ""}
      </p>
      {error && <Banner tone="danger">{error}</Banner>}

      {roster === null ? <SkeletonList rows={3} /> : (
        <ul className="divide-y divide-line" data-testid="people-list">
          {roster.map((person) => (
            <li key={person.id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="person-row">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {person.display_name}
                  {person.is_you && <span className="ml-2 rounded bg-inset px-1.5 py-0.5 text-xs font-normal">you</span>}
                </p>
                <p className="text-xs text-muted">
                  {person.email ?? "No email"}{person.email ? " · " : ""}joined {when(person.joined_at)}
                  {person.last_login_at ? ` · last sign-in ${when(person.last_login_at)}` : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {canManage(person) ? (
                  <Select aria-label={`Role of ${person.display_name}`} value={person.role} disabled={busy}
                    onChange={(e) => void act(async () => { await people.setRole(person.id, e.target.value as InviteRole); }, "Could not change that role.")}>
                    {INVITE_ROLES.filter((r) => r !== "admin" || isOwner || person.role === "admin")
                      .map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                  </Select>
                ) : (
                  <Badge>{ROLE_LABELS[person.role]}</Badge>
                )}
                {canManage(person) && (
                  <Button type="button" disabled={busy} onClick={() => remove(person)} aria-label={`Remove ${person.display_name}`}>
                    {confirming === person.id ? "Confirm remove" : "Remove"}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {admin && (
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Invite someone</h4>
          {created ? (
            <NewInvite invite={created} onDone={() => setCreated(null)} />
          ) : (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void invite(); }}>
              <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
                <div className="space-y-1">
                  <label htmlFor="invite-email" className="block text-sm font-medium">Email</label>
                  <Input id="invite-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                    placeholder="teammate@example.com" required maxLength={254} autoComplete="off" />
                </div>
                <div className="space-y-1">
                  <label htmlFor="invite-role" className="block text-sm font-medium">Role</label>
                  <Select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as InviteRole)}>
                    {INVITE_ROLES.filter((r) => r !== "admin" || isOwner).map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                  </Select>
                </div>
              </div>
              <p className="text-xs text-muted">{ROLE_HELP[role]}</p>
              <Button type="submit" disabled={busy || !email.trim()}>Create invitation link</Button>
            </form>
          )}

          {invites.length > 0 && (
            <div>
              <h4 className="mb-1 text-sm font-medium">Waiting to join</h4>
              <ul className="divide-y divide-line" data-testid="invite-list">
                {invites.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <p className="min-w-0 truncate text-sm">
                      {item.email} <span className="text-xs text-muted">· {ROLE_LABELS[item.role]} · expires {when(item.expires_at)}</span>
                    </p>
                    <Button type="button" disabled={busy || (item.role === "admin" && !isOwner)} aria-label={`Revoke the invitation for ${item.email}`}
                      onClick={() => void act(() => people.revokeInvite(item.id), "Could not revoke that invitation.")}>
                      Revoke
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <Button type="button" variant="ghost" onClick={toggleAudit} aria-expanded={showAudit}>
              {showAudit ? "Hide history" : "Show history"}
            </Button>
            {showAudit && (audit === null ? <SkeletonList rows={3} /> : audit.length === 0 ? (
              <p className="mt-2 text-sm text-muted">Nothing has changed yet.</p>
            ) : (
              <ul className="mt-2 divide-y divide-line" data-testid="audit-list">
                {audit.map((row) => (
                  <li key={row.id} className="py-1.5 text-sm">
                    <span className="font-medium">{row.actor || "Someone"}</span> {AUDIT_LABELS[row.action] ?? row.action} <span className="font-medium">{row.subject}</span>
                    {row.detail && <span className="text-muted"> ({row.detail})</span>}
                    <span className="block text-xs text-muted">{when(row.at)}</span>
                  </li>
                ))}
              </ul>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
