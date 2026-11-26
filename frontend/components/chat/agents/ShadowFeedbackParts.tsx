"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Shares } from "@/lib/connected-apps";
import {
  FEEDBACK_REASONS, actionLabel, dismiss, feedbackError, isActionable, isDismissed, parseFeedbackResult, rangeText, reasonLabel,
  sharePayload, suggestionHeadline, type FeedbackReason, type ShareSuggestion,
} from "@/lib/shadow-feedback";

const BUTTON = "rounded-field border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:opacity-60";
const CHIP = "rounded-field border border-line px-2 py-1 text-xs hover:bg-hover disabled:opacity-60";

// Storage can be blocked or throw; the card then simply shows again.
function browserStorage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; }
}

// "Was this right?" under a thread where the worker and the owner differed: one tap with a reason, so it can be counted. The owner
// can change their mind (a later answer replaces the earlier one). Nothing here sends or changes anything in Gmail.
export function FeedbackRow({ agentId, threadId }: { agentId: number; threadId: string }) {
  const [open, setOpen] = useState(false);
  const [sent, setSent] = useState<FeedbackReason | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const send = async (reason: FeedbackReason) => {
    setBusy(true); setError(null);
    try {
      const done = parseFeedbackResult(await api.sendShadowFeedback(agentId, { thread_id: threadId, kind: "ranking", reason }));
      if (!done) throw new Error("The feedback was not saved in a form this page reads.");
      if (live.current) { setSent(done.reason); setOpen(false); }
    } catch (err) {
      if (live.current) setError(feedbackError(err instanceof ApiError ? err.status : undefined, err instanceof Error ? err.message : ""));
    } finally { if (live.current) setBusy(false); }
  };

  return (
    <div className="mt-1 space-y-1">
      {sent && !open && (
        <p className="text-xs text-muted">
          Noted: {reasonLabel(sent).toLowerCase()}.{" "}
          <button type="button" className="underline" onClick={() => setOpen(true)}>Change</button>
        </p>
      )}
      {!sent && !open && (
        <button type="button" className={CHIP} onClick={() => setOpen(true)}>Was this right?</button>
      )}
      {open && (
        <div className="space-y-1">
          <p className="text-xs text-muted">What was wrong with the worker&apos;s ranking here?</p>
          <div className="flex flex-wrap gap-1.5">
            {FEEDBACK_REASONS.map((reason) => (
              <button key={reason.value} type="button" className={CHIP} disabled={busy} onClick={() => void send(reason.value)}>{reason.label}</button>
            ))}
            <button type="button" className={CHIP} disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </div>
  );
}

// A proposal to raise or lower the worker's share of the mailbox, with the evidence and its range. It never changes anything by
// itself: the button asks the agent update to save the new share, the same request the settings control makes, and the person
// can say "Not now" (remembered for two weeks).
export function SuggestionCard({ agentId, suggestion, onSharesChanged, onChanged }: {
  agentId: number;
  suggestion: ShareSuggestion;
  onSharesChanged?: (shares: Shares) => void;
  onChanged?: () => void;
}) {
  const [hidden, setHidden] = useState(() => isDismissed(browserStorage(), suggestion));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  if (done) return <p role="status" className="rounded-field border border-line p-2 text-sm">{done}</p>;
  if (hidden || !isActionable(suggestion) || suggestion.agent_id !== agentId) return null;
  const range = rangeText(suggestion.evidence);

  const apply = async () => {
    setBusy(true); setError(null);
    try {
      const agent = (await api.listAgents()).find((candidate) => candidate.id === agentId);
      if (!agent) throw new Error("That agent is no longer here.");
      const made = sharePayload(agent, suggestion);
      if (!made.ok) throw new Error(made.message);
      const saved = await api.updateAgent(agentId, { connector_shares: made.connector_shares });
      onSharesChanged?.(saved.connector_shares ?? {});
      if (live.current) setDone(`Share changed from ${suggestion.current_percent}% to ${suggestion.suggested_percent}%.`);
      onChanged?.();
    } catch (err) {
      if (live.current) setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Could not change the share.");
    } finally { if (live.current) setBusy(false); }
  };

  return (
    <div className="space-y-2 rounded-field border border-line p-3" aria-label="Suggested change to the share">
      <p className="font-medium">{suggestionHeadline(suggestion)}</p>
      <p className="text-xs text-muted">
        {range ? `The real rate could be anywhere from ${range}. ` : ""}Based on {suggestion.evidence.evaluated} drafts you replied to over the
        last four weeks. This is a suggestion; nothing changes unless you press the button.
      </p>
      {error && <p role="alert" className="text-danger">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} disabled={busy} onClick={() => void apply()}>{busy ? "Saving…" : actionLabel(suggestion)}</button>
        <button type="button" className={BUTTON} disabled={busy} onClick={() => { dismiss(browserStorage(), suggestion); setHidden(true); }}>Not now</button>
      </div>
    </div>
  );
}
