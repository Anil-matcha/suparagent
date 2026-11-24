"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { limitsSentence, type SpendLimits } from "@/lib/spend";
import type { HiredWorker } from "@/lib/templates";

// After a hire: for each worker that suggests paid MuAPI tools, an explicit "Grant these tools" step and the
// per-worker switch for scheduled runs. When MuAPI is connected the suggested tools are granted automatically (paid calls
// still ask in chats and stay inside the spending limits); scheduled spending stays off until the person turns it on.

type Row = { worker: HiredWorker; tools: string[] };

type Status = { granted: boolean; paidUse: boolean; busy: boolean; error: string | null };
const NEW_STATUS: Status = { granted: false, paidUse: false, busy: false, error: null };

export function WorkerGrants({ workers, suggestedTools }: { workers: HiredWorker[]; suggestedTools: Record<string, string[]> }) {
  const rows: Row[] = workers
    .map((worker) => ({ worker, tools: suggestedTools[worker.template_id] ?? [] }))
    .filter((row) => row.tools.length > 0);
  const workerIds = rows.map((row) => row.worker.agent_id).join(",");
  const [limits, setLimits] = useState<SpendLimits | null>(null);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Record<number, Status>>({});

  useEffect(() => {
    if (rows.length === 0) return;
    let live = true;
    Promise.all([api.getMuapiIntegration(), api.getSpend()]).then(async ([integration, overview]) => {
      const grants = await Promise.all(rows.map(async (row) => {
        try { return [row.worker.agent_id, (await api.getMuapiAgentGrants(row.worker.agent_id)).tool_names] as const; }
        catch { return [row.worker.agent_id, [] as string[]] as const; }
      }));
      if (!live) return;
      setConfigured(integration.configured);
      setLimits(overview.limits);
      const initial = Object.fromEntries(rows.map((row) => {
        const existing = grants.find(([id]) => id === row.worker.agent_id)?.[1] ?? [];
        const worker = overview.workers.find((item) => item.agent_id === row.worker.agent_id);
        return [row.worker.agent_id, { ...NEW_STATUS, granted: row.tools.every((name) => existing.includes(name)), paidUse: worker?.scheduled_paid_use ?? false }];
      }));
      setStatus(initial);
      if (!integration.configured) return;
      // Default on: grant the suggested tools to each worker that does not have them yet.
      await Promise.all(rows.filter((row) => !initial[row.worker.agent_id]?.granted).map(async (row) => {
        const id = row.worker.agent_id;
        try {
          const existing = grants.find(([agent]) => agent === id)?.[1] ?? [];
          await api.setMuapiAgentGrants(id, [...new Set([...existing, ...row.tools])]);
          if (live) setStatus((now) => ({ ...now, [id]: { ...(now[id] ?? NEW_STATUS), granted: true } }));
        } catch (e) {
          if (live) setStatus((now) => ({ ...now, [id]: { ...(now[id] ?? NEW_STATUS), error: e instanceof Error ? e.message : "Could not grant the tools." } }));
        }
      }));
    }).catch(() => { if (live) setConfigured(false); });
    return () => { live = false; };
  }, [workerIds]);

  if (rows.length === 0) return null;

  const update = (agentId: number, change: Partial<Status>) =>
    setStatus((now) => ({ ...now, [agentId]: { ...(now[agentId] ?? NEW_STATUS), ...change } }));

  const grant = async (row: Row) => {
    if (!configured) return;
    const id = row.worker.agent_id;
    update(id, { busy: true, error: null });
    try {
      await api.setMuapiAgentGrants(id, row.tools);
      update(id, { granted: true, busy: false });
    } catch (e) {
      update(id, { busy: false, error: e instanceof ApiError || e instanceof Error ? e.message : "Could not grant the tools." });
    }
  };

  const setPaidUse = async (row: Row, on: boolean) => {
    const id = row.worker.agent_id;
    update(id, { busy: true, error: null });
    try {
      await api.setWorkerPaidUse(id, on);
      update(id, { paidUse: on, busy: false });
    } catch (e) {
      update(id, { busy: false, error: e instanceof ApiError || e instanceof Error ? e.message : "Could not change this setting." });
    }
  };

  return (
    <section aria-label="Tools and spending" className="space-y-3 border-t border-line pt-3">
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted">Let your workers use MuAPI</h4>
      <p className="text-xs text-muted">
        Workers can search, inspect and estimate for free. Paid calls cost MuAPI credits and are refused above{" "}
        {limits ? limitsSentence(limits) : "your spending limits"}. The tools are granted for you once MuAPI is connected; scheduled spending stays off until you turn it on.
      </p>
      {configured === false && (
        <p className="text-xs text-muted">
          Connect MuAPI first. <Link href="/settings/integrations/" className="underline">Open Integration settings</Link>, then grant tools from Agent access there.
        </p>
      )}
      <ul className="space-y-2">
        {rows.map((row) => {
          const s = status[row.worker.agent_id] ?? NEW_STATUS;
          return (
            <li key={row.worker.agent_id} className="space-y-1 rounded-md border border-line p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{row.worker.name}</span>
                {s.granted ? (
                  <span className="text-success">Tools granted</span>
                ) : (
                  <button
                    type="button"
                    disabled={s.busy || !configured}
                    onClick={() => void grant(row)}
                    className="cursor-pointer rounded-md border border-line px-2 py-0.5 font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {s.busy ? "Granting…" : "Grant these tools"}
                  </button>
                )}
              </div>
              {s.granted && (
                <label className="flex cursor-pointer items-start gap-2">
                  <input type="checkbox" className="mt-0.5" checked={s.paidUse} disabled={s.busy} onChange={(e) => void setPaidUse(row, e.target.checked)} />
                  <span>Let scheduled runs spend within these limits, without asking each time. Off by default. Chats still ask before every paid call.</span>
                </label>
              )}
              {s.error && <p role="alert" className="text-danger">{s.error}</p>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
