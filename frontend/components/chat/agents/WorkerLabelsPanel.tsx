"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { levelOf, type Scopes } from "@/lib/connected-apps";
import {
  isEmpty, labelRows, lastApplied, loadErrorText, parseUndoResult, parseWorkerLabels, plural, undoErrorText, undoQuestion,
  undoResultText, type WorkerLabels,
} from "@/lib/worker-labels";

const BUTTON = "rounded-field border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:opacity-60";
const ROW = "flex items-baseline justify-between gap-3";

// The labels a support worker added to mail threads, all under Worker/ (BL-173), and one click to take exactly those off again.
// Counts and label names only: the server never sends mail text. Shown only for a saved agent whose Gmail level is the
// label level. Undo asks inside the panel first, and says what it removed, what was already gone and what to retry.
export function WorkerLabelsPanel({ agentId, connectorScopes }: { agentId: number | null; connectorScopes: Scopes | null | undefined }) {
  const shown = agentId !== null && levelOf(connectorScopes, "gmail") === "labels";
  const [data, setData] = useState<WorkerLabels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const live = useRef(true);
  const latest = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  // What to do with an answer. The newest request wins, so a slow answer never replaces a later one.
  const accept = useCallback((mine: number, raw: unknown) => {
    if (!live.current || mine !== latest.current) return;
    const parsed = parseWorkerLabels(raw);
    if (parsed) { setData(parsed); setError(null); }
    else setError("The worker's labels came back in a form this page does not read.");
  }, []);
  const reject = useCallback((mine: number, err: unknown) => {
    if (!live.current || mine !== latest.current) return;
    setError(loadErrorText(err instanceof ApiError ? err.status : undefined, err instanceof Error ? err.message : ""));
  }, []);

  // The first load, when the panel appears or the agent changes. State is set only in the callbacks.
  useEffect(() => {
    if (!shown || agentId === null) return;
    const mine = ++latest.current;
    api.getWorkerLabels(agentId).then((raw) => accept(mine, raw), (err) => reject(mine, err));
  }, [shown, agentId, accept, reject]);

  const reload = async () => {
    if (agentId === null) return;
    const mine = ++latest.current;
    try { accept(mine, await api.getWorkerLabels(agentId)); }
    catch (err) { reject(mine, err); }
  };

  const undo = async () => {
    if (agentId === null || undoing) return;
    setUndoing(true); setError(null); setOutcome(null);
    try {
      const result = parseUndoResult(await api.undoWorkerLabels(agentId));
      if (!live.current) return;
      if (result) setOutcome(undoResultText(result));
      else setError("The removal came back in a form this page does not read. Check the labels, then try again.");
      setConfirming(false);
      await reload();
    } catch (err) {
      if (live.current) {
        setError(undoErrorText(err instanceof ApiError ? err.status : undefined, err instanceof Error ? err.message : ""));
        setConfirming(false);
      }
    } finally { if (live.current) setUndoing(false); }
  };

  if (!shown || agentId === null) return null;
  // Labels of another agent are never shown while this one's load.
  const current = data !== null && data.agent_id === agentId ? data : null;
  const last = current ? lastApplied(current) : null;

  return (
    <section className="space-y-2 rounded-field border border-line p-3 text-sm" aria-label="Worker labels">
      <h3 className="font-semibold">Worker labels</h3>
      <p className="text-xs text-muted">
        Labels this worker added to threads, all under Worker/. Undo removes exactly those labels from those threads and changes nothing else.
      </p>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {outcome && <p role="status" className="text-muted">{outcome}</p>}
      {!current && !error && <p className="text-muted">Loading…</p>}
      {current && isEmpty(current) && (
        <p className="text-muted">No labels added yet. When the worker labels a thread it appears here.</p>
      )}
      {current && !isEmpty(current) && (
        <div className="space-y-3">
          <div className="space-y-1">
            <p className={ROW}><span>Labels added</span><span className="font-medium">{current.applied}</span></p>
            <p className={ROW}><span>Threads labelled</span><span className="font-medium">{current.threads}</span></p>
            {last && <p className={ROW}><span>Last added</span><span className="font-medium">{last.toLocaleString()}</span></p>}
          </div>
          {current.labels.length > 0 && (
            <ul className="space-y-1" aria-label="Labels by name">
              {labelRows(current).map((row) => (
                <li key={row.name} className={ROW}>
                  <span className="break-all">{row.name}</span>
                  <span className="text-muted">{plural(row.count, "thread")}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {current && confirming && !isEmpty(current) ? (
        <div role="group" aria-label="Confirm removing the labels" className="space-y-2 rounded-field border border-line bg-inset p-2">
          <p>{undoQuestion(current)}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} disabled={undoing} onClick={() => void undo()}>
              {undoing ? "Removing…" : "Yes, remove them"}
            </button>
            <button type="button" className={BUTTON} disabled={undoing} onClick={() => setConfirming(false)}>Keep them</button>
          </div>
        </div>
      ) : (
        current && (
          <button type="button" className={BUTTON} disabled={undoing || isEmpty(current)} onClick={() => { setOutcome(null); setConfirming(true); }}>
            Undo
          </button>
        )
      )}
    </section>
  );
}
