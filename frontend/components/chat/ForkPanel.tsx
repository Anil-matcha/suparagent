"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { ArrowUpRight, GitBranch, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Agent, type Engine, type Thread } from "@/lib/api";
import { buildAnnotatedMessage, type CitationAttachment, type ReplyTarget } from "@/lib/citations";
import { pendingApprovals } from "@/lib/transcript";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { useThreadStream } from "./useThreadStream";

const WIDTH_STORAGE_KEY = "suparagent:fork-panel-width";
const MIN_WIDTH = 340;
const MAX_WIDTH = 760;
const DEFAULT_WIDTH = 460;

function readSavedWidth(): number {
  if (typeof window === "undefined") return DEFAULT_WIDTH;
  const raw = Number(window.localStorage.getItem(WIDTH_STORAGE_KEY));
  return Number.isFinite(raw) ? Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, raw)) : DEFAULT_WIDTH;
}

export function ForkPanel({
  threadId,
  inline,
  agents,
  engines,
  onClose,
  onOpenFullThread,
  onListChanged,
}: {
  threadId: number;
  inline: boolean;
  agents: Agent[] | null;
  engines: Engine[] | null;
  onClose: () => void;
  onOpenFullThread: (threadId: number) => void;
  onListChanged: () => void;
}) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [answering, setAnswering] = useState<string | null>(null);
  const [approvalErrors, setApprovalErrors] = useState<Record<string, string>>({});
  const [replyTo, setReplyTo] = useState<ReplyTarget | null>(null);
  const [citations, setCitations] = useState<CitationAttachment[]>([]);
  const [focusComposer, setFocusComposer] = useState(true);
  const [width, setWidth] = useState<number>(readSavedWidth);
  const draggingRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const stream = useThreadStream(threadId);
  const { transcript } = stream;

  useEffect(() => {
    let active = true;
    setThread(null);
    setLoadError(null);
    setDraft("");
    setSendError(null);
    setReplyTo(null);
    setCitations([]);
    setFocusComposer(true);
    api
      .getThread(threadId)
      .then((res) => {
        if (active) setThread(res);
      })
      .catch((err) => {
        if (active) setLoadError(err instanceof ApiError ? err.message : "Could not load branched conversation.");
      });
    return () => {
      active = false;
    };
  }, [threadId]);

  const waiting = pendingApprovals(transcript).length > 0;
  const agent = thread?.agent_id != null && agents ? agents.find((a) => a.id === thread.agent_id) ?? null : null;
  const blockedReason =
    stream.connection === "failed"
      ? stream.error
      : thread?.session_state === "broken"
        ? "Session cannot continue."
        : waiting
          ? "The engine is waiting for your answer above."
          : null;

  const send = async () => {
    if (transcript.runningTurnId !== null || sending) return;
    const message = buildAnnotatedMessage(draft.trim(), { replyTo, citations });
    if (!message) return;
    setSendError(null);
    setSending(true);
    try {
      await api.sendTurn(threadId, message);
      setDraft("");
      setReplyTo(null);
      setCitations([]);
      onListChanged();
    } catch (err) {
      setSendError(err instanceof ApiError ? err.message : "Could not send message.");
    } finally {
      setSending(false);
    }
  };

  const stop = async () => {
    if (stopping || transcript.runningTurnId === null) return;
    setStopping(true);
    try {
      await api.stopThread(threadId);
    } catch {
      // ignore; stream reconciles state
    } finally {
      setStopping(false);
    }
  };

  const answer = async (approvalId: string, decision: "allow" | "deny", remember?: boolean) => {
    setAnswering(approvalId);
    setApprovalErrors((prev) => {
      const next = { ...prev };
      delete next[approvalId];
      return next;
    });
    try {
      await api.answerThreadApproval(threadId, approvalId, decision, remember);
    } catch (err) {
      setApprovalErrors((prev) => ({
        ...prev,
        [approvalId]: err instanceof ApiError ? err.message : "Failed to submit decision.",
      }));
    } finally {
      setAnswering(null);
    }
  };

  const header = (
    <header className="flex shrink-0 items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
      <GitBranch size={15} className="shrink-0 text-accent" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-ink">
          {thread?.title ? `Branch · ${thread.title}` : "Side-by-side branch"}
        </p>
        {agent && <p className="truncate text-xs text-muted">{agent.name}</p>}
      </div>
      <button
        type="button"
        onClick={() => onOpenFullThread(threadId)}
        title="Open in main view"
        className="inline-flex cursor-pointer items-center gap-1 rounded-field border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-hover"
      >
        <ArrowUpRight size={13} aria-hidden="true" />
        <span>Open</span>
      </button>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close branch panel"
        title="Close branch panel"
        className="inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink"
      >
        <X size={16} aria-hidden="true" />
      </button>
    </header>
  );

  const body = (
    <div className="flex min-h-0 flex-1 flex-col bg-app text-ink">
      {header}
      {loadError ? (
        <p role="alert" className="p-4 text-sm text-danger">
          {loadError}
        </p>
      ) : (
        <>
          <Conversation
            threadId={threadId}
            workspacePath={thread?.workspace_path ?? null}
            loading={thread === null}
            agent={agent ? { id: agent.id, name: agent.name } : null}
            transcript={transcript}
            answering={answering}
            approvalErrors={approvalErrors}
            onAnswer={answer}
            onReactionReply={async () => false}
            sendingReactionReplyDisabled={true}
            onReply={(target) => {
              setReplyTo(target);
              setFocusComposer(true);
            }}
            onAddCitation={(citation) => setCitations((prev) => [...prev, citation])}
          />
          <Composer
            draftKey={`branch-${threadId}`}
            focusRequested={focusComposer}
            onFocused={() => setFocusComposer(false)}
            value={draft}
            onChange={setDraft}
            onSend={send}
            sending={sending}
            blockedReason={blockedReason}
            error={sendError}
            running={transcript.runningTurnId !== null}
            stopping={stopping}
            onStop={stop}
            engines={engines}
            selectedProvider={thread?.provider}
            selectedModel={thread?.model ?? null}
            replyTo={replyTo}
            onClearReply={() => setReplyTo(null)}
            citations={citations}
            onUpdateCitation={(id, next) =>
              setCitations((prev) => prev.map((c) => (c.id === id ? next : c)))
            }
            onRemoveCitation={(id) =>
              setCitations((prev) => prev.filter((c) => c.id !== id))
            }
          />
        </>
      )}
    </div>
  );

  if (!inline) {
    return (
      <RadixDialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
        <RadixDialog.Portal>
          <RadixDialog.Overlay className="fixed inset-0 z-40 animate-fade-in bg-scrim" />
          <RadixDialog.Content
            aria-label="Side-by-side branch"
            aria-describedby={undefined}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[34rem] flex-col border-l border-line bg-app text-ink shadow-xl"
          >
            {body}
          </RadixDialog.Content>
        </RadixDialog.Portal>
      </RadixDialog.Root>
    );
  }

  return (
    <aside
      aria-label="Side-by-side branch"
      style={{ width: `min(${width}px, 46vw)` }}
      className="relative flex min-h-0 min-w-[300px] shrink-0 flex-col border-l border-line bg-app text-ink"
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize branch panel"
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") setWidth((w) => Math.min(MAX_WIDTH, w + 24));
          if (event.key === "ArrowRight") setWidth((w) => Math.max(MIN_WIDTH, w - 24));
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          draggingRef.current = { startX: event.clientX, startWidth: width };
        }}
        onPointerMove={(event) => {
          if (!draggingRef.current) return;
          const delta = draggingRef.current.startX - event.clientX;
          const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, draggingRef.current.startWidth + delta));
          setWidth(next);
        }}
        onPointerUp={() => {
          if (draggingRef.current) {
            draggingRef.current = null;
            if (typeof window !== "undefined") window.localStorage.setItem(WIDTH_STORAGE_KEY, String(width));
          }
        }}
        className="absolute inset-y-0 left-0 z-20 w-1.5 cursor-col-resize hover:bg-accent/40 focus-visible:bg-accent/60"
      />
      {body}
    </aside>
  );
}
