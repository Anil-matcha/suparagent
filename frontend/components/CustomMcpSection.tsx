"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  buildUpdate, describeError, DIRECT_TOOL_LIMIT, filterTools, sharingLabel, stateLabel,
  type CustomMcpAuthMode, type CustomMcpConnection, type CustomMcpTool,
} from "@/lib/custom-mcp";
import type { MarketplaceAgent } from "@/lib/marketplace";
import { Badge } from "@/components/ui/Badge";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Field, Input, Select } from "@/components/ui/Input";
import { SkeletonList } from "@/components/ui/Skeleton";

type Form = { name: string; endpoint: string; auth_mode: CustomMcpAuthMode; auth_header_name: string; credential: string; oauth_client_id: string; oauth_scopes: string };
const EMPTY: Form = { name: "", endpoint: "", auth_mode: "none", auth_header_name: "", credential: "", oauth_client_id: "", oauth_scopes: "" };
const AUTH_LABEL: Record<CustomMcpAuthMode, string> = { none: "No authentication", bearer: "Bearer token", header: "Custom header", oauth: "Browser sign-in (OAuth)" };

function tone(connection: CustomMcpConnection) {
  if (connection.enabled && connection.state === "connected") return "success" as const;
  if (connection.state === "unreachable" || connection.state === "needs_sign_in") return "danger" as const;
  return "neutral" as const;
}

function ConnectionForm({ form, setForm, hasSecret, disabled }: {
  form: Form; setForm: (next: Form) => void; hasSecret?: boolean; disabled: boolean;
}) {
  const set = (patch: Partial<Form>) => setForm({ ...form, ...patch });
  return <div className="grid gap-3 sm:grid-cols-2">
    <Field label="Name">{(props) => <Input {...props} value={form.name} maxLength={100} disabled={disabled} onChange={(event) => set({ name: event.target.value })} />}</Field>
    <Field label="MCP address" hint="A public https:// address. No port, query string, or login in the URL.">
      {(props) => <Input {...props} type="url" inputMode="url" placeholder="https://example.com/mcp" value={form.endpoint} maxLength={300} disabled={disabled} onChange={(event) => set({ endpoint: event.target.value })} />}
    </Field>
    <Field label="Authentication">
      {(props) => <Select {...props} value={form.auth_mode} disabled={disabled} onChange={(event) => set({ auth_mode: event.target.value as CustomMcpAuthMode })}>
        <option value="none">None</option><option value="bearer">Bearer token</option><option value="header">Custom header</option><option value="oauth">Browser sign-in (OAuth)</option>
      </Select>}
    </Field>
    {form.auth_mode === "header" && <Field label="Header name" hint="For example X-Api-Key.">
      {(props) => <Input {...props} value={form.auth_header_name} maxLength={64} disabled={disabled} onChange={(event) => set({ auth_header_name: event.target.value })} />}
    </Field>}
    {form.auth_mode === "oauth" && <>
      <Field label="Client ID (optional)" hint="Leave blank to let the server register Suparagent automatically. In the desktop app, a client you register yourself must allow http://127.0.0.1 redirects on any port.">
        {(props) => <Input {...props} value={form.oauth_client_id} maxLength={200} disabled={disabled} onChange={(event) => set({ oauth_client_id: event.target.value })} />}
      </Field>
      <Field label="Scopes (optional)" hint="Separated by spaces. Leave blank for the default.">
        {(props) => <Input {...props} value={form.oauth_scopes} maxLength={500} disabled={disabled} onChange={(event) => set({ oauth_scopes: event.target.value })} />}
      </Field>
    </>}
    {form.auth_mode !== "none" && <Field label={form.auth_mode === "oauth" ? (hasSecret ? "Replace client secret (optional)" : "Client secret (optional)") : hasSecret ? "Replace secret" : "Secret"}
      hint={hasSecret ? "Leave blank to keep the saved secret. It is never shown again." : "Sent only by this app's backend to the address above. It is never shown again and never given to the agent."}>
      {(props) => <Input {...props} type="password" autoComplete="new-password" value={form.credential} disabled={disabled} onChange={(event) => set({ credential: event.target.value })} />}
    </Field>}
  </div>;
}

export function CustomMcpSection({ agents }: { agents: MarketplaceAgent[] }) {
  const [connections, setConnections] = useState<CustomMcpConnection[] | null>(null);
  const [tools, setTools] = useState<Record<string, CustomMcpTool[]>>({});
  const [draft, setDraft] = useState<Form>(EMPTY);
  const [editing, setEditing] = useState<{ id: string; form: Form } | null>(null);
  const [agentFor, setAgentFor] = useState<Record<string, number | "">>({});
  const [granted, setGranted] = useState<Record<string, string[]>>({});
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [toolFilter, setToolFilter] = useState<Record<string, string>>({});
  const [confirmShare, setConfirmShare] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const apply = (next: CustomMcpConnection) =>
    setConnections((old) => (old ?? []).map((item) => item.id === next.id ? next : item));
  const forget = (id: string) => {
    setTools((old) => { const copy = { ...old }; delete copy[id]; return copy; });
    setGranted((old) => Object.fromEntries(Object.entries(old).filter(([key]) => !key.startsWith(`${id}:`))));
    setAgentFor((old) => { const copy = { ...old }; delete copy[id]; return copy; });
  };
  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setError(null); setNotice(null);
    try { await action(); } catch (reason) { setError(describeError(reason)); } finally { setBusy(null); }
  };

  const reload = useCallback(async () => setConnections(await api.listCustomMcp()), []);
  const [signingIn, setSigningIn] = useState<string | null>(null);
  const waited = useRef(0);
  // While a provider tab is open, look for the finished sign-in every couple of seconds (up to 10 minutes).
  useEffect(() => {
    if (!signingIn) return;
    waited.current = 0;
    const timer = setInterval(() => {
      waited.current += 2;
      api.listCustomMcp().then((list) => {
        setConnections(list);
        const found = list.find((item) => item.id === signingIn);
        if (!found || found.oauth_signed_in) { setSigningIn(null); if (found) setNotice("Signed in. Now test the connection."); }
        else if (waited.current >= 600) { setSigningIn(null); setNotice("Sign-in timed out. Start again."); }
      }).catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [signingIn]);
  useEffect(() => {
    let live = true;
    api.listCustomMcp().then((list) => { if (live) setConnections(list); })
      .catch((reason: unknown) => { if (live) { setError(describeError(reason)); setConnections([]); } });
    return () => { live = false; };
  }, []);

  const loadTools = async (connection: CustomMcpConnection) => {
    const found = await api.getCustomMcpTools(connection.id);
    setTools((old) => ({ ...old, [connection.id]: found }));
  };

  const create = () => run("create", async () => {
    const body = {
      name: draft.name.trim(), endpoint: draft.endpoint.trim(), auth_mode: draft.auth_mode,
      ...(draft.auth_mode === "header" ? { auth_header_name: draft.auth_header_name.trim() } : {}),
      ...(draft.auth_mode !== "none" && draft.credential ? { credential: draft.credential } : {}),
      ...(draft.auth_mode === "oauth" ? { oauth_client_id: draft.oauth_client_id.trim(), oauth_scopes: draft.oauth_scopes.trim() } : {}),
    };
    await api.createCustomMcp(body);
    setDraft(EMPTY); await reload();
    setNotice("Saved as a draft. It is off and no agent can use it. Test it, then turn it on and choose tools for each agent.");
  });

  const test = (connection: CustomMcpConnection) => run(`test:${connection.id}`, async () => {
    forget(connection.id);
    try {
      const next = await api.testCustomMcp(connection.id, connection.revision);
      apply(next); await loadTools(next);
      setNotice(`Connected. Found ${next.tool_count ?? 0} tools. Turn it on to choose which tools each agent gets.`);
    } catch (reason) { await reload().catch(() => undefined); throw reason; }
  });

  const signIn = (connection: CustomMcpConnection) => run(`signin:${connection.id}`, async () => {
    const { authorization_url: url } = await api.startCustomMcpSignIn(connection.id, connection.revision);
    // Only an https address from the sign-in server is ever opened.
    if (!url.startsWith("https://")) throw new Error("The sign-in address was not https.");
    window.open(url, "_blank", "noopener");
    setSigningIn(connection.id);
    setNotice("Finish signing in in the new tab. This page updates by itself.");
  });

  const toggle = (connection: CustomMcpConnection, enabled: boolean) => run(`toggle:${connection.id}`, async () => {
    const next = await api.setCustomMcpEnabled(connection.id, connection.revision, enabled);
    apply(next); if (enabled) await loadTools(next);
    setNotice(enabled ? "Turned on. Agents still have no tools until you grant them below." : "Turned off. No agent can call it now.");
  });

  const save = (connection: CustomMcpConnection, form: Form) => run(`save:${connection.id}`, async () => {
    const body = buildUpdate(connection, form);
    if (body === null) { setEditing(null); return; }
    const moved = "endpoint" in body || "auth_mode" in body || "credential" in body || "auth_header_name" in body;
    const next = await api.updateCustomMcp(connection.id, body);
    apply(next); setEditing(null);
    if (moved) { forget(connection.id); setNotice("Saved. The server is off and its tool grants were cleared. Test it again to continue."); }
    else setNotice("Saved.");
  });

  const loadGrants = async (connection: CustomMcpConnection, agentId: number) => {
    setAgentFor((old) => ({ ...old, [connection.id]: agentId }));
    try {
      const result = await api.getCustomMcpGrants(connection.id, agentId);
      setGranted((old) => ({ ...old, [`${connection.id}:${agentId}`]: result.tool_names }));
    } catch (reason) { setError(describeError(reason)); }
  };

  const card = (connection: CustomMcpConnection) => {
    const isEditing = editing?.id === connection.id;
    const list = tools[connection.id];
    const agentId = agentFor[connection.id] ?? "";
    const key = agentId === "" ? "" : `${connection.id}:${agentId}`;
    const checked = key ? granted[key] ?? [] : [];
    const off = busy !== null;
    if (!connection.can_manage) {
      // Someone else's private connection, shown to the workspace owner so it can be switched off or removed.
      return <article key={connection.id} className="space-y-3 rounded-lg border border-line p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0"><h3 className="font-semibold">{connection.name}</h3><p className="mt-1 break-all text-xs"><code>{connection.endpoint}</code></p></div>
          <Badge>{sharingLabel(connection)}</Badge>
        </div>
        <p className="text-xs text-muted">Added by {connection.added_by ?? "someone else"} and private to them. As the workspace owner you can turn it off or remove it, but not use it, test it or sign in to it.</p>
        <div className="flex flex-wrap gap-2">
          {connection.enabled && <Button disabled={off} onClick={() => void run(`toggle:${connection.id}`, async () => {
            apply(await api.setCustomMcpEnabled(connection.id, connection.revision, false)); setNotice("Turned off.");
          })}>Turn off</Button>}
          {confirmRemove === connection.id
            ? <><Button variant="danger" disabled={off} onClick={() => void run(`delete:${connection.id}`, async () => {
                await api.deleteCustomMcp(connection.id); setConfirmRemove(null); await reload(); setNotice(`${connection.name} removed with its secret and grants.`);
              })}>Confirm remove</Button><Button disabled={off} onClick={() => setConfirmRemove(null)}>Keep</Button></>
            : <Button disabled={off} onClick={() => setConfirmRemove(connection.id)}>Remove</Button>}
        </div>
      </article>;
    }
    return <article key={connection.id} className="space-y-4 rounded-lg border border-line p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="font-semibold">{connection.name}</h3><p className="mt-1 break-all text-xs"><code>{connection.endpoint}</code></p></div>
        <div className="flex flex-wrap items-center gap-2"><Badge>{sharingLabel(connection)}</Badge><Badge tone={tone(connection)}>{stateLabel(connection)}</Badge></div>
      </div>
      <p className="text-xs text-muted">
        {AUTH_LABEL[connection.auth_mode]}{connection.auth_mode === "header" && connection.auth_header_name ? ` (${connection.auth_header_name})` : ""}
        {connection.auth_mode === "oauth"
          ? ` · ${connection.oauth_signed_in ? "Signed in" : "Not signed in"}${connection.credential_configured ? " · Client secret saved" : ""}`
          : connection.auth_mode !== "none" && ` · ${connection.credential_configured ? "Secret saved" : "No secret saved"}`}
        {connection.tool_count !== null && ` · ${connection.tool_count} tools found`}
      </p>
      {connection.server_name && <p className="text-xs text-muted">The server says it is “{connection.server_name}”{connection.server_version ? ` ${connection.server_version}` : ""}. This is its own claim; check the address above.</p>}
      {connection.state === "needs_sign_in" && <Banner tone="danger">{connection.auth_mode === "oauth" ? "Sign in to this server to continue." : "The server rejected the saved secret. Replace it, then test again."}</Banner>}
      {connection.state === "unreachable" && <Banner tone="danger">The server could not be reached or answered unexpectedly. Test again when it is back.</Banner>}
      {isEditing ? <div className="space-y-3">
        <ConnectionForm form={editing.form} setForm={(form) => setEditing({ id: connection.id, form })} hasSecret={connection.credential_configured} disabled={off} />
        <p className="text-xs text-muted">Changing the address, authentication or secret turns the server off, clears its tool grants, and needs a new test.</p>
        <div className="flex gap-2"><Button variant="primary" disabled={off} onClick={() => void save(connection, editing.form)}>Save changes</Button><Button disabled={off} onClick={() => setEditing(null)}>Cancel</Button></div>
      </div> : <div className="flex flex-wrap gap-2">
        {connection.auth_mode === "oauth" && (connection.oauth_signed_in
          ? <Button disabled={off} onClick={() => void run(`signout:${connection.id}`, async () => {
              apply(await api.signOutCustomMcp(connection.id, connection.revision)); forget(connection.id);
              setNotice("Signed out here. The server is off and its tool grants were cleared. To remove access completely, also remove Suparagent in that service's connected-apps settings.");
            })}>Sign out</Button>
          : <Button variant="primary" disabled={off || signingIn === connection.id} onClick={() => void signIn(connection)}>{signingIn === connection.id ? "Waiting for sign-in…" : busy === `signin:${connection.id}` ? "Opening…" : "Sign in"}</Button>)}
        <Button disabled={off || (connection.auth_mode === "oauth" ? !connection.oauth_signed_in : connection.auth_mode !== "none" && !connection.credential_configured)} onClick={() => void test(connection)}>{busy === `test:${connection.id}` ? "Testing…" : connection.state === "not_tested" ? "Test connection" : "Test again"}</Button>
        {connection.enabled
          ? <Button disabled={off} onClick={() => void toggle(connection, false)}>Turn off</Button>
          : connection.state !== "not_tested" && connection.state !== "unreachable" && connection.state !== "needs_sign_in" && <Button variant="primary" disabled={off} onClick={() => void toggle(connection, true)}>Turn on</Button>}
        <Button disabled={off} onClick={() => setEditing({ id: connection.id, form: {
          name: connection.name, endpoint: connection.endpoint, auth_mode: connection.auth_mode,
          auth_header_name: connection.auth_header_name ?? "", credential: "",
          oauth_client_id: connection.oauth_client_id ?? "", oauth_scopes: connection.oauth_scopes ?? "" } })}>Edit</Button>
        {connection.mine && !connection.shared && (confirmShare === connection.id
          ? <>
              <span className="basis-full text-xs text-muted">Everyone in this workspace will be able to use this server through agents that are granted its tools, signed in as you where it uses your account. Each call still asks for approval.</span>
              <Button variant="primary" disabled={off} onClick={() => void run(`share:${connection.id}`, async () => {
                apply(await api.setCustomMcpSharing(connection.id, connection.revision, true)); setConfirmShare(null); setNotice("Shared with the workspace.");
              })}>Yes, share</Button><Button disabled={off} onClick={() => setConfirmShare(null)}>Cancel</Button>
            </>
          : <Button disabled={off} onClick={() => setConfirmShare(connection.id)}>Share with workspace</Button>)}
        {connection.mine && connection.shared && <Button disabled={off} onClick={() => void run(`unshare:${connection.id}`, async () => {
          apply(await api.setCustomMcpSharing(connection.id, connection.revision, false)); setNotice("No longer shared. Only you can use it.");
        })}>Stop sharing</Button>}
        {connection.credential_configured && connection.auth_mode !== "oauth" && <Button disabled={off} onClick={() => void run(`secret:${connection.id}`, async () => {
          apply(await api.removeCustomMcpCredential(connection.id, connection.revision)); forget(connection.id);
          setNotice("Secret removed. The server is off and its tool grants were cleared.");
        })}>Remove secret</Button>}
        {confirmRemove === connection.id
          ? <><Button variant="danger" disabled={off} onClick={() => void run(`delete:${connection.id}`, async () => {
              await api.deleteCustomMcp(connection.id); forget(connection.id); setConfirmRemove(null); await reload(); setNotice(`${connection.name} removed with its secret and grants.`);
            })}>Confirm remove</Button><Button disabled={off} onClick={() => setConfirmRemove(null)}>Keep</Button></>
          : <Button disabled={off} onClick={() => setConfirmRemove(connection.id)}>Remove</Button>}
      </div>}
      {connection.enabled && list && <div className="space-y-3 border-t border-line pt-4">
        <p className="text-sm font-medium">Tools for each agent</p>
        <p className="text-xs text-muted">Only checked tools are available to the chosen agent, in person chats with Claude or Codex. Other engines, routines, rooms and computer sessions do not receive these tools. Every call asks for your approval and shows the exact arguments. Tool descriptions come from the server and are untrusted.</p>
        {agents.length === 0 ? <p className="text-sm text-muted">Create an agent before granting tools.</p> : <>
          <Select value={agentId} onChange={(event) => { if (event.target.value) void loadGrants(connection, Number(event.target.value)); }}>
            <option value="">Choose an agent</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
          </Select>
          {agentId !== "" && <>
            {list.length > 12 && <div className="flex flex-wrap items-center gap-2">
              <Input aria-label="Filter tools" placeholder="Filter tools by name or description" value={toolFilter[connection.id] ?? ""} onChange={(event) => setToolFilter((old) => ({ ...old, [connection.id]: event.target.value }))} />
              <Button size="sm" disabled={off} onClick={() => setGranted((old) => ({ ...old, [key]: Array.from(new Set([...checked, ...filterTools(list, toolFilter[connection.id] ?? "").map((tool) => tool.name)])) }))}>Check shown</Button>
              <Button size="sm" disabled={off} onClick={() => setGranted((old) => ({ ...old, [key]: [] }))}>Clear all</Button>
              <span className="text-xs text-muted">{checked.length} of {list.length} checked</span>
            </div>}
            {checked.length > DIRECT_TOOL_LIMIT && <p className="text-xs text-muted">With more than {DIRECT_TOOL_LIMIT} tools checked, the agent is not given them one by one. It searches the tools you granted and calls one by reference; each call still asks for your approval with the server, tool and arguments.</p>}
            <ul className="space-y-2">{filterTools(list, toolFilter[connection.id] ?? "").map((tool) => <li key={tool.name}>
              <Checkbox checked={checked.includes(tool.name)} onChange={(event) => setGranted((old) => ({ ...old, [key]: event.target.checked ? [...checked, tool.name] : checked.filter((name) => name !== tool.name) }))}
                label={<span><span className="font-medium">{tool.name}</span><span className="line-clamp-3 block text-xs text-muted" title={tool.description}>{tool.description}</span></span>} />
            </li>)}</ul>
            <Button variant="primary" disabled={off} onClick={() => void run(`grants:${key}`, async () => {
              const exact = list.filter((tool) => checked.includes(tool.name)).map(({ name, schema_digest }) => ({ name, schema_digest }));
              await api.saveCustomMcpGrants(connection.id, Number(agentId), connection.revision, exact);
              await reload(); setNotice("Tool grants saved.");
            })}>{busy === `grants:${key}` ? "Saving…" : "Save grants"}</Button>
          </>}
        </>}
      </div>}
    </article>;
  };

  return <section className="space-y-3" aria-labelledby="custom-mcp-heading">
    <div><h2 id="custom-mcp-heading" className="text-lg font-semibold">Custom MCP servers</h2>
      <p className="mt-1 text-sm text-muted">Add your own remote MCP server. A new server starts off, and no agent can use it until you test it, turn it on and grant specific tools. Only public https:// servers are supported; local commands are not.</p></div>
    {error && <Banner tone="danger">{error}</Banner>}{notice && <p role="status" className="text-sm text-muted">{notice}</p>}
    <div className="space-y-3 rounded-lg border border-line p-4">
      <p className="text-sm font-medium">Add a server</p>
      <ConnectionForm form={draft} setForm={setDraft} disabled={busy !== null} />
      <Button variant="primary" disabled={busy !== null || !draft.name.trim() || !draft.endpoint.trim()} onClick={() => void create()}>{busy === "create" ? "Saving…" : "Save as draft"}</Button>
    </div>
    {connections === null ? <SkeletonList rows={2} /> : connections.map(card)}
    {connections?.length === 0 && <p className="text-sm text-muted">No custom servers yet.</p>}
  </section>;
}
