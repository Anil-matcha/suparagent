"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { auth } from "@/lib/auth";
import { withBase } from "@/lib/paths";
import { inviteFromHash } from "@/lib/session";
import { Banner } from "./ui/Banner";
import { Button } from "./ui/Button";
import { Field, Input } from "./ui/Input";

// Joins a shared workspace from an invitation link. The secret arrives in the URL fragment
// (#token=…&email=…), which never reaches a server log, and is removed from the address bar once read.
export function JoinScreen() {
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const invite = inviteFromHash(window.location.hash);
    if (invite) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setToken(invite.token);
      setEmail(invite.email);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (password !== repeat) {
      setError("The two passwords are different.");
      return;
    }
    setBusy(true);
    try {
      await auth.acceptInvite({ token, email, display_name: name, password });
      setDone(true);
      window.location.replace(withBase("/chat/"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not join. Check the link and try again.");
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-inset px-4">
      <main className="w-full max-w-sm space-y-5 rounded-card border border-line bg-raised p-6 shadow-(--shadow-card)">
        <h1 className="text-title font-semibold">Join this workspace</h1>
        {!token && !done ? (
          <Banner tone="danger" title="No invitation found">Open the full link you were sent. It looks like …/join/#token=…</Banner>
        ) : done ? (
          <Banner tone="success" title="Welcome">Opening your chats…</Banner>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <p className="text-sm text-muted">Use the email address the invitation was sent to. Choose a password for signing in next time.</p>
            <Field label="Email">
              {(props) => <Input {...props} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />}
            </Field>
            <Field label="Your name">
              {(props) => <Input {...props} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required maxLength={80} autoFocus />}
            </Field>
            <Field label="Password" hint="At least 10 characters.">
              {(props) => <Input {...props} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required minLength={10} />}
            </Field>
            <Field label="Repeat password">
              {(props) => <Input {...props} type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" required />}
            </Field>
            {error && <Banner tone="danger">{error}</Banner>}
            <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!email.trim() || !name.trim() || !password}>
              {busy ? "Joining…" : "Join"}
            </Button>
          </form>
        )}
        <p className="text-xs text-muted">Already a member? <Link href="/login/" className="underline">Sign in</Link>.</p>
      </main>
    </div>
  );
}
