// The host side of a bot page: what the frame may ask for and what is allowed. Pure and testable: everything outside
// (sending a message, opening a link, posting back, the clock, storage) is an adapter handed in.
import { PageStateError, type PageState } from "./page-state.ts";

export const BRIDGE_LIMITS = {
  messageBytes: 1_000_000, methodChars: 200, text: 4000, url: 2048, engagementMs: 10_000,
  sendGapMs: 3000, sendWindowMs: 10 * 60_000, sendPerWindow: 10, linkGapMs: 2000,
  heightMin: 48, heightMax: 720,
  rpcShortMs: 10_000, rpcShortMax: 10, rpcLongMs: 10 * 60_000, rpcLongMax: 120, argBytes: 64 * 1024,
} as const;

const TOOLKIT_RE = /^[a-z0-9_][a-z0-9_-]{0,63}$/;
const TOOL_RE = /^[A-Z0-9_]{1,128}$/;

export type RpcRequest = {
  method: "connectors/list" | "connectors/call"; params?: Record<string, unknown>; engaged: boolean;
  // The one-use token from a needs_approval answer, sent back with the same call once the person allows it.
  confirmation?: string;
};

// What the host shows while a page waits for the person's decision. The token stays inside the bridge.
export type PendingApproval = { summary: string; expiresAt: number };

export type BridgeAdapters = {
  pageId: string;
  title: string;
  uses: readonly string[];
  // The frame's window; messages from any other source are ignored.
  frame: () => unknown;
  now: () => number;
  // Sends text as the person's message in the thread; resolves false when it could not be sent.
  send: (text: string) => Promise<boolean>;
  openLink: (url: string) => void;
  respond: (message: Record<string, unknown>) => void;
  setHeight: (height: number) => void;
  state: PageState;
  // Forwards a connected-app request to the backend; throws an Error (with `code` when the backend gave one).
  callRpc?: (req: RpcRequest) => Promise<unknown>;
  // A change the person must allow or deny (bridge.approve() / bridge.deny()); null when none is waiting.
  onApproval?: (pending: PendingApproval | null) => void;
  // Starts a timer and returns its cancel function (default: setTimeout).
  setTimer?: (fn: () => void, ms: number) => () => void;
};

export const APPROVAL_MS = 120_000;
const RPC_MESSAGES: Record<string, string> = {
  needs_engagement: "Click inside the page first, then try again.",
};
function rpcMessage(err: unknown): string {
  const e = err as { code?: unknown; status?: unknown; message?: unknown } | null;
  const code = typeof e?.code === "string" ? e.code : "";
  if (RPC_MESSAGES[code]) return RPC_MESSAGES[code];
  if (typeof e?.message === "string" && e.message) return e.message;
  if (e?.status === 413) return "That request is too large.";
  if (e?.status === 429) return "This page is calling connected apps too often. Try again in a moment.";
  if (e?.status === 504) return "The connected app took too long to answer.";
  return "The request could not be completed.";
}

type Request = { id: string | number | null; method: string; params: Record<string, unknown> };

export function parseRequest(data: unknown): Request | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const m = data as Record<string, unknown>;
  if (m.jsonrpc !== "2.0" || typeof m.method !== "string" || m.method.length < 1 || m.method.length > BRIDGE_LIMITS.methodChars) return null;
  if (m.id !== undefined && typeof m.id !== "string" && typeof m.id !== "number") return null;
  if (m.params !== undefined && (m.params === null || typeof m.params !== "object" || Array.isArray(m.params))) return null;
  try {
    if (JSON.stringify(data).length > BRIDGE_LIMITS.messageBytes) return null;
  } catch { return null; }
  return { id: m.id === undefined ? null : m.id, method: m.method, params: (m.params ?? {}) as Record<string, unknown> };
}

// Only plain http(s) links, no credentials, a sane length.
export function checkLink(url: unknown): string {
  if (typeof url !== "string" || !url) throw new Error("A link is required.");
  if (url.length > BRIDGE_LIMITS.url) throw new Error(`A link can be at most ${BRIDGE_LIMITS.url} characters.`);
  let parsed: URL;
  try { parsed = new URL(url); } catch { throw new Error("That is not a valid link."); }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new Error("Only http and https links can be opened.");
  if (parsed.username || parsed.password) throw new Error("Links with a username or password are not allowed.");
  return parsed.href;
}

function checkText(value: unknown): string {
  if (typeof value !== "string" || value.length < 1) throw new Error("Text is required.");
  if (value.length > BRIDGE_LIMITS.text) throw new Error(`Text can be at most ${BRIDGE_LIMITS.text} characters.`);
  return value;
}

// Page-supplied context is quoted and labelled as untrusted: it is not the person's words.
export function withContext(text: string, context: string | null, title: string): string {
  if (!context) return text;
  const quoted = context.split("\n").map((line) => `> ${line}`).join("\n");
  return `${text}\n\nContext supplied by the page "${title}" (untrusted, not written by the person):\n${quoted}`;
}

export function createPageBridge(a: BridgeAdapters) {
  let engagedAt = -Infinity;
  let sentAt = -Infinity;
  let sends: number[] = [];
  let linkAt = -Infinity;
  let context: string | null = null;
  let rpcTimes: number[] = [];

  const engaged = () => a.now() - engagedAt <= BRIDGE_LIMITS.engagementMs;
  const needEngagement = () => { if (!engaged()) throw new Error("Click inside the page first: this needs a click from you."); };

  function rateLimitRpc() {
    const now = a.now();
    rpcTimes = rpcTimes.filter((t) => now - t < BRIDGE_LIMITS.rpcLongMs);
    if (rpcTimes.length >= BRIDGE_LIMITS.rpcLongMax || rpcTimes.filter((t) => now - t < BRIDGE_LIMITS.rpcShortMs).length >= BRIDGE_LIMITS.rpcShortMax) {
      throw new Error("This page is calling connected apps too often. Try again in a moment.");
    }
    rpcTimes.push(now);
  }

  type Pending = { req: RpcRequest; token: string; resolve: (v: unknown) => void; reject: (e: Error) => void; cancel: () => void };
  let pending: Pending | null = null;
  const startTimer = a.setTimer ?? ((fn, ms) => { const t = setTimeout(fn, ms); return () => clearTimeout(t); });

  function settle(): Pending | null {
    const p = pending;
    if (!p) return null;
    pending = null;
    p.cancel();
    a.onApproval?.(null);
    return p;
  }

  class NeedsApproval extends Error {
    token: string;
    constructor(summary: string, token: string) { super(summary); this.token = token; }
  }

  async function post(req: RpcRequest): Promise<unknown> {
    if (!a.callRpc) throw new Error("Connected apps are not available here.");
    try {
      return await a.callRpc(req);
    } catch (err) {
      const e = err as { code?: unknown; detail?: unknown } | null;
      const detail = e?.detail && typeof e.detail === "object" ? e.detail as Record<string, unknown> : null;
      if (e?.code === "needs_approval" && !req.confirmation && typeof detail?.confirmation === "string") {
        const summary = typeof detail.summary === "string" && detail.summary ? detail.summary : rpcMessage(err);
        throw new NeedsApproval(summary, detail.confirmation);
      }
      throw new Error(rpcMessage(err));
    }
  }

  async function forward(req: RpcRequest): Promise<unknown> {
    try {
      return await post(req);
    } catch (err) {
      if (!(err instanceof NeedsApproval)) throw err;
      if (pending) throw new Error("Another approval for this page is waiting. Try again after it is answered.");
      return new Promise((resolve, reject) => {
        const cancel = startTimer(() => {
          const p = settle();
          p?.reject(new Error("The approval timed out, so nothing was done."));
        }, APPROVAL_MS);
        pending = { req, token: err.token, resolve, reject, cancel };
        a.onApproval?.({ summary: err.message, expiresAt: a.now() + APPROVAL_MS });
      });
    }
  }

  async function dispatch(req: Request): Promise<unknown> {
    const p = req.params;
    switch (req.method) {
      case "ui/message": {
        needEngagement();
        const text = checkText(p.text);
        const now = a.now();
        sends = sends.filter((t) => now - t < BRIDGE_LIMITS.sendWindowMs);
        if (now - sentAt < BRIDGE_LIMITS.sendGapMs) throw new Error("Messages from a page are limited to one every 3 seconds.");
        if (sends.length >= BRIDGE_LIMITS.sendPerWindow) throw new Error("This page has sent too many messages. Try again later.");
        sentAt = now; sends.push(now);
        const composed = withContext(text, context, a.title);
        const ok = await a.send(composed);
        if (!ok) throw new Error("The message could not be sent right now.");
        context = null;
        return { ok: true };
      }
      case "ui/open-link": {
        needEngagement();
        const url = checkLink(p.url);
        const now = a.now();
        if (now - linkAt < BRIDGE_LIMITS.linkGapMs) throw new Error("Links from a page are limited to one every 2 seconds.");
        linkAt = now;
        a.openLink(url);
        return { ok: true };
      }
      case "ui/update-model-context": {
        const text = checkText(p.text);
        // Coalesced: the newest text wins; it is held until the next message is sent.
        context = text;
        return { ok: true };
      }
      case "size-changed":
      case "ui/size-changed": {
        const h = typeof p.height === "number" && Number.isFinite(p.height) ? p.height : null;
        if (h === null) throw new Error("A height is required.");
        a.setHeight(Math.min(BRIDGE_LIMITS.heightMax, Math.max(BRIDGE_LIMITS.heightMin, Math.round(h))));
        return null;
      }
      case "state/get": case "state/set": case "state/keys": {
        if (!a.uses.includes("state")) throw new Error("This page did not declare the 'state' capability, so it cannot store data.");
        if (req.method === "state/get") return a.state.get(p.key);
        if (req.method === "state/keys") return a.state.keys();
        return a.state.set(p.key, p.value);
      }
      case "suparagent/connectors/list": {
        if (!a.uses.some((u) => u.includes(":"))) throw new Error("This page did not declare any connected-app tools in 'uses' (for example 'gmail:GMAIL_FETCH_EMAILS'), so it cannot use connected apps.");
        rateLimitRpc();
        return forward({ method: "connectors/list", engaged: engaged() });
      }
      case "suparagent/connectors/call": {
        const { toolkit, tool } = p;
        if (typeof toolkit !== "string" || !TOOLKIT_RE.test(toolkit)) throw new Error("A valid toolkit name is required.");
        if (typeof tool !== "string" || !TOOL_RE.test(tool)) throw new Error("A valid tool name is required.");
        if (!a.uses.includes(`${toolkit}:${tool}`)) throw new Error(`This page did not declare '${toolkit}:${tool}' in 'uses', so it cannot call that tool.`);
        const args: unknown = p.arguments ?? {};
        if (args === null || typeof args !== "object" || Array.isArray(args)) throw new Error("Arguments must be an object.");
        let size: number;
        try { size = JSON.stringify(args).length; } catch { throw new Error("Arguments must be JSON."); }
        if (size > BRIDGE_LIMITS.argBytes) throw new Error("Arguments can be at most 64 KB.");
        rateLimitRpc();
        return forward({ method: "connectors/call", params: { toolkit, tool, arguments: args as Record<string, unknown> }, engaged: engaged() });
      }
      default:
        throw new Error(`Unknown method ${req.method.slice(0, 50)}.`);
    }
  }

  return {
    // The person chose Allow: the same call goes again with its token. The click is the host's own, so it counts as engaged.
    async approve(): Promise<void> {
      const p = settle();
      if (!p) return;
      try { p.resolve(await post({ ...p.req, engaged: true, confirmation: p.token })); }
      catch (err) { p.reject(err instanceof Error ? err : new Error("The request could not be completed.")); }
    },
    deny(): void { settle()?.reject(new Error("The person declined this request.")); },
    // The frame is going away: a waiting request is dropped.
    dispose(): void { settle()?.reject(new Error("The page was closed before the request was answered.")); },
    // The person clicked inside the frame region.
    noteEngagement() { engagedAt = a.now(); },
    // The context held for the next message the person sends themselves (null when none).
    pendingContext(): string | null { return context; },
    async handle(event: { source: unknown; data: unknown }): Promise<void> {
      if (event.source === null || event.source === undefined || event.source !== a.frame()) return;
      const req = parseRequest(event.data);
      if (!req) return;
      try {
        const result = await dispatch(req);
        if (req.id !== null) a.respond({ jsonrpc: "2.0", id: req.id, result: result ?? null });
      } catch (err) {
        const message = err instanceof PageStateError || err instanceof Error ? err.message : "Request refused";
        if (req.id !== null) a.respond({ jsonrpc: "2.0", id: req.id, error: { code: -32000, message } });
      }
    },
  };
}
