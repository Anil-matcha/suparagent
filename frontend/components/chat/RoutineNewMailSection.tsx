"use client";

import { Select } from "@/components/ui/Select";
import type { Agent, ConnectedAppGrant } from "@/lib/api";
import {
  EXPLANATION, QUERY_MAX, appEventProblem, gmailChoices, grantedGmailAccount, lastEventText, readiness, stateLabel, todayLines,
  type AppEventDraft, type AppEventStatus,
} from "@/lib/app-event";
import type { ComposioAccount } from "@/lib/connected-apps";

const input = "w-full rounded-md border border-line bg-transparent px-2 py-1.5 text-sm";
const STATE_TONE: Record<AppEventStatus["state"], string> = { active: "text-accent", paused: "text-muted", error: "text-danger", removed: "text-muted" };

// The routine editor's "when new mail arrives" part. It sits beside the schedule: the routine still runs on its schedule.
// Nothing is polled; the mail provider reports new mail and the server decides whether this computer can receive the report.
export function RoutineNewMailSection({ value, onChange, agent, accounts, grants, uninterruptedAccess, timezone, status, disabled = false }: {
  value: AppEventDraft;
  onChange: (next: AppEventDraft) => void;
  agent: Agent | undefined;
  accounts: ComposioAccount[] | null;
  grants: ConnectedAppGrant[];
  uninterruptedAccess: boolean;
  timezone: string;
  status: AppEventStatus | null;
  disabled?: boolean;
}) {
  const choices = gmailChoices(agent, accounts);
  const problem = appEventProblem(value);
  const cannotReceive = status !== null && !status.can_receive;
  // A new trigger cannot be switched on where events cannot arrive; one that exists can always be switched off.
  const toggleBlocked = disabled || !agent || (cannotReceive && !value.enabled);
  const known = choices.some((account) => account.id === value.accountId);

  const toggle = (enabled: boolean) => {
    if (!enabled) return onChange({ ...value, enabled: false });
    const bound = grantedGmailAccount(grants);
    const first = choices.find((account) => account.id === bound)?.id ?? choices[0]?.id ?? "";
    onChange({ ...value, enabled: true, accountId: value.accountId || first });
  };
  const checks = value.enabled ? readiness({ accountId: value.accountId, choices, grants, uninterruptedAccess, status }) : [];

  return (
    <section className="space-y-2 rounded-md border border-line p-3" aria-labelledby="routine-new-mail-title">
      <div>
        <h3 id="routine-new-mail-title" className="text-sm font-medium">When new mail arrives</h3>
        <p className="mt-1 text-xs text-muted">{EXPLANATION}</p>
      </div>

      <label className="flex items-start gap-2 text-xs">
        <input type="checkbox" checked={value.enabled} disabled={toggleBlocked} onChange={(event) => toggle(event.target.checked)} />
        <span>
          Also run this routine when new mail arrives. It also runs on its schedule.
          {!agent && <span className="text-muted"> Choose an agent first.</span>}
          {agent && !value.enabled && choices.length === 0 && accounts !== null && <span className="text-muted"> This agent has no Gmail account it may read yet.</span>}
          {cannotReceive && !value.enabled && <span className="text-warning"> {status?.reason || "This computer cannot receive new-mail reports yet."}</span>}
        </span>
      </label>

      {accounts === null && value.enabled && <p role="status" className="text-xs text-muted">Loading connected accounts…</p>}

      {value.enabled && (
        <div className="space-y-2">
          <label className="block text-xs text-muted">Gmail account
            <Select aria-label="Gmail account for new mail" value={value.accountId} disabled={disabled} onChange={(event) => onChange({ ...value, accountId: event.target.value })} className={input}>
              {choices.length === 0 && !value.accountId && <option value="">No account allowed for this agent</option>}
              {choices.map((account) => <option key={account.id} value={account.id}>{account.alias ?? account.id}</option>)}
              {value.accountId && !known && <option value={value.accountId}>Unavailable account ({value.accountId})</option>}
            </Select>
          </label>

          <label className="block text-xs text-muted">Only mail matching this Gmail search or label
            <input className={input} maxLength={QUERY_MAX} value={value.query} disabled={disabled} placeholder="label:support" onChange={(event) => onChange({ ...value, query: event.target.value })} />
            <span className="mt-1 block">For example label:support. Leave it empty to react to all new mail in the inbox.</span>
          </label>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <label>Wait (minutes) so several emails make one run
              <input type="number" min={1} max={60} className={input} value={value.debounce} disabled={disabled} onChange={(event) => onChange({ ...value, debounce: event.target.value })} />
            </label>
            <label>At most this many runs a day
              <input type="number" min={1} max={48} className={input} value={value.maxRuns} disabled={disabled} onChange={(event) => onChange({ ...value, maxRuns: event.target.value })} />
            </label>
          </div>

          <div className="space-y-1 text-xs">
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={value.quietEnabled} disabled={disabled} onChange={(event) => onChange({ ...value, quietEnabled: event.target.checked })} />
              <span>Quiet hours: mail that arrives then waits for the morning</span>
            </label>
            {value.quietEnabled && (
              <div className="grid grid-cols-2 gap-2">
                <label className="text-muted">From
                  <input type="time" className={input} value={value.quietFrom} disabled={disabled} onChange={(event) => onChange({ ...value, quietFrom: event.target.value })} />
                </label>
                <label className="text-muted">Until
                  <input type="time" className={input} value={value.quietTo} disabled={disabled} onChange={(event) => onChange({ ...value, quietTo: event.target.value })} />
                </label>
                <p className="col-span-2 text-muted">Times are in this routine&apos;s time zone ({timezone || "UTC"}).</p>
              </div>
            )}
          </div>

          {problem && <p role="alert" className="text-xs text-danger">{problem}</p>}

          <ul className="space-y-1 text-xs" aria-label="Readiness">
            {checks.map((check) => (
              <li key={check.key} className={check.ok === true ? "text-muted" : check.ok === false ? "text-warning" : "text-muted"}>
                <span aria-hidden="true">{check.ok === true ? "✓ " : check.ok === false ? "! " : "• "}</span>{check.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {status && (
        <div className="space-y-1 border-t border-line pt-2 text-xs" aria-label="New-mail trigger status">
          <p><span className={`font-medium ${STATE_TONE[status.state]}`}>{stateLabel(status.state)}</span> · <span className="text-muted">{lastEventText(status.last_event_at)}</span></p>
          {todayLines(status.today).length > 0
            ? <ul className="list-disc space-y-0.5 pl-4 text-muted">{todayLines(status.today).map((line) => <li key={line}>Today: {line}</li>)}</ul>
            : <p className="text-muted">Nothing reported today.</p>}
          {status.last_error && <p role="alert" className="text-danger">{status.last_error}</p>}
        </div>
      )}
    </section>
  );
}
