"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type Agent } from "@/lib/api";
import { capabilityIds, spendingCapabilityIds, type IntegrationManifest } from "@/lib/integrations";
import { SettingsCard } from "../SettingsCard";
import { INTEGRATION_APIS } from "./adapters";

type GrantMap = Record<number, string[]>;

const sorted = (values: string[]) => [...values].sort();
const BUTTON = "rounded-md border border-line px-3 py-1.5 text-xs font-medium hover:bg-hover disabled:opacity-50";

function describe(cause: unknown, fallback: string): string {
  return cause instanceof ApiError || cause instanceof Error ? cause.message : fallback;
}

/** One integration: its connection card, then the card that chooses which agents may use it. */
export function IntegrationPanel({
  manifest,
  agents,
  scheduled,
  onScheduledChange,
}: {
  manifest: IntegrationManifest;
  agents: Agent[];
  scheduled: Record<number, boolean>;
  onScheduledChange: (agentId: number, value: boolean) => void;
}) {
  const adapter = INTEGRATION_APIS[manifest.id];
  const tools = capabilityIds(manifest);
  const spendingTools = spendingCapabilityIds(manifest);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [saved, setSaved] = useState<GrantMap>({});
  const [draft, setDraft] = useState<GrantMap>({});
  const [keyDraft, setKeyDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      const status = await adapter.status();
      const grantRows = await Promise.all(agents.map(async (agent) => {
        try { return [agent.id, (await adapter.getGrants(agent.id)).tool_names] as const; }
        catch { return [agent.id, []] as const; }
      }));
      if (!live) return;
      setConfigured(status.configured);
      const next = Object.fromEntries(grantRows) as GrantMap;
      setSaved(next);
      setDraft(next);
    })().catch((cause: unknown) => {
      if (live) setError(cause instanceof Error ? cause.message : "Could not load integrations.");
    });
    return () => { live = false; };
  }, [adapter, agents]);

  const saveKey = async () => {
    if (!keyDraft.trim()) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      await adapter.save(keyDraft.trim());
      setConfigured(true); setKeyDraft(""); setMessage(`${manifest.name} is connected. The API key stays encrypted in the backend.`);
    } catch (cause) {
      setError(describe(cause, `Could not connect ${manifest.name}.`));
    } finally { setBusy(false); }
  };

  const removeKey = async () => {
    setBusy(true); setError(null); setMessage(null);
    try {
      await adapter.remove();
      setConfigured(false);
      const empty = Object.fromEntries(agents.map((agent) => [agent.id, []])) as GrantMap;
      setSaved(empty); setDraft(empty);
      setMessage(`${manifest.name} was disconnected and its worker grants were removed.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Could not disconnect ${manifest.name}.`);
    } finally { setBusy(false); }
  };

  const toggleTool = (agentId: number, tool: string, checked: boolean) => {
    setDraft((current) => {
      const next = new Set(current[agentId] ?? []);
      if (checked) next.add(tool); else next.delete(tool);
      return { ...current, [agentId]: [...next] };
    });
  };

  const setAllTools = (agentIds: number[], checked: boolean) => {
    setDraft((current) => ({ ...current, ...Object.fromEntries(agentIds.map((id) => [id, checked ? [...tools] : []])) }));
  };
  const everyAgentFull = agents.length > 0 && agents.every((agent) => tools.every((tool) => (draft[agent.id] ?? []).includes(tool)));
  const dirtyAgents = agents.filter((agent) => JSON.stringify(sorted(draft[agent.id] ?? [])) !== JSON.stringify(sorted(saved[agent.id] ?? [])));

  const saveAll = async () => {
    setBusy(true); setError(null); setMessage(null);
    try {
      const results = await Promise.all(dirtyAgents.map(async (agent) => [agent.id, (await adapter.setGrants(agent.id, draft[agent.id] ?? [])).tool_names] as const));
      setSaved((current) => ({ ...current, ...Object.fromEntries(results) }));
      setMessage(`${manifest.name} access was saved for ${results.length} ${results.length === 1 ? "agent" : "agents"}.`);
    } catch (cause) {
      setError(describe(cause, `Could not save ${manifest.name} access.`));
    } finally { setBusy(false); }
  };

  const saveAgent = async (agent: Agent) => {
    setBusy(true); setError(null); setMessage(null);
    try {
      const next = (await adapter.setGrants(agent.id, draft[agent.id] ?? [])).tool_names;
      setSaved((current) => ({ ...current, [agent.id]: next }));
      setMessage(`${agent.name}'s ${manifest.name} access was saved.`);
    } catch (cause) {
      setError(describe(cause, `Could not save this agent's ${manifest.name} access.`));
    } finally { setBusy(false); }
  };

  const toggleScheduled = async (agent: Agent, checked: boolean) => {
    setBusy(true); setError(null); setMessage(null);
    try {
      await api.setWorkerPaidUse(agent.id, checked);
      onScheduledChange(agent.id, checked);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not change scheduled paid use.");
    } finally { setBusy(false); }
  };

  return (
    <>
      <div id={manifest.anchorId} data-setting-container>
      <SettingsCard title={manifest.name} description={manifest.description}>
        {configured === null ? <p className="text-sm text-muted">Checking connection…</p> : configured ? (
          <p className="text-sm text-success">Connected</p>
        ) : (
          <p className="text-sm text-muted">Not connected</p>
        )}
        {configured !== null && <div className="space-y-2">
          <label className="block text-sm">{manifest.secret.label}
            <input type="password" autoComplete="off" spellCheck={false} value={keyDraft}
              onChange={(event) => setKeyDraft(event.target.value)} placeholder={configured ? manifest.secret.replacePlaceholder : manifest.secret.placeholder}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2" />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" disabled={busy || !keyDraft.trim()} onClick={() => void saveKey()} className={BUTTON}>{busy ? "Saving…" : configured ? "Replace API key" : `Connect ${manifest.name}`}</button>
            {configured && <button type="button" disabled={busy} onClick={() => void removeKey()} className={BUTTON}>Disconnect {manifest.name}</button>}
            <a className="text-xs text-accent underline" href={manifest.secret.helpUrl} target="_blank" rel="noopener noreferrer">{manifest.secret.helpLabel}</a>
          </div>
        </div>}
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        {message && <p role="status" className="text-xs text-muted">{message}</p>}
      </SettingsCard>
      </div>

      <SettingsCard title={manifest.access.title} description={manifest.access.description}>
        {!agents.length ? <p className="text-sm text-muted">{manifest.access.emptyMessage}</p> : (
          <>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs font-medium">
              <input type="checkbox" disabled={busy || !configured} checked={everyAgentFull}
                onChange={(event) => setAllTools(agents.map((agent) => agent.id), event.target.checked)} />Select all tools for every agent
            </label>
            <button type="button" disabled={busy || !configured || dirtyAgents.length === 0} onClick={() => void saveAll()} className={BUTTON}>Save all changes</button>
          </div>
          <ul className="space-y-3">
            {agents.map((agent) => {
              const selected = draft[agent.id] ?? [];
              const isDirty = JSON.stringify(sorted(selected)) !== JSON.stringify(sorted(saved[agent.id] ?? []));
              const hasPaid = (saved[agent.id] ?? []).some((tool) => spendingTools.includes(tool));
              return <li key={agent.id} className="space-y-2 rounded-md border border-line p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">{agent.name}</h3>
                  <label className="flex items-center gap-2 text-xs text-muted">
                    <input type="checkbox" disabled={busy || !configured} checked={tools.every((tool) => selected.includes(tool))}
                      onChange={(event) => setAllTools([agent.id], event.target.checked)} />Select all
                  </label>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {manifest.capabilities.map((capability) => <label key={capability.id} className="flex items-center gap-2 text-xs">
                    <input type="checkbox" disabled={busy || !configured} checked={selected.includes(capability.id)}
                      onChange={(event) => toggleTool(agent.id, capability.id, event.target.checked)} />{capability.label}
                  </label>)}
                </div>
                {hasPaid && <label className="flex items-start gap-2 border-t border-line pt-2 text-xs text-muted">
                  <input type="checkbox" className="mt-0.5" disabled={busy || !configured} checked={scheduled[agent.id] ?? false}
                    onChange={(event) => void toggleScheduled(agent, event.target.checked)} />
                  <span>{manifest.access.scheduledSpendNote}</span>
                </label>}
                <button type="button" disabled={busy || !configured || !isDirty} onClick={() => void saveAgent(agent)} className={BUTTON}>Save access</button>
              </li>;
            })}
          </ul>
          </>
        )}
      </SettingsCard>
    </>
  );
}
