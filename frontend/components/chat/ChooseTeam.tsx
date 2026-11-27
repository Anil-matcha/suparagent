"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { collaboration } from "@/lib/collaboration";
import { loadDraft, saveDraft } from "@/lib/drafts";
import { BusinessProfileForm } from "./BusinessProfileForm";
import { WorkerGrants } from "./WorkerGrants";
import { EmptyPanel, ErrorState, LoadingState } from "./PanelState";
import {
  addedTemplateIds,
  categoryLabel,
  featuredShortlist,
  groupByCategory,
  hireableChoices,
  hireSummary,
  LEAD_INTERVIEW_PROMPT,
  NO_LEAD_HELP_NOTE,
  leadAgentId,
  MAX_HIRE,
  newSuggestionSince,
  preTick,
  requirementBadges,
  SETUP_DEPTH_LABELS,
  starterRoutinesLabel,
  suggestionReasons,
  toggleChoice,
  type HireReceipt,
  type LatestSuggestion,
  type SuggestGoal,
  type TemplateCatalog,
} from "@/lib/templates";

const TAG = "max-w-full break-words rounded border border-line px-1.5 py-0.5 text-[11px] text-muted";

// Pick any number of ready-made workers and add them in one step. Nothing is created until
// Add is pressed; workers ask first, hold no connected-app access and their routines stay paused.
export function ChooseTeam({
  provider,
  model,
  skipLabel = "Skip",
  shortlist = false,
  leadHelp = true,
  onSkip,
  onHired,
  onUndone,
  onTickedChange,
}: {
  provider?: string;
  model?: string | null;
  skipLabel?: string;
  /** First-run: show only a short list of workers, without category headings. The rest stay in Templates. */
  shortlist?: boolean;
  /** False when no team lead can answer (lead box off or engine without team tools): hides the lead chat and says why. */
  leadHelp?: boolean;
  onSkip?: () => void;
  onHired?: (receipt: HireReceipt) => void;
  onUndone?: () => void;
  /** How many workers are ticked but not yet added; lets a parent warn before leaving the screen. */
  onTickedChange?: (count: number) => void;
}) {
  const [catalog, setCatalog] = useState<TemplateCatalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<HireReceipt | null>(null);
  const [undone, setUndone] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [goals, setGoals] = useState<SuggestGoal[]>([]);
  const [pickedGoals, setPickedGoals] = useState<string[]>([]);
  const [about, setAbout] = useState("");
  const [quickOnly, setQuickOnly] = useState(false);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [helpNote, setHelpNote] = useState<string | null>(null);
  const [profileDone, setProfileDone] = useState(false);
  // "Chat with your team lead instead": the baseline is the newest suggestion that already existed when the
  // chat was opened, so only a suggestion made after that is offered.
  const [chatBaseline, setChatBaseline] = useState<number | null>(null);
  const [chatNote, setChatNote] = useState<string | null>(null);
  const [leadSuggestion, setLeadSuggestion] = useState<LatestSuggestion | null>(null);
  // Workers an earlier hire already made, and the ones the person chose to add another copy of.
  const [added, setAdded] = useState<string[]>([]);
  const [copies, setCopies] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .listTemplates()
      .then((value) => {
        if (cancelled) return;
        setCatalog(value);
        // If the agent list cannot be read, every worker stays offered, as before.
        api.listAgents().then((agents) => { if (!cancelled) setAdded(addedTemplateIds(agents, value.templates)); }).catch(() => {});
      })
      .catch(() => { if (!cancelled) setLoadError("Could not load the workers."); });
    return () => { cancelled = true; };
  }, [reload]);

  const groups = useMemo(() => {
    if (!catalog) return [];
    if (shortlist) return [{ category: "", templates: featuredShortlist(catalog.templates, chosen) }];
    return groupByCategory(catalog.templates, catalog.categories);
  }, [catalog, shortlist, chosen]);
  const ticked = useMemo(
    () => hireableChoices(chosen, catalog?.templates ?? []).filter((id) => !added.includes(id) || copies.includes(id)),
    [chosen, catalog, added, copies],
  );

  useEffect(() => { onTickedChange?.(ticked.length); }, [ticked.length, onTickedChange]);

  // "Help me choose": three short questions, answered by the bundled catalog. It only ticks cards for
  // the person to review; nothing is created or granted until Add is pressed.
  const openHelp = () => {
    setHelpOpen(true);
    if (goals.length === 0) api.suggestGoals().then(setGoals).catch(() => setHelpNote("Could not load the questions."));
  };

  const suggest = async () => {
    setBusy(true);
    setHelpNote(null);
    try {
      const { suggestions } = await api.suggestTemplates(pickedGoals, about.trim(), quickOnly ? "express" : "guided");
      const ids = preTick(suggestions, catalog?.templates ?? []);
      setChosen(ids);
      setReasons(suggestionReasons(suggestions.filter((s) => ids.includes(s.template_id))));
      setHelpNote(
        ids.length === 0
          ? "Nothing matched yet. Try another goal or describe the business."
          : `I ticked ${ids.length}. Change them as you like, then add.`,
      );
    } catch {
      setHelpNote("Could not get suggestions. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  };

  // Opens a chat with the team lead in a new tab with a short interview already typed in. When the lead
  // suggests workers, this screen offers "Use these suggestions". Nothing is created or granted by chatting.
  const openLeadChat = async () => {
    setChatNote(null);
    try {
      const lead = leadAgentId(await collaboration.team());
      if (lead === null) {
        setChatNote("No team lead is set up yet. Use the quick questions, or set a lead in Team first.");
        return;
      }
      const latest = await api.latestWorkerSuggestion().catch(() => null);
      setChatBaseline(latest?.created_at ?? Date.now() / 1000);
      setLeadSuggestion(null);
      // Keep an unsent message the person already typed; otherwise start the interview for them.
      const keptDraft = loadDraft("new").trim() !== "";
      if (!keptDraft) saveDraft("new", LEAD_INTERVIEW_PROMPT);
      window.open(`/chat/?agent=${lead}`, "_blank", "noopener");
      setChatNote(
        keptDraft
          ? "Opened your team lead in a new tab. Ask it to suggest ready-made workers; this page will offer them here."
          : "Opened your team lead in a new tab with a short interview typed in. Send it; this page will offer the workers it suggests.",
      );
    } catch {
      setChatNote("Could not open the team lead. Nothing was changed.");
    }
  };

  useEffect(() => {
    if (chatBaseline === null || leadSuggestion !== null || receipt !== null) return;
    let stopped = false;
    const check = () =>
      api.latestWorkerSuggestion().then((latest) => {
        const fresh = newSuggestionSince(latest, chatBaseline);
        if (!stopped && fresh) setLeadSuggestion(fresh);
      }, () => {});
    const timer = window.setInterval(() => void check(), 3000);
    const giveUp = window.setTimeout(() => window.clearInterval(timer), 15 * 60 * 1000);
    return () => { stopped = true; window.clearInterval(timer); window.clearTimeout(giveUp); };
  }, [chatBaseline, leadSuggestion, receipt]);

  const useLeadSuggestion = () => {
    if (!leadSuggestion) return;
    const ids = preTick(leadSuggestion.suggestions, catalog?.templates ?? []);
    setChosen(ids);
    setReasons(suggestionReasons(leadSuggestion.suggestions.filter((s) => ids.includes(s.template_id))));
    setHelpNote(ids.length === 0 ? "None of those can be added right now." : `I ticked ${ids.length}. Change them as you like, then add.`);
    setLeadSuggestion(null);
    setChatBaseline(null);
  };

  const hire = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.hireTemplates(ticked, provider, model);
      setReceipt(result);
      setAdded((now) => [...new Set([...now, ...result.workers.map((w) => w.template_id)])]);
      setCopies([]);
      setUndone(false);
      setProfileDone(false);
      onHired?.(result);
    } catch (err) {
      // One message for the whole hire: nothing was added.
      setError(err instanceof ApiError ? err.message : "The workers could not be added. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  };

  const undo = async () => {
    if (!receipt) return;
    setBusy(true);
    setError(null);
    try {
      await api.undoHire(receipt.workers.map((w) => w.agent_id));
      setUndone(true);
      setAdded((now) => now.filter((id) => !receipt.workers.some((w) => w.template_id === id)));
      setReceipt(null);
      setChosen([]);
      onUndone?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo. The workers were left in place.");
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <ErrorState message={loadError} onRetry={() => { setLoadError(null); setReload((n) => n + 1); }} />;
  if (!catalog) return <LoadingState rows={4} />;

  if (receipt) {
    return (
      <div className="space-y-3 text-sm" role="status">
        <p>{hireSummary(receipt)}</p>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        <button type="button" disabled={busy} onClick={() => void undo()} className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover disabled:opacity-50">
          {busy ? "Undoing…" : "Undo"}
        </button>
        <p className="text-xs text-muted">Undo works while a worker is unchanged and has not been used.</p>
        <WorkerGrants
          workers={receipt.workers}
          suggestedTools={Object.fromEntries(catalog.templates.map((t) => [t.id, t.suggested_tools]))}
        />
        {!profileDone && (
          <section aria-label="Business profile" className="space-y-2 border-t border-line pt-3">
            <h4 className="text-xs font-medium uppercase tracking-wide text-muted">Tell your workers about your business</h4>
            <BusinessProfileForm skipLabel="Later" onSkip={() => setProfileDone(true)} onSaved={() => setProfileDone(true)} />
          </section>
        )}
      </div>
    );
  }

  if (catalog.templates.length === 0) return <EmptyPanel title="No workers yet" description="Check back after an update." />;

  return (
    <div className="space-y-4">
      {undone && <p role="status" className="text-xs text-muted">Those workers were removed.</p>}
      <p className="text-sm text-muted">Tick any workers you want, or none. You can add more later from Templates.{shortlist ? " Only a few are shown here." : ""}</p>
      {leadSuggestion && (
        <section aria-label="Your team lead's suggestions" className="space-y-2 rounded-md border border-accent p-3">
          <p className="text-xs font-medium">Your team lead suggested {leadSuggestion.suggestions.length} {leadSuggestion.suggestions.length === 1 ? "worker" : "workers"}:</p>
          <ul className="space-y-1 text-xs">
            {leadSuggestion.suggestions.map((s) => (
              <li key={s.template_id}>
                <strong>{catalog?.templates.find((t) => t.id === s.template_id)?.name ?? s.template_id}</strong>
                {s.reason ? <span className="text-muted"> — {s.reason}</span> : null}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={useLeadSuggestion} className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover">
              Use these suggestions
            </button>
            <button type="button" onClick={() => { setLeadSuggestion(null); setChatBaseline(null); }} className="cursor-pointer rounded-md px-3 py-1 text-xs text-muted hover:bg-hover">
              Dismiss
            </button>
            <span className="text-xs text-muted">Nothing is added until you press Add.</span>
          </div>
        </section>
      )}
      {!helpOpen ? (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={openHelp} className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover">
            Help me choose
          </button>
          {leadHelp && (
<button type="button" onClick={() => void openLeadChat()} className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover">
Chat with your team lead instead
</button>
)}
          {!leadHelp && <span className="min-w-0 break-words text-xs text-muted">{NO_LEAD_HELP_NOTE}</span>}
          {chatNote && <span role="status" className="min-w-0 break-words text-xs text-muted">{chatNote}</span>}
        </div>
      ) : (
        <section aria-label="Help me choose" className="space-y-3 rounded-md border border-line p-3">
          <fieldset className="space-y-1">
            <legend className="text-xs font-medium">What do you want help with?</legend>
            {goals.map((goal) => (
              <label key={goal.key} className="flex cursor-pointer items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={pickedGoals.includes(goal.key)}
                  onChange={() => setPickedGoals((now) => (now.includes(goal.key) ? now.filter((k) => k !== goal.key) : [...now, goal.key]))}
                />
                <span>{goal.label}</span>
              </label>
            ))}
          </fieldset>
          <label className="block space-y-1 text-xs">
            <span className="font-medium">What does your business do? (optional)</span>
            <input
              type="text"
              value={about}
              maxLength={500}
              onChange={(event) => setAbout(event.target.value)}
              className="w-full rounded-md border border-line bg-transparent px-2 py-1 text-xs"
            />
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-xs">
            <input type="checkbox" checked={quickOnly} onChange={() => setQuickOnly((v) => !v)} />
            <span>Only workers that start with no questions</span>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || (pickedGoals.length === 0 && about.trim() === "")}
              onClick={() => void suggest()}
              className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              Suggest workers
            </button>
            {leadHelp && (
<button type="button" onClick={() => void openLeadChat()} className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover">
Chat with your team lead instead
</button>
)}
            <button type="button" onClick={() => setHelpOpen(false)} className="cursor-pointer rounded-md px-3 py-1 text-xs text-muted hover:bg-hover">
              Close
            </button>
            {chatNote && <span role="status" className="min-w-0 break-words text-xs text-muted">{chatNote}</span>}
            {helpNote && <span role="status" className="text-xs text-muted">{helpNote}</span>}
          </div>
        </section>
      )}
      {groups.map((group) => (
        <section key={group.category || "shortlist"} aria-label={group.category ? categoryLabel(group.category) : "Suggested workers"} className="space-y-2">
          {group.category && <h4 className="text-xs font-medium uppercase tracking-wide text-muted">{categoryLabel(group.category)}</h4>}
          <ul className="grid min-w-0 gap-3 sm:grid-cols-2">
            {group.templates.map((template) => {
              const isTicked = ticked.includes(template.id);
              const atLimit = !isTicked && ticked.length >= MAX_HIRE;
              const alreadyAdded = added.includes(template.id) && !copies.includes(template.id);
              return (
                <li key={template.id} className={`min-w-0 break-words rounded-md border p-3 ${isTicked ? "border-accent bg-inset" : "border-line"}`}>
                  <label className={`flex items-start gap-2 ${template.available && !atLimit && !alreadyAdded ? "cursor-pointer" : "cursor-not-allowed opacity-60"}`}>
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={isTicked}
                      disabled={busy || !template.available || atLimit || alreadyAdded}
                      onChange={() => setChosen(toggleChoice(ticked, template.id))}
                      aria-label={`Choose ${template.name}`}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">{template.name}</span>
                      <span className="block text-xs text-muted">{template.role}</span>
                      <span className="mt-1 block text-xs">{template.description}</span>
                      {isTicked && reasons[template.id] && <span className="mt-1 block text-xs text-muted">{reasons[template.id]}</span>}
                    </span>
                  </label>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {requirementBadges(template).map((badge) =>
                      badge.connectHref ? (
                        <Link key={badge.requirement} href={badge.connectHref} className={`${TAG} underline hover:bg-hover`}>{badge.text}</Link>
                      ) : (
                        <span key={badge.requirement} className={TAG}>{badge.text}</span>
                      ),
                    )}
                    {added.includes(template.id) && <span className={TAG}>Already added</span>}
                    {alreadyAdded && (
                      <button type="button" disabled={busy} onClick={() => setCopies((now) => [...now, template.id])} className={`${TAG} cursor-pointer underline hover:bg-hover`}>
                        Add another copy
                      </button>
                    )}
                    {template.routines.length > 0 && <span className={TAG}>{SETUP_DEPTH_LABELS[template.setup_depth]}</span>}
                    {starterRoutinesLabel(template.routines) && <span className={TAG}>{starterRoutinesLabel(template.routines)}</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || ticked.length === 0}
          onClick={() => void hire()}
          className="cursor-pointer rounded-md border border-line px-3 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Adding…" : ticked.length === 0 ? "Add workers" : `Add ${ticked.length} worker${ticked.length === 1 ? "" : "s"}`}
        </button>
        {onSkip && (
          <button type="button" disabled={busy} onClick={onSkip} className="cursor-pointer rounded-md px-3 py-1 text-xs text-muted hover:bg-hover disabled:opacity-50">
            {skipLabel}
          </button>
        )}
        <span className="text-xs text-muted">{ticked.length} of up to {MAX_HIRE} chosen</span>
      </div>
    </div>
  );
}
