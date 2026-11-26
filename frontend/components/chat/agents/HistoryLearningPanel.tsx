"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { levelOf, type Scopes } from "@/lib/connected-apps";
import {
  CHECK_NOTE, DEFAULT_BOUND, OFFER_DETAIL, OFFER_QUESTION, POLICY_NOTE, SEARCH_HELP, STAGE, TEXT_MAX, acceptText, cleanQuery,
  destinationText, errorText, extendChoices, forgetQuestion, forgetResultText, groupLessons, lessonCaption, lessonLabel,
  parseForget, parseHistoryLearning, parseLessons, phaseOf, progressText, type AccountUpdate, type HistoryAccount,
  type HistoryLearning, type Lesson, type Lessons,
} from "@/lib/history-learning";

const BUTTON = "rounded-field border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:opacity-60";
const INPUT = "mt-1 w-full rounded-field border border-line bg-surface px-2 py-1 text-sm disabled:opacity-70";

const statusOf = (err: unknown) => (err instanceof ApiError ? err.status : undefined);
const messageOf = (err: unknown) => (err instanceof Error ? err.message : "");

// The owner's switch for learning from their past replies (BL-174): per Gmail account the agent may read, off by default,
// with the support search it must be given, one stage of 30 threads to start and more only when the owner allows it.
// Shown only for a saved agent whose Gmail is not off. Nothing here reads mail; it only sets what the worker may read.
export function HistoryLearningPanel({ agentId, connectorScopes }: { agentId: number | null; connectorScopes: Scopes | null | undefined }) {
  const shown = agentId !== null && levelOf(connectorScopes, "gmail") !== "off";
  const [data, setData] = useState<HistoryLearning | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [confirmForget, setConfirmForget] = useState(false);
  const live = useRef(true);
  const latest = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const accept = useCallback((mine: number, raw: unknown) => {
    if (!live.current || mine !== latest.current) return;
    const parsed = parseHistoryLearning(raw);
    if (parsed) { setData(parsed); setError(null); }
    else setError("The settings came back in a form this page does not read.");
  }, []);
  const reject = useCallback((mine: number, err: unknown) => {
    if (!live.current || mine !== latest.current) return;
    setError(errorText(statusOf(err), messageOf(err)));
  }, []);

  useEffect(() => {
    if (!shown || agentId === null) return;
    const mine = ++latest.current;
    api.getHistoryLearning(agentId).then((raw) => accept(mine, raw), (err) => reject(mine, err));
  }, [shown, agentId, accept, reject]);

  const reload = async () => {
    if (agentId === null) return;
    const mine = ++latest.current;
    try { accept(mine, await api.getHistoryLearning(agentId)); } catch (err) { reject(mine, err); }
  };

  const change = async (account: HistoryAccount, body: AccountUpdate) => {
    if (agentId === null || busy) return;
    setBusy(true); setError(null); setNote(null);
    try {
      await api.updateHistoryLearning(agentId, account.account_id, body);
      await reload();
    } catch (err) { if (live.current) setError(errorText(statusOf(err), messageOf(err))); }
    finally { if (live.current) setBusy(false); }
  };

  const forget = async () => {
    if (agentId === null || busy) return;
    setBusy(true); setError(null); setNote(null);
    try {
      const result = parseForget(await api.forgetHistoryLearning(agentId));
      if (live.current) { setNote(result ? forgetResultText(result) : "Removed."); setConfirmForget(false); }
      await reload();
    } catch (err) { if (live.current) { setError(errorText(statusOf(err), messageOf(err))); setConfirmForget(false); } }
    finally { if (live.current) setBusy(false); }
  };

  if (!shown || agentId === null) return null;
  const current = data !== null && data.agent_id === agentId ? data : null;

  return (
    <section className="space-y-2 rounded-field border border-line p-3 text-sm" aria-label="Learn from my past replies">
      <h3 className="font-semibold">{OFFER_QUESTION}</h3>
      <p className="text-xs text-muted">{OFFER_DETAIL}</p>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {note && <p role="status" className="text-muted">{note}</p>}
      {!current && !error && <p className="text-muted">Loading…</p>}
      {current && current.accounts.length === 0 && (
        <p className="text-muted">Choose which Gmail account this agent may read above, then come back here.</p>
      )}
      {current?.accounts.map((account) => {
        const phase = phaseOf(account);
        const draft = queries[account.account_id] ?? account.query;
        const valid = cleanQuery(draft);
        const options = extendChoices(account);
        return (
          <div key={account.account_id} className="space-y-2 rounded-field border border-line bg-inset p-2" role="group" aria-label={`Past replies, ${account.account_id}`}>
            <p className="break-all text-xs font-medium">{account.account_id}</p>
            {(phase === "off" || phase === "declined") && (
              <>
                {phase === "declined" && <p className="text-xs text-muted">You chose not now. You can start whenever you like.</p>}
                <label className="block text-xs font-medium">
                  Gmail label or search for your support mail
                  <input className={INPUT} value={draft} maxLength={200} disabled={busy} placeholder="label:support"
                    onChange={(event) => setQueries({ ...queries, [account.account_id]: event.target.value })} />
                </label>
                <p className="text-xs text-muted">{SEARCH_HELP}</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={BUTTON} disabled={busy || valid === null}
                    onClick={() => void change(account, { enabled: true, query: valid ?? undefined })}>
                    Yes, start with {STAGE}
                  </button>
                  {phase === "off" && (
                    <button type="button" className={BUTTON} disabled={busy} onClick={() => void change(account, { declined: true })}>Not now</button>
                  )}
                </div>
              </>
            )}
            {phase !== "off" && phase !== "declined" && (
              <>
                <p>{progressText(account)}</p>
                <p className="text-xs text-muted">Search: <span className="break-all">{account.query}</span> · last {account.months} months · up to {account.max_threads || DEFAULT_BOUND} threads</p>
                <div className="flex flex-wrap gap-2">
                  {options.map((choice) => (
                    <button key={choice.extend} type="button" className={BUTTON} disabled={busy} onClick={() => void change(account, { extend: choice.extend })}>
                      {choice.label}
                    </button>
                  ))}
                  {phase === "finished" && (
                    <button type="button" className={BUTTON} disabled={busy} onClick={() => void change(account, { restart: true })}>Start again</button>
                  )}
                  <button type="button" className={BUTTON} disabled={busy} onClick={() => void change(account, { enabled: false })}>Stop</button>
                </div>
              </>
            )}
          </div>
        );
      })}
      {current && current.accounts.length > 0 && (confirmForget ? (
        <div role="group" aria-label="Confirm forgetting what was learned" className="space-y-2 rounded-field border border-line bg-inset p-2">
          <p>{forgetQuestion()}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} disabled={busy} onClick={() => void forget()}>{busy ? "Removing…" : "Yes, forget it"}</button>
            <button type="button" className={BUTTON} disabled={busy} onClick={() => setConfirmForget(false)}>Keep it</button>
          </div>
        </div>
      ) : (
        <button type="button" className={BUTTON} disabled={busy} onClick={() => { setNote(null); setConfirmForget(true); }}>Forget what it learned</button>
      ))}
    </section>
  );
}

// What the worker learned from the owner's past replies, waiting for review (BL-174). Nothing here is used until the owner
// accepts it; a policy observation is only ever a suggestion. The owner can edit the text before accepting.
export function LessonsPanel({ agentId, connectorScopes }: { agentId: number | null; connectorScopes: Scopes | null | undefined }) {
  const shown = agentId !== null && levelOf(connectorScopes, "gmail") !== "off";
  const [data, setData] = useState<Lessons | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const live = useRef(true);
  const latest = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const accept = useCallback((mine: number, raw: unknown) => {
    if (!live.current || mine !== latest.current) return;
    const parsed = parseLessons(raw);
    if (parsed) { setData(parsed); setError(null); }
    else setError("What the worker learned came back in a form this page does not read.");
  }, []);
  const reject = useCallback((mine: number, err: unknown) => {
    if (!live.current || mine !== latest.current) return;
    setError(errorText(statusOf(err), messageOf(err)));
  }, []);

  useEffect(() => {
    if (!shown || agentId === null) return;
    const mine = ++latest.current;
    api.getLessons(agentId).then((raw) => accept(mine, raw), (err) => reject(mine, err));
  }, [shown, agentId, accept, reject]);

  const reload = async () => {
    if (agentId === null) return;
    const mine = ++latest.current;
    try { accept(mine, await api.getLessons(agentId)); } catch (err) { reject(mine, err); }
  };

  const decide = async (lesson: Lesson, action: "accept" | "dismiss") => {
    if (agentId === null || busy) return;
    setBusy(lesson.id); setError(null);
    try {
      if (action === "accept") {
        const edited = edits[lesson.id];
        await api.acceptLesson(agentId, lesson.id, edited !== undefined && edited !== lesson.text ? edited : undefined);
      } else await api.dismissLesson(agentId, lesson.id);
      await reload();
    } catch (err) { if (live.current) setError(errorText(statusOf(err), messageOf(err))); }
    finally { if (live.current) setBusy(null); }
  };

  if (!shown || agentId === null) return null;
  const current = data;
  const groups = current ? groupLessons(current.pending) : [];

  return (
    <section className="space-y-2 rounded-field border border-line p-3 text-sm" aria-label="What the worker learned">
      <h3 className="font-semibold">What the worker learned</h3>
      <p className="text-xs text-muted">Short lessons from your past replies, waiting for you. Nothing is used until you accept it. {CHECK_NOTE}</p>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {!current && !error && <p className="text-muted">Loading…</p>}
      {current && current.pending.length === 0 && (
        <p className="text-muted">Nothing is waiting. Turn on learning from past replies above, then ask the worker to learn from them.</p>
      )}
      {groups.map((group) => (
        <div key={group.type} className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">{lessonLabel(group.type)}s</h4>
          {group.type === "policy" && <p className="text-xs text-muted">{POLICY_NOTE}</p>}
          <ul className="space-y-2">
            {group.items.map((lesson) => (
              <li key={lesson.id} className="space-y-1 rounded-field border border-line bg-inset p-2">
                <p className="font-medium">{lesson.pattern}</p>
                <p className="text-xs text-muted">{lessonCaption(lesson)}</p>
                <textarea aria-label={`Text of the lesson: ${lesson.pattern}`} className={INPUT} rows={3} maxLength={TEXT_MAX} disabled={busy !== null}
                  value={edits[lesson.id] ?? lesson.text} onChange={(event) => setEdits({ ...edits, [lesson.id]: event.target.value })} />
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={BUTTON} disabled={busy !== null} onClick={() => void decide(lesson, "accept")}>
                    {busy === lesson.id ? "Saving…" : acceptText(lesson)}
                  </button>
                  <button type="button" className={BUTTON} disabled={busy !== null} onClick={() => void decide(lesson, "dismiss")}>Dismiss</button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {current && current.accepted.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted">{current.accepted.length} already accepted</summary>
          <ul className="mt-1 space-y-1">
            {current.accepted.map((lesson) => (
              <li key={lesson.id} className="text-xs"><span className="font-medium">{lesson.pattern}</span> <span className="text-muted">· {destinationText(lesson)}</span></li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
