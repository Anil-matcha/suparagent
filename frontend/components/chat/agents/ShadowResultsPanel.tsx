"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { levelOf, type Scopes, type Shares } from "@/lib/connected-apps";
import { parseShareSuggestion, type ShareSuggestion } from "@/lib/shadow-feedback";
import { FeedbackRow, SuggestionCard } from "./ShadowFeedbackParts";
import {
  disagreementText, isEmpty, parseShadowReport, percent, rateText, refreshMessage, sampleCaption,
  type ShadowReport,
} from "@/lib/shadow-report";

const BUTTON = "rounded-field border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:opacity-60";
const ROW = "flex items-baseline justify-between gap-3";

// What a support worker in shadow mode would have done, set beside what the owner did (BL-173). Numbers and thread ids only:
// the server never sends mail text, and nothing here sends or changes anything in Gmail. Shown only for a saved agent that
// has Gmail turned on.
export function ShadowResultsPanel({ agentId, connectorScopes, onSharesChanged }: {
  agentId: number | null;
  connectorScopes: Scopes | null | undefined;
  // Told the new shares when the owner accepts a suggested change, so the editor that holds this panel does not save the old ones back.
  onSharesChanged?: (shares: Shares) => void;
}) {
  const shown = agentId !== null && levelOf(connectorScopes, "gmail") !== "off";
  const [report, setReport] = useState<ShadowReport | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);
  const latest = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  // The proposal to raise or lower the share (BL-175), read beside the report. It is only ever a proposal and is secondary: a
  // failure to read it just hides the card. The newest request wins.
  const [suggestion, setSuggestion] = useState<ShareSuggestion | null>(null);
  const suggestionLatest = useRef(0);
  const loadSuggestion = useCallback((id: number, refresh: boolean) => {
    const mine = ++suggestionLatest.current;
    api.getShareSuggestion(id, { refresh }).then(
      (raw) => { if (live.current && mine === suggestionLatest.current) setSuggestion(parseShareSuggestion(raw)); },
      () => { if (live.current && mine === suggestionLatest.current) setSuggestion(null); },
    );
  }, []);

  // What to do with an answer. The newest request wins, so a slow answer never replaces a later one.
  const accept = useCallback((mine: number, raw: unknown) => {
    if (!live.current || mine !== latest.current) return;
    const parsed = parseShadowReport(raw);
    if (parsed) { setReport(parsed); setError(null); }
    else setError("The shadow results came back in a form this page does not read.");
  }, []);
  const reject = useCallback((mine: number, err: unknown) => {
    if (!live.current || mine !== latest.current) return;
    setError(refreshMessage(err instanceof ApiError ? err.status : undefined, err instanceof Error ? err.message : ""));
  }, []);

  // The first load, when the panel appears or the agent changes. State is set only in the callbacks.
  useEffect(() => {
    if (!shown || agentId === null) return;
    const mine = ++latest.current;
    api.getShadowReport(agentId, { days: 28, refresh: false }).then((raw) => accept(mine, raw), (err) => reject(mine, err));
    loadSuggestion(agentId, false);
  }, [shown, agentId, accept, reject, loadSuggestion]);

  const refresh = async () => {
    if (agentId === null) return;
    const mine = ++latest.current;
    setRefreshing(true); setError(null);
    loadSuggestion(agentId, true);
    try { accept(mine, await api.getShadowReport(agentId, { days: 28, refresh: true })); }
    catch (err) { reject(mine, err); }
    finally { if (live.current && mine === latest.current) setRefreshing(false); }
  };

  if (!shown || agentId === null) return null;
  // A report for another agent is never shown while this one's loads.
  const current = report !== null && report.agent_id === agentId ? report : null;

  return (
    <section className="space-y-2 rounded-field border border-line p-3 text-sm" aria-label="Shadow results">
      <div className={ROW}>
        <h3 className="font-semibold">Shadow results</h3>
        <button type="button" className={BUTTON} disabled={refreshing} onClick={() => void refresh()}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <p className="text-xs text-muted">
        The worker ranks your support mail and keeps its drafts in its notes; you reply as usual and this compares the two.
        Nothing is sent or changed in Gmail.
      </p>
      {suggestion !== null && suggestion.agent_id === agentId && (
        <SuggestionCard
          key={`${suggestion.state}-${suggestion.suggested_percent}`}
          agentId={agentId}
          suggestion={suggestion}
          onSharesChanged={onSharesChanged}
          onChanged={() => loadSuggestion(agentId, true)}
        />
      )}
      {error && <p role="alert" className="text-danger">{error}</p>}
      {current && isEmpty(current) && (
        <p className="text-muted">No shadow results yet. Ask the worker to rank your threads; it records what it would do.</p>
      )}
      {current && !isEmpty(current) && <ReportBody report={current} />}
      {!current && !error && <p className="text-muted">Loading…</p>}
    </section>
  );
}

function ReportBody({ report }: { report: ShadowReport }) {
  const { ranking, drafts } = report;
  const answered = drafts.as_is + drafts.light_edit + drafts.rewrite;
  const generated = new Date(report.generated_at);
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        {sampleCaption(report)}
        {Number.isNaN(generated.getTime()) ? "" : ` Updated ${generated.toLocaleString()}.`}
      </p>

      <div className="space-y-1">
        <h4 className="text-xs font-medium">Ranking</h4>
        {ranking.too_few ? (
          <p className="text-muted">Too few to judge yet. There need to be more ranked threads with a reply from you first.</p>
        ) : (
          <>
            <p className={ROW}><span>Worker&apos;s top five answered within 24 hours</span><span className="font-medium">{rateText(ranking.top5_answered_24h, false)}</span></p>
            <p className={ROW}><span>All threads answered within 24 hours</span><span className="font-medium">{rateText(ranking.baseline_answered_24h, false)}</span></p>
          </>
        )}
      </div>

      <div className="space-y-1">
        <h4 className="text-xs font-medium">Drafts</h4>
        <p className={ROW}><span>Used as written</span><span className="font-medium">{drafts.as_is}</span></p>
        <p className={ROW}><span>Lightly edited</span><span className="font-medium">{drafts.light_edit}</span></p>
        <p className={ROW}><span>Rewritten</span><span className="font-medium">{drafts.rewrite}</span></p>
        <p className={ROW}><span>No reply seen yet</span><span className="font-medium">{drafts.no_reply_seen}</span></p>
        {drafts.too_few ? (
          <p className="text-muted">Too few to judge yet. A usable rate needs more drafts with a reply from you.</p>
        ) : (
          <p className={ROW}><span>Usable as written or lightly edited</span><span className="font-medium">{percent(drafts.usable_rate)} of {answered}</span></p>
        )}
      </div>

      {report.disagreements.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-xs font-medium">Where you and the worker differed</h4>
          <ul className="space-y-1.5">
            {report.disagreements.map((item) => (
              <li key={`${item.thread_id}-${item.kind}`}>
                <p>{disagreementText(item)}</p>
                <p className="break-all text-xs text-muted">Thread {item.thread_id}</p>
                <FeedbackRow agentId={report.agent_id} threadId={item.thread_id} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
