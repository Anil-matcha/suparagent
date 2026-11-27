"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, MessageSquare } from "lucide-react";
import { UsesChips } from "@/components/chat/UsesChips";
import { PageFrame } from "@/components/chat/PageFrame";
import type { RpcRequest } from "@/lib/page-bridge";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { useToast } from "@/components/ui/Toast";
import {
  APP_CHAT_NOTE, appInstallBridge, appViewSend, chatPath, getSavedApp, getSavedAppDocument, isSavedAppId, pinSavedAppRev, savedAppRpc, versionChoices,
  type SavedAppDocument,
} from "@/lib/saved-apps";

const message = (err: unknown, fallback: string) => err instanceof Error && err.message ? `${fallback} ${err.message}` : fallback;

// A saved app on its own: the page in the same sandboxed frame as in the chat, with a small header. With window=1 (the
// desktop launcher's own window) the app's back link is left out.
export function SavedAppView() {
  const params = useSearchParams();
  const rawId = params.get("id");
  const id = isSavedAppId(rawId) ? rawId : null;
  const own = params.get("window") === "1";
  const toast = useToast();
  const [doc, setDoc] = useState<SavedAppDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState(false);
  const [installed, setInstalled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [bridge] = useState(() => appInstallBridge());
  // The thread to link to is on the app record, not the document.
  const [threadId, setThreadId] = useState<number | null>(null);

  const [tick, setTick] = useState(0);
  const load = useCallback((appId: string) => Promise.all([getSavedAppDocument(appId), getSavedApp(appId).catch(() => null)]), []);
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    load(id)
      .then(([next, app]) => { if (!cancelled) { setDoc(next); setThreadId(app?.thread_id ?? null); setError(null); } })
      .catch((err) => { if (!cancelled) setError(message(err, "Could not open this app.")); });
    return () => { cancelled = true; };
  }, [id, load, tick]);
  useEffect(() => {
    if (!id || !bridge) return;
    let cancelled = false;
    bridge.list().then((ids) => { if (!cancelled) setInstalled(Array.isArray(ids) && ids.includes(id)); }).catch(() => { if (!cancelled) setInstalled(null); });
    return () => { cancelled = true; };
  }, [id, bridge]);

  const send = useMemo(() => appViewSend({ chat: doc?.chat ?? false, onNote: () => setNote(true) }), [doc?.chat]);

  const callRpc = useMemo(() => (id ? (req: RpcRequest) => savedAppRpc(id, req) : undefined), [id]);

  async function pin(rev: number | null) {
    if (!id) return;
    try { await pinSavedAppRev(id, rev); setTick((n) => n + 1); toast(rev === null ? "Following the newest version." : `Showing version ${rev}.`); }
    catch (err) { toast(message(err, "Could not change the version."), "danger"); }
  }
  async function toggleInstall() {
    if (!id || !doc || !bridge) return;
    setBusy(true);
    try {
      if (installed) { await bridge.uninstall(id); setInstalled(false); toast("Removed from this computer."); }
      else { await bridge.install({ id, name: doc.name }); setInstalled(true); toast("Installed on this computer."); }
    } catch (err) { toast(message(err, installed ? "Could not uninstall." : "Could not install."), "danger"); }
    finally { setBusy(false); }
  }

  if (!id) return <div className="min-h-dvh p-6"><Banner tone="danger">This is not a valid app link.</Banner></div>;

  const choices = doc ? versionChoices(doc.versions, doc.rev, doc.pinned_rev) : [];
  const showChat = doc?.chat === true && threadId !== null;
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex min-h-12 flex-wrap items-center gap-3 border-b border-line px-4 py-1.5">
        {!own && <Link href="/apps/" className="text-sm text-muted hover:underline">← Apps</Link>}
        <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">{doc?.name ?? "App"}</h1>
        {doc && <UsesChips uses={doc.uses} />}
        {doc && doc.versions.length > 1 && (
          <Menu trigger={<Button size="sm" type="button" icon={<ChevronDown size={14} aria-hidden="true" />}>Version {doc.rev}{doc.pinned_rev === null ? "" : " (pinned)"}</Button>}>
            {choices.map((v) => (
              <MenuItem key={v.rev} onSelect={() => void pin(v.rev)} icon={v.current ? <Check size={14} /> : <span className="inline-block w-3.5" />}>
                Version {v.rev}{v.newest ? " (newest)" : ""}{v.pinned ? ", pinned" : ""}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem onSelect={() => void pin(null)} disabled={doc.pinned_rev === null}>Follow newest</MenuItem>
          </Menu>
        )}
        {showChat && <Link href={chatPath(threadId)} className="inline-flex items-center gap-1 text-sm underline"><MessageSquare size={14} aria-hidden="true" />Go to chat</Link>}
        {bridge && doc && (installed
          ? <span className="inline-flex items-center gap-2 text-xs text-muted"><Check size={12} aria-hidden="true" />Installed<Button size="sm" type="button" variant="ghost" loading={busy} onClick={() => void toggleInstall()}>Uninstall</Button></span>
          : <Button size="sm" type="button" loading={busy} onClick={() => void toggleInstall()}>Install on this computer</Button>)}
      </header>
      {note && (
        <Banner action={showChat ? <Link href={chatPath(threadId)} className="text-sm underline">Go to chat</Link> : undefined}>
          {APP_CHAT_NOTE}
        </Banner>
      )}
      <main className="min-h-0 flex-1">
        {error && <div className="p-4"><Banner tone="danger" action={<Button type="button" onClick={() => setTick((n) => n + 1)}>Try again</Button>}>{error}</Banner></div>}
        {!doc && !error && <p className="p-6 text-center text-sm text-muted">Loading…</p>}
        {doc && <PageFrame key={`${doc.rev}`} pageId={`app:${id}`} title={doc.name} html={doc.html} uses={doc.uses} minHeight={doc.height} send={send} callRpc={callRpc} />}
      </main>
    </div>
  );
}
