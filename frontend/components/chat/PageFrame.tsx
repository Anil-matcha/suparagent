"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPageBridge, BRIDGE_LIMITS, type RpcRequest, type PendingApproval } from "@/lib/page-bridge";
import { buildSrcdoc } from "@/lib/page-frame";
import { createPageState } from "@/lib/page-state";
import { openExternalLink } from "@/lib/external-link";

export function useAppTheme(): "light" | "dark" {
  const read = () => (typeof document !== "undefined" && document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  const [theme, setTheme] = useState<"light" | "dark">(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

// The sandboxed frame of a bot page and its bridge (lib/page-bridge.ts), shared by the chat card and the app view. The
// page has no access to this app; everything it may ask for goes through the bridge, which applies the limits.
// `pageId` keys the host-side saved data (a chat page uses its page id, a saved app "app:<id>").
export function PageFrame({ pageId, title, html, uses, minHeight, fill = false, send, callRpc }: {
  pageId: string;
  title: string;
  html: string;
  uses: readonly string[];
  minHeight: number;
  // Fill the parent's height instead of fitting the page.
  fill?: boolean;
  // Sends text as the person's message; resolves false (or throws a readable Error) when it could not be sent.
  send?: (text: string) => Promise<boolean>;
  // Forwards a connected-app request to the backend (the page's own route); absent when none is available.
  callRpc?: (req: RpcRequest) => Promise<unknown>;
}) {
  const theme = useAppTheme();
  const [size, setSize] = useState<{ doc: string; height: number }>({ doc: "", height: 0 });
  const frame = useRef<HTMLIFrameElement>(null);
  const hovered = useRef(false);
  const sendRef = useRef(send);
  const rpcRef = useRef(callRpc);
  const bridgeRef = useRef<ReturnType<typeof createPageBridge> | null>(null);
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  useEffect(() => { sendRef.current = send; rpcRef.current = callRpc; }, [send, callRpc]);

  const usesKey = uses.join(",");
  const srcdoc = useMemo(() => buildSrcdoc({ html, theme, uses: usesKey ? usesKey.split(",") : [] }), [html, usesKey, theme]);

  useEffect(() => {
    let storage: Storage | null = null;
    try { storage = window.localStorage; } catch { storage = null; }
    const bridge = createPageBridge({
      pageId, title, uses: usesKey ? usesKey.split(",") : [],
      frame: () => frame.current?.contentWindow ?? null, now: () => Date.now(),
      send: async (text) => (sendRef.current ? sendRef.current(text) : false),
      openLink: openExternalLink,
      respond: (message) => frame.current?.contentWindow?.postMessage(message, "*"),
      setHeight: (height) => setSize({ doc: srcdoc, height }),
      state: createPageState(storage, pageId),
      callRpc: (req) => (rpcRef.current ? rpcRef.current(req) : Promise.reject(new Error("Connected apps are not available here."))),
      onApproval: setApproval,
    });
    bridgeRef.current = bridge;
    const onMessage = (event: MessageEvent) => { void bridge.handle({ source: event.source, data: event.data }); };
    // Clicks inside a frame never reach this page. The click is seen as the window losing focus to the frame while the
    // pointer is over it; a frame cannot fake that.
    const onBlur = () => { if (hovered.current && document.activeElement === frame.current) bridge.noteEngagement(); };
    window.addEventListener("message", onMessage);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("message", onMessage); window.removeEventListener("blur", onBlur);
      bridge.dispose(); bridgeRef.current = null;
    };
  }, [pageId, title, usesKey, srcdoc]);

  const measured = size.doc === srcdoc ? size.height : 0;
  const height = Math.min(BRIDGE_LIMITS.heightMax, Math.max(minHeight, measured));
  return (
    <>
    <div
      className={fill ? "min-h-0 flex-1" : ""}
      onMouseEnter={() => { hovered.current = true; if (document.activeElement === frame.current) window.focus(); }}
      onMouseLeave={() => { hovered.current = false; }}
    >
      <iframe
        ref={frame}
        title={title}
        sandbox="allow-scripts allow-forms"
        srcDoc={srcdoc}
        referrerPolicy="no-referrer"
        className="block w-full border-0 bg-transparent"
        style={{ height: fill ? "100%" : height }}
      />
    </div>
    {approval && (
      <div role="alertdialog" aria-label="Approval needed" className="flex flex-wrap items-center gap-2 border-t border-line bg-raised px-3 py-2 text-sm">
        <span className="min-w-0 flex-1">{title} wants to {approval.summary}</span>
        <button type="button" className="rounded-field border border-line px-2 py-1 text-xs font-medium hover:bg-hover" onClick={() => bridgeRef.current?.deny()}>Deny</button>
        <button type="button" className="rounded-field bg-accent px-2 py-1 text-xs font-medium text-accent-fg hover:opacity-90" onClick={() => void bridgeRef.current?.approve()}>Allow</button>
      </div>
    )}
    </>
  );
}
