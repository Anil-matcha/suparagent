"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type ProfileProposal } from "@/lib/api";
import { pendingProposals, proposalBefore, proposalTitle } from "@/lib/business-profile";

const SMALL =
  "cursor-pointer rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY_SMALL =
  "cursor-pointer rounded-md px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90";

// Changes workers suggested to the business profile. Nothing changes until the person accepts one; a worker can only
// add a suggestion here, never edit the profile.
export function ProfileProposals({
  onCount,
  onApplied,
}: {
  onCount?: (count: number) => void;
  onApplied?: () => void;
}) {
  const [items, setItems] = useState<ProfileProposal[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState<{ id: string; current: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const next = pendingProposals((await api.profileProposals()).items);
      setItems(next);
      onCount?.(next.length);
    } catch {
      setError("Could not load suggested changes.");
    }
  }, [onCount]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const accept = async (item: ProfileProposal, overwrite = false) => {
    setBusy(item.id);
    setError(null);
    try {
      await api.acceptProfileProposal(item.id, overwrite);
      setStale(null);
      onApplied?.();
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "proposal_stale") {
        setStale({ id: item.id, current: (await currentText(item.field)).trim() || "(empty)" });
      } else {
        setError(err instanceof ApiError ? err.message : "Could not apply that suggestion.");
        await load();
      }
    } finally {
      setBusy(null);
    }
  };

  const dismiss = async (item: ProfileProposal) => {
    setBusy(item.id);
    setError(null);
    try {
      await api.dismissProfileProposal(item.id);
      setStale(null);
      await load();
    } catch {
      setError("Could not dismiss that suggestion.");
      await load();
    } finally {
      setBusy(null);
    }
  };

  if (error && !items) return <p role="alert" className="text-xs text-danger">{error}</p>;
  if (!items || items.length === 0) return null;
  return (
    <section aria-label="Suggested changes" className="space-y-2 rounded-md border border-line p-3">
      <h3 className="text-xs font-semibold">Suggested changes ({items.length})</h3>
      <p className="text-xs text-muted">Workers can suggest changes. Nothing changes until you accept one.</p>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      <ul className="space-y-3">
        {items.map((item) => (
          <li key={item.id} className="space-y-1 text-xs">
            <p className="font-medium">{proposalTitle(item)}</p>
            {item.reason && <p className="text-muted">Why: {item.reason}</p>}
            <p className="whitespace-pre-wrap text-muted">When suggested: {proposalBefore(item)}</p>
            <p className="whitespace-pre-wrap">New: {item.text}</p>
            {stale?.id === item.id && (
              <p role="alert" className="text-warning">
                That field changed after this was suggested (it now says: {stale.current}). Accept again to replace it,
                or dismiss.
              </p>
            )}
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                disabled={busy === item.id}
                className={PRIMARY_SMALL}
                onClick={() => void accept(item, stale?.id === item.id)}
              >
                {stale?.id === item.id ? "Replace anyway" : "Accept"}
              </button>
              <button type="button" disabled={busy === item.id} className={SMALL} onClick={() => void dismiss(item)}>
                Dismiss
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

async function currentText(field: string): Promise<string> {
  try {
    return (await api.businessProfile()).fields[field] ?? "";
  } catch {
    return "";
  }
}
