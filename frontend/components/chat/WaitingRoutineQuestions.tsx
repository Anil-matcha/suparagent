"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type WaitingRoutineRun } from "@/lib/api";
import { waitingQuestionsFor } from "@/lib/routines";
import { t } from "@/lib/i18n";
import { SharedApproval } from "./SharedApproval";

const POLL_MS = 5000;

// Questions a scheduled routine of this agent is waiting on, shown in the
// agent's own chat. Answers use the same approval route as the routine's own
// conversation, so the run resumes exactly as if answered there.
export function WaitingRoutineQuestions({ agentId, openThreadId }: { agentId: number | null; openThreadId: number | null }) {
  const [state, setState] = useState<{ agentId: number; runs: WaitingRoutineRun[] } | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    if (agentId === null) return;
    let live = true;
    const load = () => api.listWaitingRoutineRuns(agentId).then(
      (runs) => { if (live) setState({ agentId, runs }); },
      () => { /* keep what is shown */ },
    );
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, POLL_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [agentId, tick]);

  const waiting = state !== null && state.agentId === agentId ? state.runs : [];
  const shown = waitingQuestionsFor(waiting, openThreadId);
  if (shown.length === 0) return null;
  return (
    <section aria-live="polite" className="mx-auto w-full max-w-3xl space-y-3 px-4 pt-3">
      {shown.map((run) => (
        <div key={run.run_id} className="space-y-2 rounded-lg border border-amber-400/60 p-3">
          <p className="text-sm font-medium">{t("knowledgeRoutines.waitingInChat", { name: run.routine_name })}</p>
          {run.approvals.map((approval) => (
            <SharedApproval
              key={approval.id}
              onChanged={refresh}
              approval={{ id: approval.id, thread_id: run.thread_id, turn_id: 0, tool: approval.tool, input: approval.input, summary: approval.summary, kind: approval.kind, status: "pending" }}
            />
          ))}
        </div>
      ))}
    </section>
  );
}
