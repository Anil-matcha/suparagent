"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { continueText, markResumed, readGrant, stepFor, wasResumed } from "@/lib/app-connection";
import {
  POLL_INTERVAL_MS, accountsOf, appLabel, outcomeFor, pollDeadline, reservePage,
  type ComposioAccount, type TabWindow,
} from "@/lib/connected-apps";
import type { TranscriptItem } from "@/lib/transcript";

const BUTTON = "rounded-field border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:opacity-60";
const fail = (err: unknown, fallback: string) => err instanceof ApiError ? err.message : fallback;

// An agent asked for an app it cannot use yet. The person signs in to the app and allows the agent themselves: this
// card never takes a credential and never grants anything without a click.
export function AppConnectionCard({ item, threadId, onContinue, sendDisabled = false }: {
  item: Extract<TranscriptItem, { kind: "app_connection" }>;
  threadId?: number | null;
  onContinue?: (message: string) => Promise<boolean>;
  sendDisabled?: boolean;
}) {
  const label = appLabel(item.app);
  const who = item.agentName || `Agent ${item.agentId}`;
  const [accounts, setAccounts] = useState<ComposioAccount[] | null>(null);
  const [granted, setGranted] = useState(false);
  const [connectedId, setConnectedId] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<{ accountId: string; expiresIn: number } | null>(null);
  const [busy, setBusy] = useState<"connect" | "allow" | "continue" | null>(null);
  const storage = typeof window === "undefined" ? null : window.localStorage;
  const [sent, setSent] = useState(() => wasResumed(item.requestId, storage));
  // Set only by the person's own click on Allow: the last step only they can do. A card that merely loads, such as an old
  // one in the history, never sends anything by itself.
  const [auto, setAuto] = useState(false);
  const sending = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const step = granted ? "continue" : stepFor(item.state, accounts, item.state === "ready" ? { [item.app]: "read" } : null, item.app);

  // After the person starts signing in: watch the account until it is active, the link fails or expires.
  const accountId = waiting?.accountId;
  const expiresIn = waiting?.expiresIn;
  useEffect(() => {
    if (!accountId || expiresIn === undefined) return;
    const deadline = pollDeadline(expiresIn, Date.now());
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const latest = await api.listComposioAccounts();
        if (cancelled) return;
        setAccounts(latest);
        const outcome = outcomeFor(latest, accountId);
        if (outcome === "connected") { setConnectedId(accountId); setWaiting(null); return; }
        if (outcome !== "waiting") { setWaiting(null); setError("The connection did not complete. Try again."); return; }
      } catch (err) { if (!cancelled) { setWaiting(null); setError(fail(err, "Could not check the connection.")); } return; }
      if (Date.now() > deadline) { setWaiting(null); setError("The connect link expired before it was finished. Try again."); return; }
      timer = setTimeout(() => void poll(), POLL_INTERVAL_MS);
    };
    timer = setTimeout(() => void poll(), POLL_INTERVAL_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [accountId, expiresIn]);

  const connect = async () => {
    setBusy("connect"); setError(null);
    // Opened during the click so a browser does not block it.
    const page = reservePage(window.suparagent, (url, target) => window.open(url, target) as TabWindow | null);
    try {
      const link = await api.connectComposioApp(item.app);
      await page.navigate(link.redirect_url);
      if (live.current) setWaiting({ accountId: link.account_id, expiresIn: link.expires_in_seconds });
    } catch (err) { page.cancel(); if (live.current) setError(fail(err, "Could not start the connection.")); }
    finally { if (live.current) setBusy(null); }
  };

  // Read level only, merged into what the agent already has; an explicit click, never automatic.
  const allow = async () => {
    setBusy("allow"); setError(null);
    try {
      const [agents, found] = await Promise.all([api.listAgents(), accounts ?? api.listComposioAccounts()]);
      const agent = agents.find((candidate) => candidate.id === item.agentId);
      if (!agent) throw new Error("That agent is no longer here.");
      if (accountsOf(found, item.app).every((account) => account.status !== "ACTIVE")) throw new Error(`No ${label} account is connected yet.`);
      await api.updateAgent(agent.id, readGrant(agent, item.app, found, connectedId));
      if (live.current) { setAccounts(found); setGranted(true); setAuto(true); }
    } catch (err) { if (live.current) setError(fail(err, err instanceof Error ? err.message : "Could not save the access.")); }
    finally { if (live.current) setBusy(null); }
  };

  const proceed = async () => {
    if (!onContinue || sent || sending.current) return;
    sending.current = true;
    setBusy("continue"); setError(null);
    try {
      if (await onContinue(continueText(item.app))) { markResumed(item.requestId, storage); if (live.current) setSent(true); }
      else if (live.current) setError("Could not send that now. Press Continue to try again.");
    } finally { sending.current = false; if (live.current) { setBusy(null); setAuto(false); } }
  };

  // Resume the agent as soon as the person has allowed it; if another reply is still running, wait for it to finish.
  useEffect(() => {
    if (auto && !sent && !sendDisabled && onContinue && threadId != null) void proceed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, sent, sendDisabled]);

  return (
    <article className="space-y-2 rounded-field border border-line p-3 text-sm" aria-label={`Connect ${label}`}>
      <p><strong>{who} needs {label}</strong></p>
      {item.reason && <p className="text-muted">{item.reason}</p>}
      {step === "key" && <>
        <p>Connected apps are not set up on this computer yet. Add your Composio key on the Apps page, then ask {who} again.</p>
        <Link href="/apps/" className={`${BUTTON} inline-block`}>Open Apps</Link>
      </>}
      {step === "connect" && <>
        <p>{waiting ? `Finish signing in to ${label} in your browser. This card updates when it is done.` : `Sign in to ${label} in your browser. ${who} never sees your password.`}</p>
        <button type="button" className={BUTTON} disabled={busy !== null || waiting !== null} onClick={() => void connect()}>
          {waiting ? "Waiting for sign-in…" : `Connect ${label}`}
        </button>
      </>}
      {step === "allow" && <>
        <p>{label} is connected. Let {who} look at it? It can only read; you can change this in the agent&apos;s settings.</p>
        <button type="button" className={BUTTON} disabled={busy !== null} onClick={() => void allow()}>
          {busy === "allow" ? "Saving…" : `Allow ${who} to read ${label}`}
        </button>
      </>}
      {step === "continue" && <>
        <p>{label} is connected and {who} may read it.{sent ? ` ${who} was asked to continue.` : auto && busy === "continue" ? ` Asking ${who} to continue…` : ""}</p>
        {onContinue && threadId != null && (
          <button type="button" className={BUTTON} disabled={busy !== null || sent || sendDisabled} onClick={() => void proceed()}>
            {sent ? "Continued" : "Continue"}
          </button>
        )}
      </>}
      {error && <p role="alert" className="text-danger">{error}</p>}
    </article>
  );
}
