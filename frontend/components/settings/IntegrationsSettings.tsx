"use client";

import { useEffect, useState } from "react";
import { api, type Agent } from "@/lib/api";
import { INTEGRATIONS } from "@/lib/integrations";
import { IntegrationPanel } from "./integrations/IntegrationPanel";

// The page title and its one-line summary come from the Settings frame (lib/settings-nav.ts), so this
// component starts straight with the integrations.
export function IntegrationsSettings() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [scheduled, setScheduled] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.listAgents(), api.getSpend()]).then(([people, spend]) => {
      if (!live) return;
      setAgents(people);
      setScheduled(Object.fromEntries(spend.workers.map((worker) => [worker.agent_id, worker.scheduled_paid_use])));
    }).catch((cause: unknown) => {
      if (live) setError(cause instanceof Error ? cause.message : "Could not load integrations.");
    });
    return () => { live = false; };
  }, []);

  return (
    <div className="space-y-5">
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {agents && INTEGRATIONS.map((manifest) => (
        <IntegrationPanel
          key={manifest.id}
          manifest={manifest}
          agents={agents}
          scheduled={scheduled}
          onScheduledChange={(agentId, value) => setScheduled((current) => ({ ...current, [agentId]: value }))}
        />
      ))}
    </div>
  );
}
