"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Agent } from "@/lib/api";
import { canRestore, describeRevision, type AgentRevision } from "@/lib/agent-history";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";

// Stored versions of one agent's profile (the last 20), with a restore that is itself a new version.
export function AgentHistory({ agent, onRestored }: { agent: Agent; onRestored: () => void }) {
  const toast = useToast();
  const [revisions, setRevisions] = useState<AgentRevision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<AgentRevision | null>(null);
  const [busy, setBusy] = useState(false);
  const newest = revisions && revisions.length ? revisions[0].number : null;

  const load = useCallback(async () => {
    try {
      setRevisions(await api.listAgentRevisions(agent.id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the history.");
    }
  }, [agent.id]);
  useEffect(() => {
    let live = true;
    api.listAgentRevisions(agent.id).then(
      (rows) => { if (live) setRevisions(rows); },
      (e) => { if (live) setError(e instanceof Error ? e.message : "Could not load the history."); },
    );
    return () => { live = false; };
  }, [agent.id]);

  const restore = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await api.restoreAgentRevision(agent.id, target.number, newest);
      toast(`Restored version ${target.number}.`);
      setTarget(null);
      onRestored();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not restore that version.");
      setTarget(null);
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        Earlier versions of this agent&apos;s settings. Restoring one is saved as a new version, so it can be undone.
        Text that looked like a secret was hidden and is not put back.
      </p>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {revisions === null && !error && <p className="text-sm text-muted">Loading…</p>}
      {revisions?.length === 0 && <p className="text-sm text-muted">No changes recorded yet.</p>}
      <ul className="space-y-2">
        {revisions?.map((revision) => (
          <li key={revision.number} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-2 text-sm">
            <div className="min-w-0">
              <div className="font-medium">Version {revision.number}{revision.number === newest ? " · current" : ""}</div>
              <div className="text-xs text-muted">
                {describeRevision(revision)} · {new Date(revision.created_at).toLocaleString()}
                {revision.redacted.length > 0 && " · some text hidden"}
              </div>
            </div>
            {canRestore(revision, newest) && (
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => setTarget(revision)}>Restore</Button>
            )}
          </li>
        ))}
      </ul>
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => { if (!open) setTarget(null); }}
        title={`Restore version ${target?.number ?? ""}?`}
        description="This agent's settings go back to that version. Conversations pick up the change from their next message. The current settings stay in the history."
        confirmLabel="Restore"
        loading={busy}
        onConfirm={() => void restore()}
      />
    </div>
  );
}
