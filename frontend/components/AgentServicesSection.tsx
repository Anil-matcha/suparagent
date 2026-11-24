"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  agentNames, AUDIT_PAGE, cleanAddress, describeServiceError, hasMoreAudit, kindOf, notProviders, OUTCOME_LABEL, outcomeTone,
  SETUP_TEXT, setupStep, toggleAgent,
  type ServiceAudit, type ServiceCandidate, type ServiceKind, type ServicePreset, type ServiceProvider, type ServiceResource,
} from "@/lib/agent-services";
import { stateLabel, type CustomMcpConnection } from "@/lib/custom-mcp";
import type { MarketplaceAgent } from "@/lib/marketplace";
import { Badge } from "@/components/ui/Badge";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Field, Input, Select } from "@/components/ui/Input";
import { SkeletonList } from "@/components/ui/Skeleton";

type AddForm = { kind: ServiceKind; address: string; label: string; identifier: string };

export function AgentServicesSection({ agents, version = 0, onChanged }: { agents: MarketplaceAgent[]; version?: number; onChanged?: () => void }) {
  const [presets, setPresets] = useState<ServicePreset[]>([]);
  const [providers, setProviders] = useState<ServiceProvider[] | null>(null);
  const [resources, setResources] = useState<ServiceResource[]>([]);
  const [connections, setConnections] = useState<CustomMcpConnection[]>([]);
  const [audit, setAudit] = useState<ServiceAudit[]>([]);
  const [auditAgent, setAuditAgent] = useState("");
  const [auditMore, setAuditMore] = useState(false);
  const [forms, setForms] = useState<Record<string, AddForm>>({});
  const [limits, setLimits] = useState<Record<string, string>>({});
  const [found, setFound] = useState<Record<string, ServiceCandidate[]>>({});
  const [pick, setPick] = useState("");
  const [pickLimit, setPickLimit] = useState("3");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // The connection just added: its card is scrolled into view and outlined so a click is never silent.
  const [fresh, setFresh] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState<string | null>(null);
  const waited = useRef(0);

  const auditPage = useCallback((agent: string, offset: number) =>
    api.listServiceAudit(AUDIT_PAGE, offset, agent === "" ? undefined : Number(agent)), []);
  const fetchAll = useCallback(() => Promise.all([
    api.listServiceProviders(), api.listServiceResources(), api.listCustomMcp(), auditPage(auditAgent, 0),
  ]), [auditPage, auditAgent]);
  const apply = (list: ServiceProvider[], items: ServiceResource[], mcp: CustomMcpConnection[], calls: ServiceAudit[]) => {
    setProviders(list); setResources(items); setConnections(mcp); setAudit(calls); setAuditMore(hasMoreAudit(calls.length));
  };
  const reload = useCallback(async () => {
    const [list, items, mcp, calls] = await fetchAll();
    setProviders(list); setResources(items); setConnections(mcp); setAudit(calls); setAuditMore(hasMoreAudit(calls.length));
  }, [fetchAll]);
  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setError(null); setNotice(null);
    try { await action(); onChanged?.(); } catch (reason) { setError(describeServiceError(reason)); } finally { setBusy(null); }
  };

  useEffect(() => {
    let live = true;
    api.listServicePresets().then((list) => { if (live) setPresets(list); }).catch(() => undefined);
    fetchAll().then(([list, items, mcp, calls]) => { if (live) apply(list, items, mcp, calls); })
      .catch((reason: unknown) => { if (live) { setError(describeServiceError(reason)); setProviders([]); } });
    return () => { live = false; };
    // Loaded once; later changes go through reload().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!fresh || !providers?.some((item) => item.connection_id === fresh)) return;
    document.getElementById(`provider-${fresh}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [fresh, providers]);

  // While the provider's sign-in tab is open, look for the finished sign-in every couple of seconds (up to 10 minutes).
  useEffect(() => {
    if (!signingIn) return;
    waited.current = 0;
    const timer = setInterval(() => {
      waited.current += 2;
      api.listCustomMcp().then((list) => {
        setConnections(list);
        const match = list.find((item) => item.id === signingIn);
        if (!match || match.oauth_signed_in) { setSigningIn(null); if (match) setNotice("Signed in. Now test the connection."); }
        else if (waited.current >= 600) { setSigningIn(null); setNotice("Sign-in timed out. Start again."); }
      }).catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [signingIn]);

  // The Custom MCP section changed a connection: show it as it is now (skipped on the first render).
  const firstVersion = useRef(version);
  useEffect(() => {
    if (version === firstVersion.current) return;
    firstVersion.current = version;
    api.listCustomMcp().then(setConnections).catch(() => undefined);
  }, [version]);

  const off = busy !== null;
  const formFor = (provider: ServiceProvider): AddForm =>
    forms[provider.connection_id] ?? { kind: kindOf(provider.provider) ?? "email", address: "", label: "", identifier: "" };
  const clearForm = (id: string) => setForms((old) => { const copy = { ...old }; delete copy[id]; return copy; });

  const setup = (provider: ServiceProvider, connection: CustomMcpConnection) => {
    const step = setupStep(connection);
    const id = provider.connection_id;
    return <div className="space-y-2">
      <p className="text-sm">{SETUP_TEXT[step]}</p>
      <div className="flex flex-wrap gap-2">
        {step === "sign_in" && <Button variant="primary" disabled={off || signingIn === id} onClick={() => void run(`signin:${id}`, async () => {
          const { authorization_url: url } = await api.startCustomMcpSignIn(id, connection.revision);
          // Only an https address from the sign-in server is ever opened.
          if (!url.startsWith("https://")) throw new Error("The sign-in address was not https.");
          window.open(url, "_blank", "noopener");
          setSigningIn(id); setNotice("Finish signing in in the new tab. This page updates by itself.");
        })}>{signingIn === id ? "Waiting for sign-in…" : busy === `signin:${id}` ? "Opening…" : "Sign in"}</Button>}
        {(step === "test" || step === "ready") && <Button variant={step === "test" ? "primary" : "secondary"} disabled={off} onClick={() => void run(`test:${id}`, async () => {
          try {
            const next = await api.testCustomMcp(id, connection.revision);
            await reload();
            setNotice(`Connected. Found ${next.tool_count ?? 0} tools.${next.enabled ? "" : " Turn it on to continue."}`);
          } catch (reason) { await reload().catch(() => undefined); throw reason; }
        })}>{busy === `test:${id}` ? "Testing…" : step === "test" ? "Test connection" : "Test again"}</Button>}
        {step === "turn_on" && <Button variant="primary" disabled={off} onClick={() => void run(`toggle:${id}`, async () => {
          await api.setCustomMcpEnabled(id, connection.revision, true); await reload();
          setNotice("Turned on. Agents still have no tools: assign an address below, then grant tools under “Custom MCP servers”.");
        })}>Turn on</Button>}
        {step === "ready" && <Button disabled={off} onClick={() => void run(`toggle:${id}`, async () => {
          await api.setCustomMcpEnabled(id, connection.revision, false); await reload(); setNotice("Turned off. No agent can call it now.");
        })}>Turn off</Button>}
      </div>
    </div>;
  };

  const lookup = (provider: ServiceProvider, form: AddForm, full: boolean, have: Set<string>, setForm: (patch: Partial<AddForm>) => void) => {
    const id = provider.connection_id;
    const list = found[id];
    return <div className="space-y-2">
      <Button size="sm" disabled={off} onClick={() => void run(`lookup:${id}`, async () => {
        const result = await api.lookUpServiceProvider(id);
        setFound((old) => ({ ...old, [id]: result }));
      })}>{busy === `lookup:${id}` ? "Looking up…" : "Look up what is on my account"}</Button>
      {list && (list.length === 0
        ? <p className="text-sm text-muted">The service reports nothing on this account yet.</p>
        : <ul aria-label="What the service reports" className="space-y-1">{list.map((item) => <li key={item.provider_resource_id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="min-w-0 break-all">{item.address}{item.label ? <span className="text-muted"> · {item.label}</span> : null}</span>
            <Button size="sm" disabled={off || item.added || have.has(item.address.toLowerCase()) || full} onClick={() => setForm({
              address: item.address, identifier: item.provider_resource_id,
              label: form.label.trim() || item.label || (provider.provider === "agentphone" ? "Phone number" : "Inbox"),
            })}>{item.added || have.has(item.address.toLowerCase()) ? "Added" : "Use"}</Button>
          </li>)}</ul>)}
    </div>;
  };

  const card = (provider: ServiceProvider) => {
    const id = provider.connection_id;
    const connection = connections.find((item) => item.id === id);
    const mine = resources.filter((item) => item.connection_id === id);
    const form = formFor(provider);
    const fixedKind = kindOf(provider.provider);
    const kind = fixedKind ?? form.kind;
    const cleaned = cleanAddress(kind, form.address);
    const full = provider.resource_count >= provider.resource_limit;
    const limitText = limits[id] ?? String(provider.resource_limit);
    const limitValue = Number(limitText);
    const setForm = (patch: Partial<AddForm>) => setForms((old) => ({ ...old, [id]: { ...form, ...patch } }));
    const canLookUp = provider.provider !== "other" && connection !== undefined && setupStep(connection) !== "sign_in" && setupStep(connection) !== "needs_secret";
    return <article key={id} id={`provider-${id}`}
      className={`space-y-4 rounded-lg border p-4 ${fresh === id ? "border-accent ring-2 ring-accent/40" : "border-line"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold">{provider.connection_name}</h3>
          <p className="mt-1 text-xs text-muted">{fixedKind === "email" ? "Email addresses" : fixedKind === "phone" ? "Phone numbers" : "Email or phone"} · {provider.resource_count} of {provider.resource_limit} added</p>
        </div>
        {connection && <Badge tone={connection.enabled && connection.state === "connected" ? "success" : "neutral"}>{stateLabel(connection)}</Badge>}
      </div>
      {connection && setup(provider, connection)}
      {provider.provider === "other" && <Banner tone="info">This service is not AgentMail or AgentPhone, so nothing checks which address a call names. Read each approval card before you allow it.</Banner>}

      <div className="space-y-3">
        <p className="text-sm font-medium">Addresses</p>
        {mine.length === 0 && <p className="text-sm text-muted">None yet. Add one you already created at the service. Agents cannot create them.</p>}
        <ul aria-label="Recorded addresses" className="space-y-3">{mine.map((resource) => <li key={resource.id} className="space-y-2 rounded-md border border-line p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0"><p className="break-all font-medium">{resource.address}</p><p className="text-xs text-muted">{resource.label} · {agentNames(resource.agent_ids, agents)}</p></div>
            {confirm === resource.id
              ? <div className="flex gap-2"><Button size="sm" variant="danger" disabled={off} onClick={() => void run(`rm:${resource.id}`, async () => {
                  await api.deleteServiceResource(resource.id); setConfirm(null); await reload(); setNotice(`${resource.address} removed here. It still exists at the service.`);
                })}>Confirm remove</Button><Button size="sm" disabled={off} onClick={() => setConfirm(null)}>Keep</Button></div>
              : <Button size="sm" disabled={off} onClick={() => setConfirm(resource.id)}>Remove</Button>}
          </div>
          {agents.length === 0 ? <p className="text-xs text-muted">Create an agent to give it this address.</p> : <div className="flex flex-wrap gap-x-4 gap-y-1">
            {agents.map((agent) => <Checkbox key={agent.id} label={agent.name} disabled={off} checked={resource.agent_ids.includes(agent.id)}
              onChange={(event) => void run(`assign:${resource.id}`, async () => {
                const next = await api.assignServiceResource(resource.id, toggleAgent(resource.agent_ids, agent.id, event.target.checked));
                setResources((old) => old.map((item) => item.id === next.id ? next : item));
              })} />)}
          </div>}
        </li>)}</ul>
        {canLookUp && lookup(provider, form, full, new Set(mine.map((item) => item.address.toLowerCase())), setForm)}
        <div className="grid gap-3 sm:grid-cols-2">
          {!fixedKind && <Field label="Kind">{(props) => <Select {...props} value={form.kind} disabled={off} onChange={(event) => setForm({ kind: event.target.value as ServiceKind })}>
            <option value="email">Email address</option><option value="phone">Phone number</option></Select>}</Field>}
          <Field label={kind === "email" ? "Email address" : "Phone number"} hint={kind === "email" ? "For example helper@agentmail.to" : "With country code, such as +14155550100"}>
            {(props) => <Input {...props} value={form.address} maxLength={200} disabled={off || full} onChange={(event) => setForm({ address: event.target.value })} />}
          </Field>
          <Field label="Label" hint="A name for you, such as “Support inbox”.">
            {(props) => <Input {...props} value={form.label} maxLength={100} disabled={off || full} onChange={(event) => setForm({ label: event.target.value })} />}
          </Field>
          {provider.provider === "agentphone" && <Field label="AgentPhone number id" hint="Filled in for you when you pick a number above. Calls and texts name the sending number by this id, so it is required.">
            {(props) => <Input {...props} value={form.identifier} maxLength={200} disabled={off || full} onChange={(event) => setForm({ identifier: event.target.value })} />}
          </Field>}
        </div>
        {full && <p className="text-xs text-muted">This provider is at its limit of {provider.resource_limit}. Raise the limit below to add more.</p>}
        {form.address.trim() !== "" && cleaned === null && <p className="text-xs text-danger">That does not look like a valid {kind === "email" ? "email address" : "phone number"}.</p>}
        <Button variant="primary" disabled={off || full || cleaned === null || !form.label.trim() || (provider.provider === "agentphone" && !form.identifier.trim())} onClick={() => void run(`add:${id}`, async () => {
          await api.addServiceResource({ connection_id: id, kind, address: cleaned ?? "", label: form.label.trim(),
            ...(form.identifier.trim() ? { provider_resource_id: form.identifier.trim() } : {}) });
          clearForm(id); await reload();
          setNotice("Added. No agent has it yet: tick the agents above, then grant them tools under “Custom MCP servers”.");
        })}>{busy === `add:${id}` ? "Adding…" : "Add address"}</Button>
      </div>

      <div className="flex flex-wrap items-end gap-3 border-t border-line pt-4">
        <Field label="Most addresses allowed" hint="A cap you set. Nothing here creates addresses for you.">
          {(props) => <Input {...props} type="number" min={1} max={20} value={limitText} disabled={off} onChange={(event) => setLimits((old) => ({ ...old, [id]: event.target.value }))} />}
        </Field>
        <Button disabled={off || !Number.isInteger(limitValue) || limitValue < 1 || limitValue > 20 || limitValue === provider.resource_limit}
          onClick={() => void run(`limit:${id}`, async () => {
            await api.setServiceProviderLimit(id, provider.provider, limitValue); await reload();
            setLimits((old) => { const copy = { ...old }; delete copy[id]; return copy; }); setNotice("Limit saved.");
          })}>Save limit</Button>
        {confirm === `stop:${id}`
          ? <div className="flex flex-wrap items-center gap-2">
              <span className="basis-full text-xs text-muted">This removes the addresses and which agents have them, and clears every agent&apos;s tool grants for this connection, so none of them keeps using it. The connection and its sign-in stay. The history below is kept.</span>
              <Button variant="danger" disabled={off} onClick={() => void run(`clear:${id}`, async () => {
                await api.clearServiceProvider(id); setConfirm(null); await reload(); setNotice("No longer an email or phone provider. Its tool grants were cleared.");
              })}>Yes, stop</Button><Button disabled={off} onClick={() => setConfirm(null)}>Cancel</Button>
            </div>
          : confirm === `drop:${id}`
            ? <div className="flex flex-wrap items-center gap-2">
                <span className="basis-full text-xs text-muted">This removes the whole connection: its sign-in, its addresses here, and every agent&apos;s tool grants. Your account at the service is not touched. The history below is kept.</span>
                <Button variant="danger" disabled={off} onClick={() => void run(`drop:${id}`, async () => {
                  await api.deleteCustomMcp(id); setConfirm(null); setFound((old) => { const copy = { ...old }; delete copy[id]; return copy; });
                  await reload(); setNotice(`${provider.connection_name} removed.`);
                })}>Yes, remove connection</Button><Button disabled={off} onClick={() => setConfirm(null)}>Cancel</Button>
              </div>
            : <><Button disabled={off} onClick={() => setConfirm(`stop:${id}`)}>Stop using as provider</Button>
                <Button disabled={off} onClick={() => setConfirm(`drop:${id}`)}>Remove connection</Button></>}
      </div>
    </article>;
  };

  const existing = notProviders(connections, providers ?? []);
  return <section className="space-y-3" aria-labelledby="agent-services-heading">
    <div><h2 id="agent-services-heading" className="text-lg font-semibold">Agent email and phone</h2>
      <p className="mt-1 text-sm text-muted">Give an agent its own inbox or phone number on your own account with the service. An agent only gets what you assign, and it can only use the tools you grant. Every send or call asks for your approval and shows the exact address, and a call naming an address you did not assign is refused.</p></div>
    {error && <Banner tone="danger">{error}</Banner>}{notice && <p role="status" className="text-sm text-muted">{notice}</p>}
    <div className="space-y-3 rounded-lg border border-line p-4">
      <p className="text-sm font-medium">Connect a service</p>
      <div className="grid gap-3 sm:grid-cols-2">{presets.map((preset) => {
        const have = (providers ?? []).filter((item) => item.provider === preset.preset).length;
        return <div key={preset.preset} className="space-y-2 rounded-md border border-line p-3">
          <p className="font-medium">{preset.label}</p><p className="text-xs text-muted">{preset.summary}</p>
          {have > 0 && <p className="text-xs text-muted">You already have {have} {preset.label} {have === 1 ? "connection" : "connections"} below. Add another only for a different account.</p>}
          <Button variant="primary" disabled={off} onClick={() => void run(`preset:${preset.preset}`, async () => {
            const added = await api.addServiceProvider(preset.preset, 3); await reload(); setFresh(added.connection_id);
            setNotice(`${preset.label} added below (highlighted), switched off. Sign in, test and turn it on there.`);
          })}>{busy === `preset:${preset.preset}` ? "Adding…" : have > 0 ? `Add another ${preset.label} account` : `Connect ${preset.label}`}</Button>
        </div>;
      })}</div>
      {existing.length > 0 && <div className="space-y-2 rounded-md border border-line p-3">
        <p className="font-medium">Use a connection you already added</p>
        <p className="text-xs text-muted">For another service, such as Telnyx or Vapi: add it under “Custom MCP servers” first, then choose it here. Its tools are hidden from every agent until you assign it an address. Unlike AgentMail and AgentPhone, nothing checks which address a call names, so read each approval card.</p>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Connection">{(props) => <Select {...props} value={pick} disabled={off} onChange={(event) => setPick(event.target.value)}>
            <option value="">Choose a connection</option>{existing.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select>}</Field>
          <Field label="Most addresses allowed">{(props) => <Input {...props} type="number" min={1} max={20} value={pickLimit} disabled={off} onChange={(event) => setPickLimit(event.target.value)} />}</Field>
          <Button disabled={off || pick === "" || !Number.isInteger(Number(pickLimit)) || Number(pickLimit) < 1 || Number(pickLimit) > 20} onClick={() => void run("pick", async () => {
            await api.setServiceProviderLimit(pick, "other", Number(pickLimit)); await reload(); setFresh(pick); setPick("");
            setNotice("Added below (highlighted). Record its addresses and assign agents.");
          })}>Use as provider</Button>
        </div>
      </div>}
    </div>
    {providers === null ? <SkeletonList rows={2} /> : providers.map(card)}
    <div className="space-y-2 rounded-lg border border-line p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><p className="text-sm font-medium">Recent activity</p>
          <p className="text-xs text-muted">Which agent used which address, through which tool. Message text and tool arguments are not kept here.</p></div>
        {agents.length > 0 && <Select aria-label="Show activity for" value={auditAgent} disabled={off} onChange={(event) => {
          const next = event.target.value; setAuditAgent(next);
          void run("audit", async () => { const page = await auditPage(next, 0); setAudit(page); setAuditMore(hasMoreAudit(page.length)); });
        }}><option value="">All agents</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</Select>}
      </div>
      {audit.length === 0 ? <p className="text-sm text-muted">No calls yet.</p> : <ul className="divide-y divide-line">{audit.map((row) => <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
        <span className="min-w-0"><span className="font-medium">{row.agent_name || `Agent ${row.agent_id}`}</span> used <code>{row.tool_name}</code> as <span className="break-all">{row.resources.join(", ")}</span>
          <span className="block text-xs text-muted">{new Date(row.created_at).toLocaleString()}{row.approved_by ? ` · approved by ${row.approved_by}` : ""}</span></span>
        <Badge tone={outcomeTone(row.outcome)}>{OUTCOME_LABEL[row.outcome]}</Badge>
      </li>)}</ul>}
      {auditMore && <Button disabled={off} onClick={() => void run("audit-more", async () => {
        const page = await auditPage(auditAgent, audit.length); setAudit((old) => [...old, ...page]); setAuditMore(hasMoreAudit(page.length));
      })}>{busy === "audit-more" ? "Loading…" : "Show more"}</Button>}
    </div>
  </section>;
}
