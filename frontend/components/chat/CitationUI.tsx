"use client";

import { Quote, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { isSendKey } from "@/lib/composer";
import {
  CITATION_MAX_COMMENT_LENGTH,
  CITATION_MAX_QUOTE_LENGTH,
  citationAttachment,
  withCitationComment,
  type CitationAttachment,
} from "@/lib/citations";
import { captureCitationSelection, citationShortcutHint, isCitationShortcut, isMacPlatform } from "@/lib/citations-dom";

type Point = { left: number; top: number };

function place(element: HTMLElement, point: Point): void {
  const rect = element.getBoundingClientRect();
  element.style.left = `${Math.max(8, Math.min(point.left, window.innerWidth - rect.width - 8))}px`;
  element.style.top = `${Math.max(8, Math.min(point.top, window.innerHeight - rect.height - 8))}px`;
}

function focusComposer(): void {
  requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>("textarea[data-chat-composer]")?.focus());
}

const POPOVER_CLASS =
  "fixed z-50 rounded-xl border border-line bg-raised p-3 text-left text-ink shadow-xl";

function QuoteBlock({ quote, className }: { quote: string; className?: string }) {
  return (
    <>
      <div className="mb-1 text-xs text-muted">Quoted message</div>
      <div
        dir="auto"
        className={cn(
          "overflow-y-auto whitespace-pre-wrap border-l-2 border-line pl-3 text-[13px] leading-snug text-ink/90 [overflow-wrap:anywhere]",
          className,
        )}
      >
        {quote}
      </div>
    </>
  );
}

export function CitationEditor({
  citation,
  point,
  onSave,
  onCancel,
}: {
  citation: CitationAttachment;
  point?: Point;
  onSave: (citation: CitationAttachment) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [comment, setComment] = useState(citation.comment ?? "");
  const tooLong = comment.length > CITATION_MAX_COMMENT_LENGTH;

  useLayoutEffect(() => {
    const update = () => {
      const editor = ref.current;
      if (!editor) return;
      const composer = point ? null : document.querySelector<HTMLTextAreaElement>("textarea[data-chat-composer]");
      const rect = composer?.getBoundingClientRect();
      place(
        editor,
        point ?? {
          left: rect ? rect.left + (rect.width - editor.offsetWidth) / 2 : 16,
          top: rect ? rect.top - editor.offsetHeight - 8 : 16,
        },
      );
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [point]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label="Add a comment to your quote"
      className={cn(POPOVER_CLASS, "w-[min(28rem,calc(100vw-1rem))]")}
      style={point}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Escape" && !event.nativeEvent.isComposing) {
          event.preventDefault();
          onCancel();
        }
      }}
    >
      <QuoteBlock quote={citation.quote} className="max-h-36" />
      <textarea
        ref={inputRef}
        dir="auto"
        value={comment}
        rows={3}
        aria-label="Comment on quoted text"
        aria-invalid={tooLong || undefined}
        placeholder="Add an optional comment..."
        onChange={(event) => setComment(event.target.value)}
        onKeyDown={(event) => {
          if (isSendKey({ key: event.key, shiftKey: event.shiftKey, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode })) {
            event.preventDefault();
            if (!tooLong) onSave(withCitationComment(citation, comment));
          }
        }}
        className="mt-3 block w-full resize-none rounded-lg border border-line bg-inset px-3 py-2 text-[13px] leading-snug text-ink outline-none placeholder:text-muted focus:border-accent aria-invalid:border-danger"
      />
      {tooLong && (
        <p role="status" className="mt-1 text-xs text-danger">
          Comment can be at most {CITATION_MAX_COMMENT_LENGTH.toLocaleString()} characters.
        </p>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="cursor-pointer rounded-full border border-line px-3.5 py-1.5 text-xs font-medium text-ink hover:bg-hover"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={tooLong}
          onClick={() => onSave(withCitationComment(citation, comment))}
          className="cursor-pointer rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </div>,
    document.body,
  );
}

export function CitationSelectionToolbar({
  viewportRef,
  onAdd,
}: {
  viewportRef: RefObject<HTMLElement | null>;
  onAdd: (citation: CitationAttachment) => void;
}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [isMac] = useState(() => isMacPlatform());
  const [captured, setCaptured] = useState<{ citation: CitationAttachment; point: Point } | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const viewport = viewportRef.current;
    const update = () => {
      if (editing || !viewport) return;
      const selection = captureCitationSelection(viewport, window.getSelection());
      if (!selection) {
        setCaptured(null);
        return;
      }
      const itemKey = selection.source.dataset.citationSource;
      const threadId = selection.source.dataset.citationThread ?? "0";
      if (!itemKey) {
        setCaptured(null);
        return;
      }
      const rects = selection.range.getClientRects();
      const rect = rects.item(rects.length - 1) ?? selection.range.getBoundingClientRect();
      const citation =
        selection.selector.text.length <= CITATION_MAX_QUOTE_LENGTH
          ? citationAttachment({ threadId, itemKey }, selection.selector)
          : {
              kind: "citation" as const,
              version: 1 as const,
              id: "selection-too-long",
              quote: selection.selector.text,
              source: { threadId, itemKey, ...selection.selector },
              size: selection.selector.text.length,
            };
      setCaptured({ citation, point: { left: rect.left, top: rect.bottom + 8 } });
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (!captured) return;
      if (editing) {
        if (event.repeat && isCitationShortcut(event, isMac)) event.preventDefault();
        return;
      }
      if (isCitationShortcut(event, isMac)) {
        if (captured.citation.quote.length > CITATION_MAX_QUOTE_LENGTH) return;
        event.preventDefault();
        event.stopPropagation();
        setEditing(true);
      }
    };

    document.addEventListener("selectionchange", update);
    viewport?.addEventListener("pointerup", update);
    viewport?.addEventListener("keyup", update);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("selectionchange", update);
      viewport?.removeEventListener("pointerup", update);
      viewport?.removeEventListener("keyup", update);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [captured, editing, isMac, viewportRef]);

  useLayoutEffect(() => {
    if (!captured || editing) return;
    const update = () => {
      if (buttonRef.current) place(buttonRef.current, captured.point);
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [captured, editing]);

  if (!captured) return null;

  if (editing) {
    return (
      <CitationEditor
        citation={captured.citation}
        point={captured.point}
        onCancel={() => {
          setEditing(false);
          setCaptured(null);
          focusComposer();
        }}
        onSave={(citation) => {
          onAdd(citation);
          window.getSelection()?.removeAllRanges();
          setEditing(false);
          setCaptured(null);
          focusComposer();
        }}
      />
    );
  }

  const tooLong = captured.citation.quote.length > CITATION_MAX_QUOTE_LENGTH;
  const hint = !tooLong ? citationShortcutHint(isMac) : null;

  return createPortal(
    <button
      ref={buttonRef}
      type="button"
      disabled={tooLong}
      aria-label={tooLong ? "Selection is too long to cite" : "Cite selected text"}
      aria-keyshortcuts={hint ? (isMac ? "Meta+L" : "Control+L") : undefined}
      title={tooLong ? "Selection is too long to cite" : `Cite selected text (${hint})`}
      onPointerDown={(event) => event.preventDefault()}
      onClick={() => setEditing(true)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          setCaptured(null);
        }
      }}
      className="fixed z-50 flex cursor-pointer items-center gap-1.5 rounded-full border border-line bg-raised px-3 py-1.5 text-xs font-medium text-ink shadow-lg hover:bg-hover disabled:text-danger"
      style={captured.point}
    >
      <Quote size={13} aria-hidden="true" />
      <span>{tooLong ? "Shorten selection" : "Cite"}</span>
      {hint && <kbd aria-hidden="true" className="font-sans text-[11px] font-normal text-muted">{hint}</kbd>}
    </button>,
    document.body,
  );
}

export function CitationBadge({
  citation,
  onChange,
  onRemove,
  onNavigate,
}: {
  citation: CitationAttachment;
  onChange?: (citation: CitationAttachment) => void;
  onRemove?: () => void;
  onNavigate?: () => boolean;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const detailsRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [point, setPoint] = useState({ left: 16, top: 22 });

  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const trigger = triggerRef.current;
      const details = detailsRef.current;
      if (!trigger || !details) return;
      const r = trigger.getBoundingClientRect();
      place(details, { left: r.left, top: r.bottom + 6 });
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [open]);

  if (editing && onChange) {
    return (
      <CitationEditor
        citation={citation}
        onCancel={() => {
          setEditing(false);
          triggerRef.current?.focus();
        }}
        onSave={(next) => {
          onChange(next);
          setEditing(false);
          triggerRef.current?.focus();
        }}
      />
    );
  }

  return (
    <span className="relative inline-flex max-w-full items-center gap-1">
      <button
        ref={triggerRef}
        type="button"
        onClick={(event) => {
          const r = event.currentTarget.getBoundingClientRect();
          setPoint({ left: r.left, top: r.bottom + 6 });
          setUnavailable(false);
          setOpen((v) => !v);
        }}
        aria-expanded={open}
        aria-label={`Open quote: ${citation.quote.slice(0, 80)}`}
        className="inline-flex max-w-64 cursor-pointer items-center gap-1.5 rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-[11px] text-ink hover:border-accent/60"
      >
        <Quote size={11} className="shrink-0 text-accent" aria-hidden="true" />
        <span className="truncate">{citation.quote.replace(/\s+/g, " ")}</span>
        {citation.comment && <span className="shrink-0 text-[10px] text-muted">· note</span>}
      </button>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label="Remove quote"
          className="inline-flex size-5 cursor-pointer items-center justify-center rounded-full text-muted hover:bg-hover hover:text-ink"
        >
          <X size={11} />
        </button>
      )}
      {open &&
        createPortal(
          <div
            role="dialog"
            aria-label="Quote details"
            ref={(el) => {
              detailsRef.current = el;
            }}
            className={cn(POPOVER_CLASS, "w-[min(28rem,calc(100vw-1rem))]")}
            style={point}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                setOpen(false);
                triggerRef.current?.focus();
              }
            }}
          >
            <QuoteBlock quote={citation.quote} className="max-h-56" />
            {citation.comment && (
              <div dir="auto" className="mt-3 text-[13px] leading-snug [overflow-wrap:anywhere]">
                <span className="font-medium text-ink">Comment: </span>
                <span className="whitespace-pre-wrap text-muted">{citation.comment}</span>
              </div>
            )}
            {unavailable && (
              <p role="status" className="mt-2 text-xs text-warning">
                Source message is not currently loaded in view.
              </p>
            )}
            <div className="mt-3 flex justify-end gap-2">
              {onNavigate && (
                <button
                  type="button"
                  onClick={() => {
                    const found = onNavigate();
                    setUnavailable(!found);
                    if (found) setOpen(false);
                  }}
                  className="cursor-pointer rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-hover"
                >
                  Go to source
                </button>
              )}
              {onChange && (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    setEditing(true);
                  }}
                  className="cursor-pointer rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-hover"
                >
                  Edit comment
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  triggerRef.current?.focus();
                }}
                className="cursor-pointer rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-hover"
              >
                Close
              </button>
            </div>
          </div>,
          document.body,
        )}
    </span>
  );
}
