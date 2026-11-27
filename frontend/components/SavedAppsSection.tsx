"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SkeletonList } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { createDelayedActions } from "@/lib/delayed-actions";
import { relativeTime } from "@/lib/relative-time";
import { useT } from "@/lib/use-i18n";
import {
  appInitial, appInstallBridge, appTint, appViewPath, cleanName, deleteSavedApp, listSavedApps, renameSavedApp, type SavedApp,
} from "@/lib/saved-apps";

const UNDO_MS = 6000;
const message = (err: unknown, fallback: string) => err instanceof Error && err.message ? `${fallback} ${err.message}` : fallback;

// "Your apps": pages an agent made that were saved as apps, as launcher cards.
export function SavedAppsSection() {
  const t = useT();
  const toast = useToast();
  const [apps, setApps] = useState<SavedApp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removed, setRemoved] = useState<SavedApp[]>([]);
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null);
  const [installed, setInstalled] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [bridge] = useState(() => appInstallBridge());
  const delayed = useRef<ReturnType<typeof createDelayedActions> | null>(null);
  if (delayed.current === null) delayed.current = createDelayedActions(UNDO_MS);

  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    listSavedApps()
      .then((list) => { if (!cancelled) { setApps(list); setError(null); } })
      .catch((err) => { if (!cancelled) setError(message(err, "Could not load your apps.")); });
    return () => { cancelled = true; };
  }, [tick]);
  useEffect(() => {
    if (!bridge) return;
    bridge.list().then((ids) => setInstalled(Array.isArray(ids) ? ids : [])).catch(() => setInstalled(null));
  }, [bridge]);
  // Leaving the page makes any removal still waiting final.
  useEffect(() => { const queue = delayed.current; return () => queue?.flush(); }, []);

  const hidden = useMemo(() => new Set(removed.map((a) => a.id)), [removed]);
  const visible = (apps ?? []).filter((a) => !hidden.has(a.id));
  const when = (iso: string) => {
    const rel = relativeTime(iso);
    return rel === null ? "" : "date" in rel ? rel.date : t(rel.key, "n" in rel ? { n: rel.n } : undefined);
  };

  function remove(app: SavedApp) {
    setRemoved((list) => [...list, app]);
    delayed.current?.start(app.id, () => {
      void (async () => {
        try {
          await deleteSavedApp(app.id);
          if (bridge && installed?.includes(app.id)) { await bridge.uninstall(app.id).catch(() => undefined); setInstalled((ids) => ids?.filter((i) => i !== app.id) ?? ids); }
          setApps((list) => list?.filter((a) => a.id !== app.id) ?? list);
        } catch (err) { toast(message(err, `Could not remove ${app.name}.`), "danger"); }
        finally { setRemoved((list) => list.filter((a) => a.id !== app.id)); }
      })();
    });
  }
  function undo(app: SavedApp) {
    if (delayed.current?.undo(app.id)) setRemoved((list) => list.filter((a) => a.id !== app.id));
  }
  async function saveName(app: SavedApp) {
    const name = renaming ? cleanName(renaming.value) : null;
    setRenaming(null);
    if (!name || name === app.name) return;
    try { const next = await renameSavedApp(app.id, name); setApps((list) => list?.map((a) => a.id === app.id ? { ...a, ...next } : a) ?? list); }
    catch (err) { toast(message(err, "Could not rename the app."), "danger"); }
  }
  async function toggleInstall(app: SavedApp) {
    if (!bridge) return;
    const on = installed?.includes(app.id) ?? false;
    setBusy(app.id);
    try {
      if (on) { await bridge.uninstall(app.id); setInstalled((ids) => (ids ?? []).filter((i) => i !== app.id)); }
      else { await bridge.install({ id: app.id, name: app.name }); setInstalled((ids) => [...(ids ?? []), app.id]); }
    } catch (err) { toast(message(err, on ? "Could not uninstall." : "Could not install."), "danger"); }
    finally { setBusy(null); }
  }

  return (
    <section aria-labelledby="your-apps" className="space-y-3">
      <h2 id="your-apps" className="text-xl font-semibold">Your apps</h2>
      {error && <Banner tone="danger" action={<Button type="button" onClick={() => setTick((n) => n + 1)}>Try again</Button>}>{error}</Banner>}
      {apps === null && !error && <SkeletonList rows={2} />}
      {removed.map((app) => (
        <Banner key={app.id} action={<Button size="sm" type="button" onClick={() => undo(app)}>Undo</Button>}>Removed {app.name}.</Banner>
      ))}
      {apps !== null && visible.length === 0 && removed.length === 0 && (
        <p className="text-sm text-muted">Apps you save from a chat show up here.</p>
      )}
      {visible.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2">
          {visible.map((app) => {
            const on = installed?.includes(app.id) ?? false;
            return (
              <li key={app.id} className="flex flex-col gap-3 rounded-lg border border-line p-4">
                <div className="flex items-center gap-3">
                  <span aria-hidden="true" className="flex size-9 shrink-0 items-center justify-center rounded-md text-base font-semibold" style={appTint(app.id)}>{appInitial(app.name)}</span>
                  <div className="min-w-0 flex-1">
                    {renaming?.id === app.id ? (
                      <form onSubmit={(e) => { e.preventDefault(); void saveName(app); }}>
                        <Input autoFocus aria-label="App name" value={renaming.value} maxLength={80}
                          onChange={(e) => setRenaming({ id: app.id, value: e.target.value })}
                          onBlur={() => void saveName(app)}
                          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); setRenaming(null); } }} />
                      </form>
                    ) : <p className="truncate text-sm font-medium">{app.name}</p>}
                    <p className="truncate text-xs text-muted">{[app.agent_name, when(app.updated_at)].filter(Boolean).join(" · ")}</p>
                  </div>
                  {on && <span className="text-xs text-muted">Installed</span>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Link href={appViewPath(app.id)} className="inline-flex h-8 items-center rounded-field bg-accent px-3 text-[13px] font-medium text-accent-fg hover:opacity-90 touch:h-11">Open</Link>
                  <Button size="sm" type="button" onClick={() => setRenaming({ id: app.id, value: app.name })}>Rename</Button>
                  {bridge && <Button size="sm" type="button" loading={busy === app.id} onClick={() => void toggleInstall(app)}>{on ? "Uninstall" : "Install"}</Button>}
                  <Button size="sm" type="button" variant="ghost" className="ml-auto" onClick={() => remove(app)}>Remove</Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
