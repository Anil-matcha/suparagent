"use client";

import Link from "next/link";
import { useState } from "react";
import {
  SHARE_PRESETS, agentAppOptions, levelOf, shareOf, supportsShare, validPercent, withAccount, withApp, withShare,
  type AccountScopes, type ComposioAccount, type Level, type Scopes, type Shares,
} from "@/lib/connected-apps";

const SEGMENT = "inline-flex min-h-8 cursor-pointer items-center rounded border border-transparent px-2.5 text-xs font-medium text-muted hover:bg-hover hover:text-ink peer-checked:border-accent peer-checked:bg-accent/15 peer-checked:text-accent peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent touch:min-h-11";

// How much of one mailbox this agent may see. A fixed share of threads, chosen by the server, so raising it only adds
// threads. Presets plus a number; the number keeps what is being typed until it is a valid whole percent.
function ShareControl({ id, label, percent, busy, onChange }: { id: string; label: string; percent: number; busy: boolean; onChange: (percent: number) => void }) {
  // What is being typed, or null to show the saved percent. A preset or leaving the field clears it.
  const [typed, setTyped] = useState<string | null>(null);
  const text = typed ?? String(percent);
  const commit = (value: string) => {
    setTyped(value);
    const parsed = Number(value);
    if (value.trim() !== "" && validPercent(parsed)) onChange(parsed);
  };
  return (
    <fieldset className="ml-6 space-y-1 border-l border-line pl-3" aria-label={`Share of ${label} this agent may see`}>
      <legend className="text-xs font-medium text-ink">Share of mail this agent may see</legend>
      <div role="radiogroup" aria-label="Share presets" className="inline-flex max-w-full flex-wrap items-center gap-1 rounded-md border border-line bg-inset p-1">
        {SHARE_PRESETS.map((preset) => (
          <label key={preset} className="relative">
            <input type="radio" name={`share-${id}`} className="peer sr-only" disabled={busy} checked={percent === preset} onChange={() => { setTyped(null); onChange(preset); }} />
            <span className={SEGMENT}>{preset === 100 ? "All mail" : `${preset}%`}</span>
          </label>
        ))}
        <label className="ml-1 flex items-center gap-1 text-xs text-muted">
          Other
          <input
            type="number" min={1} max={100} step={1} inputMode="numeric" disabled={busy} value={text}
            aria-label="Custom percent of mail" aria-invalid={text.trim() !== "" && !validPercent(Number(text))}
            onChange={(event) => commit(event.target.value)}
            onBlur={() => setTyped(null)}
            className="h-8 w-16 rounded border border-line bg-surface px-2 text-xs text-ink touch:h-11"
          />
          %
        </label>
      </div>
      <p className="text-xs leading-5 text-muted">A fixed share of threads. Raising it only adds threads; the rest is never shown to this agent.</p>
      <p className="text-xs leading-5 text-muted">Lowering it hides threads from now on but cannot erase what the agent already noted.</p>
      {percent < 100 && <p className="text-xs leading-5 text-muted">While this is below All mail, this agent cannot send email from this account. Drafts still work, and you send them yourself.</p>}
    </fieldset>
  );
}

export function ConnectedAppsSection({
  appAccounts,
  connectorScopes,
  connectorAccountScopes,
  connectorShares,
  busy,
  onScopesChange,
  onAccountScopesChange,
  onSharesChange,
}: {
  appAccounts: ComposioAccount[] | null;
  connectorScopes: Scopes;
  connectorAccountScopes: AccountScopes;
  connectorShares: Shares;
  busy: boolean;
  onScopesChange: (scopes: Scopes) => void;
  onAccountScopesChange: (scopes: AccountScopes) => void;
  onSharesChange: (shares: Shares) => void;
}) {
  const apps = agentAppOptions(appAccounts, connectorScopes);

  return (
    <div className="space-y-4" role="group" aria-label="Connected apps access">
      {apps.map((app) => {
        const level = levelOf(connectorScopes, app.slug);
        const options: { value: Level; label: string }[] = [
          { value: "off", label: "Off" },
          { value: "read", label: "Read only" },
          ...(app.labels ? [{ value: "labels" as const, label: "Read and label mail under Worker/" }] : []),
          ...(app.write ? [{ value: "write" as const, label: "Read and change" }] : []),
        ];
        const helper = level === "off"
          ? "This agent cannot use this app."
          : level === "read"
            ? `${app.generic ? "Look up only." : "Read only."} ${app.read}`
            : level === "labels"
              ? `Read and label. ${app.labels ?? ""}`
              : `${app.generic ? "Look up and run." : "Read and change."} ${app.write ?? ""}`;

        const accounts = appAccounts?.filter((account) => account.toolkit === app.slug && account.status === "ACTIVE") ?? [];
        const selected = connectorAccountScopes[app.slug] ?? [];
        const unavailable = selected.filter((id) => !accounts.some((account) => account.id === id));

        return (
          <fieldset key={app.slug} className="min-w-0 space-y-2 rounded-field border border-line p-3">
            <legend className="px-1 text-sm font-medium text-ink">
              {app.label}
              {app.generic && <span className="ml-2 text-xs font-normal text-muted">No built-in tools; every action asks you first.</span>}
            </legend>
            <div role="radiogroup" aria-label={`${app.label} access`} className="inline-flex max-w-full flex-wrap gap-1 rounded-md border border-line bg-inset p-1">
              {options.map((option) => (
                <label key={option.value} className="relative">
                  <input
                    type="radio"
                    name={`app-${app.slug}`}
                    className="peer sr-only"
                    disabled={busy}
                    checked={level === option.value}
                    onChange={() => onScopesChange(withApp(connectorScopes, app.slug, option.value))}
                  />
                  <span className={SEGMENT}>{option.label}</span>
                </label>
              ))}
            </div>
            <p className="text-xs leading-5 text-muted" aria-live="polite">{helper}</p>
            {level === "labels" && <p className="text-xs leading-5 text-muted">If this is a shared inbox, everyone who can open it will see these labels.</p>}

            {level !== "off" && (
              <div className="space-y-1 border-l border-line pl-3">
                <p className="text-xs text-muted">Allow this agent to use these connected accounts:</p>
                {appAccounts === null ? (
                  <p className="text-xs text-muted">Loading connected accounts…</p>
                ) : (
                  <>
                    {accounts.length ? accounts.map((account) => (
                      <div key={account.id} className="space-y-1">
                        <label className="flex min-h-8 cursor-pointer items-center gap-2 text-xs touch:min-h-11">
                          <input
                            type="checkbox"
                            disabled={busy}
                            checked={selected.includes(account.id)}
                            onChange={(event) => onAccountScopesChange(withAccount(connectorAccountScopes, app.slug, account.id, event.target.checked))}
                          />
                          <span>{account.alias || account.id}</span>
                        </label>
                        {supportsShare(app.slug) && selected.includes(account.id) && (
                          <ShareControl
                            id={account.id}
                            label={account.alias || account.id}
                            percent={shareOf(connectorShares, app.slug, account.id)}
                            busy={busy}
                            onChange={(percent) => onSharesChange(withShare(connectorShares, app.slug, account.id, percent))}
                          />
                        )}
                      </div>
                    )) : (
                      <p className="text-xs text-muted">No connected account is available. Connect one on the <Link href="/apps/" className="text-accent underline underline-offset-2">Apps page</Link>.</p>
                    )}
                    {unavailable.map((id) => (
                      <label key={id} className="flex min-h-8 cursor-pointer items-center gap-2 text-xs touch:min-h-11">
                        <input
                          type="checkbox"
                          disabled={busy}
                          checked
                          onChange={() => onAccountScopesChange(withAccount(connectorAccountScopes, app.slug, id, false))}
                        />
                        <span>Unavailable account ({id}) · remove access</span>
                      </label>
                    ))}
                    {selected.length === 0 && <p className="text-xs text-muted">No accounts selected; this app is unavailable to the agent.</p>}
                  </>
                )}
              </div>
            )}
          </fieldset>
        );
      })}
      <p className="text-xs leading-5 text-muted">
        <Link href="/apps/" className="text-accent underline underline-offset-2">Connect apps first on the Apps page</Link>, then choose which exact accounts this agent can use. Access is used only in conversations you start, never in routines or from outside messages.
      </p>
    </div>
  );
}
