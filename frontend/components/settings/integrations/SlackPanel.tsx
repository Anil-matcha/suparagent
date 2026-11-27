"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Agent } from "@/lib/api";
import type { ChannelIntegrationManifest } from "@/lib/integrations";
import {
  checkSlackTokens,
  linkMessage,
  minutesLeft,
  slackManifestJson,
  slackStateText,
  SLACK_APP_NAME,
  type SlackStatus,
  type SlackTone,
} from "@/lib/slack";
import { SettingsCard } from "../SettingsCard";

const BUTTON = "rounded-md border border-line px-3 py-1.5 text-xs font-medium hover:bg-hover disabled:opacity-50";
const FIELD = "mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2";
const TONE: Record<SlackTone, string> = { good: "text-success", warn: "text-warning", bad: "text-danger", muted: "text-muted" };
const POLL_MS = 3000;

type PendingCode = { code: string; expiresAt: number };

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

/**
 * The Slack channel: connect the two tokens, link the owner's Slack account with a one-time code, and
 * choose the agent the direct message talks to. Both tokens are write-only; nothing on this page can
 * read one back.
 */
export function SlackPanel({ manifest, agents }: { manifest: ChannelIntegrationManifest; agents: Agent[] }) {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [botToken, setBotToken] = useState("");
  const [appToken, setAppToken] = useState("");
  const [pending, setPending] = useState<PendingCode | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setStatus(await api.getSlackIntegration());
  }, []);

  useEffect(() => {
    let live = true;
    api.getSlackIntegration().then((found) => {
      if (live) setStatus(found);
    }).catch((cause: unknown) => {
      if (live) setError(messageOf(cause, "Could not load the Slack connection."));
    });
    return () => { live = false; };
  }, []);

  const linked = status?.linked === true;
  // A code is shown only until it is used (the account is linked) or runs out; derived, not stored.
  const activeCode = pending !== null && !linked && now < pending.expiresAt ? pending : null;
  const waitingForLink = activeCode !== null;
  const settling = status?.state === "connecting" || status?.state === "reconnecting";

  // While the connection is coming up, or a link code is waiting to be used in Slack, look again every
  // few seconds so the card follows what Slack is doing.
  useEffect(() => {
    if (!status?.configured || !(settling || waitingForLink)) return undefined;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      void refresh().catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [status?.configured, settling, waitingForLink, refresh]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage(null);
    try { await work(); } catch (cause) { setError(messageOf(cause, "That did not work. Try again.")); } finally { setBusy(false); }
  };

  const connect = () => {
    const problem = checkSlackTokens(botToken, appToken);
    if (problem) { setError(problem); setMessage(null); return; }
    void run(async () => {
      setStatus(await api.saveSlackIntegration(botToken.trim(), appToken.trim()));
      setBotToken(""); setAppToken("");
      setMessage("Slack is connected. The tokens stay encrypted in the backend and are never shown again.");
    });
  };

  const disconnect = () => run(async () => {
    await api.deleteSlackIntegration();
    setPending(null);
    await refresh();
    setMessage("Slack was disconnected and its tokens were removed.");
  });

  const makeCode = () => run(async () => {
    const made = await api.createSlackLinkCode();
    setNow(Date.now());
    setPending({ code: made.code, expiresAt: Date.now() + made.expires_in * 1000 });
    await refresh();
  });

  const chooseAgent = (agentId: number) => run(async () => {
    setStatus(await api.setSlackAgent(agentId));
    setMessage("Saved. The next message in Slack starts a fresh conversation with this agent.");
  });

  const copyManifest = async () => {
    try {
      await navigator.clipboard.writeText(slackManifestJson());
      setMessage("The manifest was copied. Paste it into Slack's From a manifest box."); setError(null);
    } catch {
      setError("Could not copy. Open Show the manifest and copy it by hand.");
    }
  };

  const state = status ? slackStateText(status) : null;
  const configured = status?.configured === true;
  const secondsLeft = activeCode ? Math.max(0, Math.round((activeCode.expiresAt - now) / 1000)) : 0;

  const tokenFields = (
    <div className="space-y-2">
      <label className="block text-sm">Bot token
        <input type="password" autoComplete="off" spellCheck={false} value={botToken} onChange={(event) => setBotToken(event.target.value)}
          placeholder={configured ? "Enter a replacement bot token (xoxb-…)" : "xoxb-…"} className={FIELD} />
      </label>
      <label className="block text-sm">App-level token
        <input type="password" autoComplete="off" spellCheck={false} value={appToken} onChange={(event) => setAppToken(event.target.value)}
          placeholder={configured ? "Enter a replacement app-level token (xapp-…)" : "xapp-…"} className={FIELD} />
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy || !botToken.trim() || !appToken.trim()} onClick={connect} className={BUTTON}>
          {busy ? "Checking with Slack…" : configured ? "Replace tokens" : "Connect Slack"}
        </button>
        {configured && <button type="button" disabled={busy} onClick={() => void disconnect()} className={BUTTON}>Disconnect Slack</button>}
        <a className="text-xs text-accent underline" href={manifest.helpUrl} target="_blank" rel="noopener noreferrer">{manifest.helpLabel}</a>
      </div>
    </div>
  );

  const setupSteps = (
    <ol className="list-decimal space-y-2 pl-5 text-sm">
      <li>
        Create a Slack app <em>from a manifest</em> in your workspace and paste the manifest below.
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void copyManifest()} className={BUTTON}>Copy the manifest</button>
        </div>
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">Show the manifest</summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-line p-3 text-xs">{slackManifestJson()}</pre>
        </details>
      </li>
      <li>Choose <strong>Install to Workspace</strong>. Under <em>OAuth &amp; Permissions</em>, copy the <strong>Bot User OAuth Token</strong> (it starts with <code>xoxb-</code>).</li>
      <li>Under <em>Basic Information → App-Level Tokens</em>, generate a token with the <code>connections:write</code> scope and copy it (it starts with <code>xapp-</code>).</li>
      <li>Paste both tokens here and connect.</li>
    </ol>
  );

  return (
    <>
      <div id={manifest.anchorId} data-setting-container>
        <SettingsCard title={manifest.name} description={manifest.description}>
          {status === null ? <p className="text-sm text-muted">Checking connection…</p> : !status.available ? (
            <p className="text-sm text-muted">{state?.text}</p>
          ) : (
            <>
              <p className={`text-sm ${state ? TONE[state.tone] : ""}`}>{state?.text}</p>
              {configured ? (
                <details>
                  <summary className="cursor-pointer text-xs text-muted">Replace the tokens or see the setup steps</summary>
                  <div className="mt-3 space-y-4">{setupSteps}{tokenFields}</div>
                </details>
              ) : (
                <div className="space-y-4">{setupSteps}{tokenFields}</div>
              )}
            </>
          )}
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          {message && <p role="status" className="text-xs text-muted">{message}</p>}
        </SettingsCard>
      </div>

      {configured && (
        <div id={manifest.chatAnchorId} data-setting-container>
          <SettingsCard
            title="Chat with an agent in Slack"
            description={`Send ${SLACK_APP_NAME} a direct message in Slack and the agent you choose answers there. Only your own Slack account is listened to; anyone else, and anything in a channel, is ignored.`}
          >
            <label className="block text-sm">Agent
              {agents.length === 0 ? (
                <span className="mt-1 block text-xs text-muted">Create an agent first.</span>
              ) : (
                <select value={status?.agent_id ?? ""} disabled={busy} onChange={(event) => void chooseAgent(Number(event.target.value))} className={FIELD}>
                  {status?.agent_id == null && <option value="" disabled>Choose an agent</option>}
                  {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
                </select>
              )}
            </label>

            <div className="space-y-2 border-t border-line pt-3">
              <p className="text-sm">{linked ? "Your Slack account is linked." : "Your Slack account is not linked yet."}</p>
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" disabled={busy || status?.state !== "connected"} onClick={() => void makeCode()} className={BUTTON}>
                  {linked ? "Link a different Slack account" : "Link my Slack account"}
                </button>
                {status?.state !== "connected" && <span className="text-xs text-muted">Available once Slack is connected.</span>}
              </div>
              {activeCode && (
                <div role="status" className="space-y-1 rounded-md border border-line p-3 text-sm">
                  <p>In Slack, open a direct message with <strong>{SLACK_APP_NAME}</strong> and send:</p>
                  <p><code className="rounded bg-hover px-2 py-1 text-base">{linkMessage(activeCode.code)}</code></p>
                  <p className="text-xs text-muted">This code works once and expires in {minutesLeft(secondsLeft)} {minutesLeft(secondsLeft) === 1 ? "minute" : "minutes"}.</p>
                </div>
              )}
            </div>

            <ul className="list-disc space-y-1 border-t border-line pl-5 pt-3 text-xs text-muted">
              <li>Send <code>new</code> in the direct message to start a fresh conversation.</li>
              <li>When the agent needs approval, Slack shows <strong>Allow</strong> and <strong>Deny</strong> buttons. A request too big for one message says to open the app. There is no &ldquo;always allow&rdquo; in Slack.</li>
              <li>The agent keeps its own approval mode, spending limits and tools. Slack adds no permissions.</li>
              <li>Replies and approvals reach Slack only while this app is running.</li>
            </ul>
          </SettingsCard>
        </div>
      )}
    </>
  );
}
