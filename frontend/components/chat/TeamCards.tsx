"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { collaboration } from "@/lib/collaboration";
import { can } from "@/lib/permissions";
import type { TeamJobItem, TeamSetupOperation, TeamSetupPlan, TranscriptItem } from "@/lib/transcript";
import { JobCard } from "./TeamPanel";
import { useSession } from "@/components/AuthGate";
import { useT } from "@/lib/use-i18n";

export function DelegationCard({ item }: { item: TeamJobItem }) {
  return <JobCard job={{ id: item.jobId, root_id: null, parent_id: null, depth: 0, requester_agent_id: 0, requester_thread_id: null, assignee_agent_id: item.agentId, assignee_thread_id: item.threadId, mode: item.mode, brief: item.brief, status: item.status, created_at: new Date().toISOString(), started_at: null, finished_at: null, result: item.result, outcome: item.outcome }} agents={[]} onChanged={() => {}} />;
}
export function AgentCreatedCard({ item }: { item: Extract<TranscriptItem, { kind: "agent_created" }> }) {
  const [state, setState] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const { session } = useSession();
  useEffect(() => {
    let live = true;
    collaboration.undoState(item.agentId).then(r => { if (live) setAvailable(r.available); }, () => { if (live) setAvailable(false); });
    return () => { live = false; };
  }, [item.agentId]);
  const undo = async () => {
    setState("busy"); setError(null);
    try { const result = await collaboration.undoCreate(item.agentId); setState(result.removed); }
    catch (err) { setState(""); setError(err instanceof ApiError ? err.message : "Could not undo this specialist."); }
  };
  return <article className="space-y-2 rounded-field border border-line p-3 text-sm"><p><strong>Specialist created: {item.name || `Agent ${item.agentId}`}</strong>{item.title ? ` · ${item.title}` : ""}</p><p className="text-xs text-muted">Instructions were written by the lead. Review them in Agents.</p>{session?.scopes.includes("admin") && available && !state && <button type="button" className="text-xs underline" onClick={() => void undo()}>Undo creation</button>}{state && <p role="status" className="text-xs text-muted">{state === "busy" ? "Undoing…" : `Specialist ${state}.`}</p>}{error && <p role="alert" className="text-xs text-danger">{error}</p>}</article>;
}

const STALE: Record<string, string> = {
  changed_since: "Something changed since this was applied, so it was left alone.",
  agent_used: "A specialist this added has already been used, so it was kept.",
  agent_busy: "A teammate is working right now. Try again when it is idle.",
};
const VERB: Record<string, string> = { created: "Added", updated: "Updated", deleted: "Deleted" };

function ProposalField({ label, value, onChange, multiline = false, disabled = false, current }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean; disabled?: boolean; current?: string | null }) {
  return <label className="block text-xs font-medium">{label}{current !== undefined && <span className="ml-2 font-normal text-muted">Current: {current ?? "Default / empty"}</span>}{multiline
    ? <textarea disabled={disabled} value={value} onChange={event => onChange(event.target.value)} rows={3} className="mt-1 w-full rounded-field border border-line bg-surface px-2 py-1 text-sm disabled:opacity-70" />
    : <input disabled={disabled} value={value} onChange={event => onChange(event.target.value)} className="mt-1 w-full rounded-field border border-line bg-surface px-2 py-1 text-sm disabled:opacity-70" />}</label>;
}

function TeamSetupProposal({ item }: { item: Extract<TranscriptItem, { kind: "team_setup" }> }) {
  const { session } = useSession();
  const [plan, setPlan] = useState<TeamSetupPlan>(item.plan ?? { operations: [] });
  const [savedPlan, setSavedPlan] = useState<TeamSetupPlan>(item.plan ?? { operations: [] });
  const [version, setVersion] = useState(item.version ?? 0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canReview = session !== null && session !== undefined && (session.scopes.includes("admin") || (session.scopes.includes("manage") && can(session.permissions, "agents")));
  const isAdmin = session?.scopes.includes("admin") === true;
  const locked = item.state === "rejected" || message === "approved" || message === "declined";
  const needsAdmin = !isAdmin && plan.operations.some(operation => operation.op === "create" && operation.share_workspace === true);
  const dirty = JSON.stringify(plan) !== JSON.stringify(savedPlan);
  const stale = (err: unknown) => err instanceof ApiError && ["changed_since", "request_not_found", "lead_only"].includes(err.code ?? "");

  useEffect(() => {
    const current = item.plan ?? { operations: [] };
    // Resets the editable copy when a newer version of the plan arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPlan(current); setSavedPlan(current); setVersion(item.version ?? 0);
  }, [item.version, item.plan]);
  const changeOperation = (index: number, update: (operation: TeamSetupOperation) => TeamSetupOperation) => {
    setPlan(current => ({ ...current, operations: current.operations.map((operation, i) => i === index ? update(operation) : operation) }));
    setError(null);
  };
  const save = async () => {
    if (!canReview || busy || locked) return;
    setBusy(true); setError(null); setMessage("");
    try { const result = await collaboration.updateSetupProposal(item.requestId, version, plan); setVersion(result.version); setPlan(result.plan); setSavedPlan(result.plan); setMessage("Saved revision."); }
    catch (err) { setError(stale(err) ? "This proposal changed or closed elsewhere. Refresh the conversation before editing again." : err instanceof ApiError ? err.message : "Could not save this revision."); }
    finally { setBusy(false); }
  };
  const decide = async (decision: "approve" | "decline") => {
    if (!canReview || busy || locked) return;
    setBusy(true); setError(null); setMessage("");
    try {
      if (decision === "approve") {
        let decisionVersion = version;
        if (dirty) {
          const saved = await collaboration.updateSetupProposal(item.requestId, version, plan);
          decisionVersion = saved.version;
          setVersion(saved.version); setPlan(saved.plan); setSavedPlan(saved.plan);
        }
        await collaboration.approveSetupProposal(item.requestId, decisionVersion); setMessage("approved");
      }
      else { await collaboration.declineSetupProposal(item.requestId, version); setMessage("declined"); }
    } catch (err) { setError(stale(err) ? "This proposal changed or closed elsewhere. Refresh the conversation before deciding." : err instanceof ApiError ? err.message : "Could not update this proposal."); }
    finally { setBusy(false); }
  };

  return <article className="space-y-3 rounded-field border border-line p-3 text-sm">
    <div><strong>{item.state === "rejected" || message === "declined" ? "Team plan declined" : message === "approved" ? "Team plan approved" : "Review team plan"}</strong><p className="mt-1 text-xs text-muted">Review the specialist roles and requested changes before applying them.</p></div>
    {!locked && <ProposalField label="Plan summary" value={plan.summary ?? ""} disabled={!canReview || busy} onChange={summary => setPlan(current => ({ ...current, summary }))} multiline />}
    {!canReview && plan.summary && <p className="text-sm">{plan.summary}</p>}
    {!locked ? <>
      <ol className="space-y-3">{plan.operations.map((operation, index) => <li key={`${operation.op}-${index}`} className="rounded-field border border-line p-3">
        <div className="mb-2 flex items-center justify-between"><strong>{operation.op === "create" ? `New specialist ${index + 1}` : `Update ${operation.name || `agent ${operation.agent_id}`}`}</strong>{canReview && plan.operations.length > 1 && <button type="button" className="text-xs underline" disabled={busy} onClick={() => setPlan(current => ({ ...current, operations: current.operations.filter((_, i) => i !== index) }))}>Remove</button>}</div>
        {operation.op === "create" ? <div className="space-y-2">{(["name", "title", "description", "instructions", "provider", "model", "first_task"] as const).map(field => <ProposalField key={field} label={field.replaceAll("_", " ")} value={operation[field] ?? ""} disabled={!canReview || busy} multiline={field === "description" || field === "instructions" || field === "first_task"} onChange={value => changeOperation(index, current => current.op === "create" ? { ...current, [field]: field === "model" && value === "" ? null : value } : current)} />)}<label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={!canReview || busy || (!isAdmin && operation.share_workspace !== true)} checked={operation.share_workspace === true} onChange={event => changeOperation(index, current => current.op === "create" ? { ...current, share_workspace: event.target.checked } : current)} /> Share the lead’s workspace{!isAdmin && operation.share_workspace === true ? " (admin approval required)" : ""}</label></div>
          : <div className="space-y-2">{Object.entries(operation.fields).map(([field, value]) => <ProposalField key={field} label={field.replaceAll("_", " ")} value={value ?? ""} current={operation.current?.[field]} disabled={!canReview || busy} multiline={field === "instructions" || field === "description"} onChange={next => changeOperation(index, current => current.op === "update" ? { ...current, fields: { ...current.fields, [field]: field === "model" && next === "" ? null : next } } : current)} />)}<p className="text-xs text-muted">Revision {operation.expected_revision}</p></div>}
      </li>)}</ol>
      {needsAdmin && <p className="text-xs text-muted">An admin must approve a plan that shares the lead’s workspace.</p>}
      {canReview && <div className="flex flex-wrap gap-3"><button type="button" className="text-xs underline" disabled={busy || plan.operations.length < 1 || !dirty} onClick={() => void save()}>{busy ? "Saving…" : "Save revision"}</button><button type="button" className="text-xs underline" disabled={busy || needsAdmin || plan.operations.length < 1} onClick={() => void decide("approve")}>{busy ? "Working…" : dirty ? "Save & approve" : "Approve plan"}</button><button type="button" className="text-xs underline" disabled={busy} onClick={() => void decide("decline")}>Decline</button></div>}
    </> : <p className="text-xs text-muted">{item.state === "rejected" || message === "declined" ? "This proposal is closed." : "Applying the approved plan…"}</p>}
    {message === "Saved revision." && <p role="status" className="text-xs text-muted">Saved revision {version}.</p>}{error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </article>;
}

export function TeamSetupCard({ item }: { item: Extract<TranscriptItem, { kind: "team_setup" }> }) {
  return item.state === "pending" || item.state === "rejected"
    ? <TeamSetupProposal item={item} />
    : <AppliedTeamSetupCard item={item} />;
}

function AppliedTeamSetupCard({ item }: { item: Extract<TranscriptItem, { kind: "team_setup" }> }) {
  const t = useT();
  const [state, setState] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { session } = useSession();
  const [availability, setAvailability] = useState<{ available: boolean; reason: string | null } | null>(null);
  useEffect(() => {
    if (!session?.scopes.includes("admin")) return;
    let live = true;
    const refresh = () => collaboration.setupReceipt(item.requestId).then(result => {
      if (live) setAvailability({ available: result.undo_available, reason: result.undo_reason });
    }, () => { if (live) setAvailability({ available: false, reason: "Could not check Undo availability." }); });
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    window.addEventListener("focus", refresh);
    return () => { live = false; clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [item.requestId, session]);
  const undo = async () => {
    setState("busy"); setError(null);
    try { await collaboration.undoSetup(item.requestId); setState("undone"); }
    catch (err) { setState(""); setError(err instanceof ApiError ? STALE[err.code ?? ""] ?? err.message : "Could not undo this change."); }
  };
  const canUndo = availability?.available === true && !state && session?.scopes.includes("admin");
  return (
    <article className="space-y-2 rounded-field border border-line p-3 text-sm">
      <p><strong>Team changes applied</strong></p>
      <ul className="space-y-2 text-xs text-muted">{item.rows.map((row) => <li key={`${row.action}-${row.agentId}`}>
        {VERB[row.action] ?? row.action}: {row.name || `Agent ${row.agentId}`}
        {row.changes && <details className="mt-1"><summary className="cursor-pointer underline">Details</summary><dl className="mt-2 space-y-2">{row.changes.map(change => <div key={change.field}><dt className="font-medium">{change.field.replaceAll("_", " ")}</dt><dd className="whitespace-pre-wrap break-words"><del>{change.before == null ? "Default / empty" : String(change.before)}</del>{" → "}{change.after == null ? "Default / empty" : String(change.after)}</dd></div>)}</dl><p className="mt-2">{t("agentTeam.teamSavedDefaults")}</p></details>}
        {row.action !== "deleted" && <div className="mt-1 flex flex-wrap gap-3"><button type="button" className="underline" onClick={() => window.dispatchEvent(new CustomEvent("suparagent:open-agent", { detail: { id: row.agentId, edit: false } }))}>Start chat</button>{row.firstTask && <button type="button" className="underline" onClick={() => window.dispatchEvent(new CustomEvent("suparagent:open-agent", { detail: { id: row.agentId, edit: false, firstTask: row.firstTask } }))}>Start with this task</button>}{session?.scopes.includes("admin") && <button type="button" className="underline" onClick={() => window.dispatchEvent(new CustomEvent("suparagent:open-agent", { detail: { id: row.agentId, edit: true } }))}>Edit agent</button>}</div>}
      </li>)}</ul>
      {item.undoAvailable && <p className="text-xs text-muted">{t("agentTeam.undoCaveat")}</p>}
      {availability && !availability.available && !state && <p className="text-xs text-muted">{availability.reason}</p>}
      {canUndo && <button type="button" className="text-xs underline" onClick={() => void undo()}>Undo</button>}
      {state && <p role="status" className="text-xs text-muted">{state === "busy" ? "Undoing…" : "Undone."}</p>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </article>
  );
}

const NOTICE_LABEL = { incident: "Incident", escalation: "Escalation", refused: "Report not delivered", retried: "Retry" } as const;

// What the lead was told about a teammate (or what it retried). The text is
// quoted data from other agents; it is only ever shown, never acted on here.
export function TeamNoticeCard({ item }: { item: Extract<TranscriptItem, { kind: "team_notice" }> }) {
  return (
    <article className="flex items-start justify-between gap-3 rounded-field border border-line p-3 text-sm">
      <p className="min-w-0 break-words"><strong>{NOTICE_LABEL[item.tone]}</strong>{" · "}{item.text.replace(/^(Incident|Escalation): /, "")}</p>
      {item.threadId !== null && <Link href={`/chat?thread=${item.threadId}`} className="shrink-0 text-xs underline">Open</Link>}
    </article>
  );
}
