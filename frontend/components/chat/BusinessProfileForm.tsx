"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type BusinessProfile } from "@/lib/api";
import { charsLeftLabel, overLimitFields, profileDirty, profileFieldNames, profileHint } from "@/lib/business-profile";

const FIELD = "w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm placeholder:text-muted";
const SMALL =
  "cursor-pointer rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY_SMALL =
  "cursor-pointer rounded-md px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90";

// The business profile: written once, read by every worker, never edited by an agent. Never put keys or
// passwords here; the server refuses text that looks like one.
export function BusinessProfileForm({
  onSaved,
  onSkip,
  skipLabel = "Skip",
}: {
  onSaved?: (profile: BusinessProfile) => void;
  onSkip?: () => void;
  skipLabel?: string;
}) {
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [conflict, setConflict] = useState<BusinessProfile | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.businessProfile();
      setProfile(next);
      setDraft(next.fields);
    } catch {
      setError("Could not load the business profile.");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const save = async (overwrite = false) => {
    if (!profile) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.saveBusinessProfile(draft, overwrite ? null : profile.hash);
      setProfile(next);
      setDraft(next.fields);
      setConflict(null);
      setSaved(true);
      onSaved?.(next);
    } catch (err) {
      if (err instanceof ApiError && err.code === "profile_changed") {
        try {
          setConflict(await api.businessProfile());
        } catch {
          setError("The profile changed and could not be reloaded.");
        }
      } else {
        setError(err instanceof ApiError ? err.message : "Could not save the business profile.");
      }
    } finally {
      setBusy(false);
    }
  };

  if (!profile) return error ? <p role="alert" className="text-xs text-danger">{error}</p> : <p className="text-xs text-muted">Loading…</p>;

  const dirty = profileDirty(profile.fields, draft);
  const tooLong = overLimitFields(profile, draft);
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        Tell your workers about your business once. Every worker reads this as background; none can change it. Do not
        paste keys or passwords here.
      </p>
      {profileFieldNames(profile).map((name) => {
        const value = draft[name] ?? "";
        const limit = profile.limits[name] ?? 600;
        const label = profile.labels[name] ?? name;
        return (
          <label key={name} className="block space-y-1">
            <span className="flex items-baseline justify-between gap-2 text-xs font-medium">
              {label}
              <span className={`font-normal ${value.length > limit ? "text-danger" : "text-muted"}`}>{charsLeftLabel(value, limit)}</span>
            </span>
            <textarea
              value={value}
              rows={name === "sells" || name === "never_say" ? 3 : 2}
              onChange={(e) => {
                setDraft({ ...draft, [name]: e.target.value });
                setSaved(false);
              }}
              placeholder={profileHint(name)}
              className={`${FIELD} resize-y`}
            />
          </label>
        );
      })}
      {conflict && (
        <div role="alert" className="rounded-md border border-warning/60 p-3 text-xs">
          <p>The profile changed while you were editing. Load the new version, or keep yours and overwrite it.</p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={busy}
              className={SMALL}
              onClick={() => {
                setProfile(conflict);
                setDraft(conflict.fields);
                setConflict(null);
              }}
            >
              Load the new version
            </button>
            <button type="button" disabled={busy} className={SMALL} onClick={() => void save(true)}>
              Overwrite with mine
            </button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {saved && !dirty && <p role="status" className="text-xs text-muted">Saved. Workers will read it on their next run.</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy || !dirty || tooLong.length > 0} onClick={() => void save()} className={PRIMARY_SMALL}>
          {busy ? "Saving…" : "Save profile"}
        </button>
        {dirty && (
          <button type="button" disabled={busy} className={SMALL} onClick={() => setDraft(profile.fields)}>
            Discard
          </button>
        )}
        {onSkip && (
          <button type="button" disabled={busy} onClick={onSkip} className="cursor-pointer rounded-md px-3 py-1 text-xs text-muted hover:bg-hover disabled:opacity-50">
            {skipLabel}
          </button>
        )}
      </div>
    </div>
  );
}
