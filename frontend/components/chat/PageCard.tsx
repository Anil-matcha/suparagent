"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Maximize2, Minimize2, Save } from "lucide-react";
import type { RpcRequest } from "@/lib/page-bridge";
import { PageFrame } from "./PageFrame";
import { UsesChips } from "./UsesChips";
import { appViewPath, pageRpc } from "@/lib/saved-apps";
import type { TranscriptItem } from "@/lib/transcript";

const BUTTON = "inline-flex items-center gap-1 rounded-field border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:opacity-50";

// A page an agent made, shown inline. It runs in a sandboxed frame with no access to this app; everything it may ask
// for goes through the bridge (lib/page-bridge.ts), which applies the limits.
export function PageCard({ item, onSend, onSave, saved = null, saving = false, threadId = null }: {
  item: Extract<TranscriptItem, { kind: "page" }>;
  // Sends text as the person's own message in this conversation; resolves false when it could not be sent.
  onSend?: (text: string) => Promise<boolean>;
  // Turns the page into an app. The button is disabled until a caller provides this.
  onSave?: (page: { pageId: string; rev: number; title: string; html: string }) => void;
  // The saved app this page already is (shows "Saved" and an Open link instead of the button).
  saved?: { id: string } | null;
  saving?: boolean;
  // The conversation this page is in; connected-app requests go through its page route.
  threadId?: number | null;
}) {
  const latest = item.versions.length - 1;
  const [viewing, setViewing] = useState(latest);
  const [prevLatest, setPrevLatest] = useState(latest);
  // A new revision arriving moves the card to it; stepping back is the person's choice.
  if (latest !== prevLatest) { setPrevLatest(latest); setViewing(latest); }
  const shown = item.versions[Math.min(viewing, latest)] ?? item.versions[latest];
  const [full, setFull] = useState(false);
  const callRpc = useMemo(
    () => (threadId === null ? undefined : (req: RpcRequest) => pageRpc(threadId, item.pageId, req)),
    [threadId, item.pageId],
  );

  useEffect(() => {
    if (!full) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);

  const frameless = shown.frameless;
  return (
    <>
      {full && <div className="fixed inset-0 z-40 bg-scrim" onClick={() => setFull(false)} aria-hidden />}
      <section
        aria-label={shown.title}
        className={full
          ? "fixed inset-4 z-50 flex flex-col overflow-hidden rounded-field border border-line bg-raised text-sm shadow-lg"
          : `overflow-hidden text-sm ${frameless ? "" : "rounded-field border border-line bg-raised"}`}
      >
        <header className="flex flex-wrap items-center gap-2 px-3 py-1.5">
          <strong className="min-w-0 flex-1 truncate">{shown.title}</strong>
          <UsesChips uses={shown.uses} />
          {item.versions.length > 1 && (
            <span className="inline-flex items-center gap-1 text-xs text-muted" role="group" aria-label="Versions">
              <button type="button" className={BUTTON} aria-label="Previous version" disabled={viewing <= 0} onClick={() => setViewing(Math.max(0, viewing - 1))}><ChevronLeft size={14} /></button>
              <span>v{shown.rev}</span>
              <button type="button" className={BUTTON} aria-label="Next version" disabled={viewing >= latest} onClick={() => setViewing(Math.min(latest, viewing + 1))}><ChevronRight size={14} /></button>
            </span>
          )}
          <button type="button" className={BUTTON} onClick={() => setFull(!full)} aria-pressed={full}>
            {full ? <Minimize2 size={14} /> : <Maximize2 size={14} />}{full ? "Exit full screen" : "Full screen"}
          </button>
          {saved ? (
            <span className="inline-flex items-center gap-2 text-xs text-muted">
              <span>Saved</span>
              <Link href={appViewPath(saved.id)} className="font-medium text-ink underline">Open</Link>
            </span>
          ) : (
            <button type="button" className={BUTTON} disabled={!onSave || saving} title={onSave ? undefined : "Saving pages as apps is not available yet"}
              onClick={() => onSave?.({ pageId: item.pageId, rev: shown.rev, title: shown.title, html: shown.html })}>
              <Save size={14} />{saving ? "Saving…" : "Save as app"}
            </button>
          )}
        </header>
        <PageFrame pageId={item.pageId} title={shown.title} html={shown.html} uses={shown.uses} minHeight={shown.height} fill={full} send={onSend} callRpc={callRpc} />
      </section>
    </>
  );
}
