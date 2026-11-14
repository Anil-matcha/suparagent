"use client";

import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { people, type Person } from "@/lib/people";
import { withBase } from "@/lib/paths";
import type { SessionInfo } from "@/lib/session";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Input";

function failure(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// The signed-in person's own name and password. The owner of an install that predates people has no
// email or password yet: they set both once, confirmed with the owner passphrase, so they can sign in
// as an ordinary member (and hand the workspace over).
export function YourAccount({ session, me, onChanged }: { session: SessionInfo; me: Person | null; onChanged: () => void }) {
  const [name, setName] = useState(session.display_name ?? "");
  const [email, setEmail] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  if (session.person_id == null || !me) return null;
  const needsSetup = me.role === "owner" && !me.email;

  const run = async (work: () => Promise<void>, done: string, fallback: string) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await work();
      setNote(done);
      onChanged();
    } catch (err) {
      setError(failure(err, fallback));
    } finally {
      setBusy(false);
    }
  };

  const saveName = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => { await people.rename(name); }, "Name saved.", "Could not save your name.");
  };

  const savePassword = (event: FormEvent) => {
    event.preventDefault();
    if (next !== repeat) { setError("The two passwords are different."); return; }
    void run(async () => {
      await people.changePassword(needsSetup
        ? { new_password: next, email, passphrase }
        : { new_password: next, current_password: current });
      setCurrent(""); setNext(""); setRepeat(""); setPassphrase("");
    }, needsSetup ? "Email sign-in is set up. Other signed-in browsers were signed out." : "Password changed. Other signed-in browsers were signed out.",
    "Could not change the password.");
  };

  return (
    <div className="space-y-4 rounded-md border border-line p-4" data-testid="your-account">
      <h4 className="text-sm font-medium">Your account</h4>
      {error && <Banner tone="danger">{error}</Banner>}
      {note && <Banner tone="success">{note}</Banner>}
      <form onSubmit={saveName} className="flex flex-wrap items-end gap-3">
        <Field label="Your name" className="min-w-48 flex-1">
          {(props) => <Input {...props} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />}
        </Field>
        <Button type="submit" disabled={busy || !name.trim() || name.trim() === me.display_name}>Save name</Button>
      </form>
      <form onSubmit={savePassword} className="space-y-3">
        <p className="text-sm text-muted">
          {needsSetup
            ? "You sign in with the owner passphrase. Add an email and password too, so you can sign in like everyone else and hand the workspace over later."
            : "Change your password. Your other signed-in browsers are signed out."}
        </p>
        {needsSetup ? (
          <>
            <Field label="Email">{(props) => <Input {...props} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />}</Field>
            <Field label="Owner passphrase">{(props) => <Input {...props} type="password" value={passphrase} onChange={(e) => setPassphrase(e.target.value)} autoComplete="current-password" required />}</Field>
          </>
        ) : (
          <Field label="Current password">{(props) => <Input {...props} type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />}</Field>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="New password" hint="At least 10 characters.">{(props) => <Input {...props} type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required minLength={10} />}</Field>
          <Field label="Repeat new password">{(props) => <Input {...props} type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" required />}</Field>
        </div>
        <Button type="submit" disabled={busy}>{needsSetup ? "Set up email sign-in" : "Change password"}</Button>
      </form>
    </div>
  );
}

// The owner hands the workspace to another member. The old owner becomes an admin and the owner
// passphrase is replaced, so this signs the owner out; they sign back in with email and password.
export function HandOver({ roster, me }: { roster: Person[]; me: Person | null }) {
  const [target, setTarget] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!me || me.role !== "owner") return null;
  const others = roster.filter((p) => !p.is_you && p.role !== "owner");
  if (!me.email) {
    return <p className="text-xs text-muted">To hand this workspace to someone else, first add an email and password to your own account above.</p>;
  }
  if (others.length === 0) return null;
  const chosen = others.find((p) => String(p.id) === target);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!chosen) return;
    if (!confirming) { setConfirming(true); return; }
    setBusy(true);
    setError(null);
    try {
      await people.transfer(chosen.id, passphrase);
      window.location.replace(withBase("/login/"));
    } catch (err) {
      setError(failure(err, "Could not hand over the workspace."));
      setConfirming(false);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3 rounded-md border border-line p-4" data-testid="hand-over">
      <h4 className="text-sm font-medium">Hand over this workspace</h4>
      <p className="text-xs text-muted">
        The new owner controls billing and people. You become an admin and are signed out; sign back in with your email and password.
        The owner passphrase stops working: whoever runs the server sets a new one with <span className="font-mono">python -m app.auth.cli set-passphrase</span>.
      </p>
      {error && <Banner tone="danger">{error}</Banner>}
      <Field label="New owner">
        {(props) => (
          <Select {...props} value={target} onChange={(e) => { setTarget(e.target.value); setConfirming(false); }}>
            <option value="">Choose a member…</option>
            {others.map((p) => <option key={p.id} value={p.id}>{p.display_name}{p.email ? ` (${p.email})` : ""}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Owner passphrase">
        {(props) => <Input {...props} type="password" value={passphrase} onChange={(e) => setPassphrase(e.target.value)} autoComplete="current-password" required />}
      </Field>
      <Button type="submit" variant={confirming ? "primary" : undefined} disabled={busy || !chosen || !passphrase}>
        {confirming && chosen ? `Confirm: make ${chosen.display_name} the owner` : "Hand over"}
      </Button>
    </form>
  );
}
