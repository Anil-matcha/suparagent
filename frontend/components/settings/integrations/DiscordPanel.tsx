"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Agent } from "@/lib/api";
import { checkDiscordToken, discordStateText, type DiscordStatus } from "@/lib/discord";
import type { ChannelIntegrationManifest } from "@/lib/integrations";
import { SettingsCard } from "../SettingsCard";

const BUTTON = "rounded-md border border-line px-3 py-1.5 text-xs font-medium hover:bg-hover disabled:opacity-50";
const FIELD = "mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2";
const POLL_MS = 3000;
type PendingCode = { code: string; expiresAt: number };

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

export function DiscordPanel({ manifest, agents }: { manifest: ChannelIntegrationManifest; agents: Agent[] }) {
  const [status, setStatus] = useState<DiscordStatus | null>(null);
  const [token, setToken] = useState("");
  const [pending, setPending] = useState<PendingCode | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => setStatus(await api.getDiscordIntegration()), []);
  useEffect(() => {
    let live = true;
    api.getDiscordIntegration().then((found) => {
      if (live) setStatus(found);
    }).catch((cause: unknown) => {
      if (live) setError(messageOf(cause, "Could not load the Discord connection."));
    });
    return () => { live = false; };
  }, []);

  const linked = status?.linked === true;
  const activeCode = pending !== null && !linked && now < pending.expiresAt ? pending : null;
  const settling = status?.state === "connecting" || status?.state === "reconnecting";
  useEffect(() => {
    if (!status?.configured || !(settling || activeCode)) return undefined;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      void refresh().catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [status?.configured, settling, activeCode, refresh]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage(null);
    try { await work(); } catch (cause) { setError(messageOf(cause, "That did not work. Try again.")); } finally { setBusy(false); }
  };
  const connect = () => {
    const problem = checkDiscordToken(token);
    if (problem) { setError(problem); return; }
    void run(async () => {
      setStatus(await api.saveDiscordIntegration(token.trim()));
      setToken("");
      setMessage("Discord is connected. The bot token stays encrypted and is never shown again.");
    });
  };
  const disconnect = () => run(async () => {
    await api.deleteDiscordIntegration();
    setPending(null);
    await refresh();
    setMessage("Discord was disconnected and its bot token was removed.");
  });
  const makeCode = () => run(async () => {
    const made = await api.createDiscordLinkCode();
    setNow(Date.now());
    setPending({ code: made.code, expiresAt: Date.now() + made.expires_in * 1000 });
    await refresh();
  });
  const chooseAgent = (agentId: number) => run(async () => {
    setStatus(await api.setDiscordAgent(agentId));
    setMessage("Saved. The next Discord message starts a fresh conversation with this agent.");
  });
  const configured = status?.configured === true;
  const minutes = activeCode ? Math.ceil((activeCode.expiresAt - now) / 60_000) : 0;
  const setup = (
    <div className="space-y-4">
      <ol className="list-decimal space-y-2 pl-5 text-sm">
        <li>Create an app in the Discord Developer Portal. On its <strong>Bot</strong> page, create or reset the bot token.</li>
        <li>On <strong>Installation</strong>, check <strong>Guild Install</strong>. In <strong>Default Install Settings → Guild Install</strong>, add the <code>bot</code> scope. Use the Discord Provided Link to add the bot to a server you belong to, then open a direct message with it. No privileged Message Content intent is needed for DMs.</li>
        <li>Paste the bot token here. This app connects to Discord over an outbound Gateway connection; do not set an Interactions Endpoint URL.</li>
      </ol>
      <label className="block text-sm">Bot token
        <input type="password" autoComplete="off" spellCheck={false} value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder={configured ? "Enter a replacement bot token" : "Paste the bot token"} className={FIELD} />
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy || !token.trim()} onClick={connect} className={BUTTON}>
          {busy ? "Checking with Discord…" : configured ? "Replace token" : "Connect Discord"}
        </button>
        {configured && <button type="button" disabled={busy} onClick={() => void disconnect()} className={BUTTON}>Disconnect Discord</button>}
        <a className="text-xs text-accent underline" href={manifest.helpUrl} target="_blank" rel="noopener noreferrer">{manifest.helpLabel}</a>
      </div>
    </div>
  );

  return (
    <>
      <div id={manifest.anchorId} data-setting-container>
        <SettingsCard title={manifest.name} description={manifest.description}>
          {status === null ? <p className="text-sm text-muted">Checking connection…</p> : !status.available ? (
            <p className="text-sm text-muted">{discordStateText(status)}</p>
          ) : (
            <>
              <p className={`text-sm ${status.state === "connected" ? "text-success" : status.state === "failed" ? "text-danger" : "text-muted"}`}>
                {discordStateText(status)}
              </p>
              {configured ? <details><summary className="cursor-pointer text-xs text-muted">Replace the token or see setup steps</summary>
                <div className="mt-3">{setup}</div></details> : setup}
            </>
          )}
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          {message && <p role="status" className="text-xs text-muted">{message}</p>}
        </SettingsCard>
      </div>
      {configured && (
        <div id={manifest.chatAnchorId} data-setting-container>
          <SettingsCard title="Chat with an agent in Discord" description="Only the Discord user you link, in their direct message with this bot, can start agent turns or answer approvals.">
            <label className="block text-sm">Agent
              {agents.length === 0 ? <span className="mt-1 block text-xs text-muted">Create an agent first.</span> : (
                <select value={status?.agent_id ?? ""} disabled={busy} onChange={(event) => void chooseAgent(Number(event.target.value))} className={FIELD}>
                  {status?.agent_id == null && <option value="" disabled>Choose an agent</option>}
                  {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
                </select>
              )}
            </label>
            <div className="space-y-2 border-t border-line pt-3">
              <p className="text-sm">{linked ? "Your Discord account is linked." : "Your Discord account is not linked yet."}</p>
              <button type="button" disabled={busy || status?.state !== "connected"} onClick={() => void makeCode()} className={BUTTON}>
                {linked ? "Link a different Discord account" : "Link my Discord account"}
              </button>
              {activeCode && <div role="status" className="space-y-1 rounded-md border border-line p-3 text-sm">
                <p>Send this in a direct message to <strong>{status?.bot ?? "your bot"}</strong>:</p>
                <p><code className="rounded bg-hover px-2 py-1 text-base">link {activeCode.code}</code></p>
                <p className="text-xs text-muted">The code works once and expires in {minutes} {minutes === 1 ? "minute" : "minutes"}.</p>
              </div>}
            </div>
            <ul className="list-disc space-y-1 border-t border-line pl-5 pt-3 text-xs text-muted">
              <li>Send <code>new</code> to start a fresh conversation.</li>
              <li>Approvals show Allow and Deny buttons when the full request fits in one message. There is no always-allow button.</li>
              <li>The agent keeps its own approval mode, spending limits and tools.</li>
              <li>Replies and approvals reach Discord only while this app is running.</li>
            </ul>
          </SettingsCard>
        </div>
      )}
    </>
  );
}
