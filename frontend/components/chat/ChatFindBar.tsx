"use client";

import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  applyFindHighlights,
  clearFindHighlights,
  collectFindDomMatches,
  scrollToFindMatch,
  stepMatchIndex,
  type FindDomMatch,
} from "@/lib/chat-find";

export function ChatFindBar({
  open,
  focusNonce,
  viewportRef,
  contentVersion,
  onClose,
}: {
  open: boolean;
  focusNonce: number;
  viewportRef: RefObject<HTMLDivElement | null>;
  contentVersion: string;
  onClose: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<FindDomMatch[]>([]);
  const [activeIndex, setActiveIndex] = useState(-1);
  const lastQueryRef = useRef("");

  useEffect(() => {
    if (!open) {
      clearFindHighlights();
      return;
    }
    const id = window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    return () => window.cancelAnimationFrame(id);
  }, [open, focusNonce]);

  useEffect(() => {
    if (!open) return;
    const viewport = viewportRef.current;
    if (!viewport || !query.trim()) {
      setMatches([]);
      setActiveIndex(-1);
      lastQueryRef.current = query;
      clearFindHighlights();
      return;
    }

    const found = collectFindDomMatches(viewport, query);
    const queryChanged = lastQueryRef.current !== query;
    lastQueryRef.current = query;

    setMatches(found);
    setActiveIndex((prev) => {
      if (!found.length) return -1;
      if (queryChanged || prev < 0) return 0;
      return Math.min(prev, found.length - 1);
    });
  }, [open, query, contentVersion, viewportRef]);

  useEffect(() => {
    if (!open) return;
    applyFindHighlights(matches, activeIndex);
    if (activeIndex >= 0 && activeIndex < matches.length && viewportRef.current) {
      scrollToFindMatch(viewportRef.current, matches[activeIndex]!);
    }
  }, [open, matches, activeIndex, viewportRef]);

  useEffect(() => () => clearFindHighlights(), []);

  const step = (delta: 1 | -1) => {
    if (!matches.length) return;
    setActiveIndex((prev) => stepMatchIndex(prev, delta, matches.length));
  };

  const countText = useMemo(() => {
    if (!query.trim()) return "";
    if (!matches.length) return "0 / 0";
    return `${Math.max(1, activeIndex + 1)} / ${matches.length}`;
  }, [query, matches.length, activeIndex]);

  if (!open) return null;

  return (
    <div
      role="search"
      aria-label="Find in conversation"
      className="pointer-events-auto absolute right-4 top-3 z-30 flex items-center gap-1 rounded-field border border-line bg-raised px-2.5 py-1.5 shadow-pop animate-pop-in"
    >
      <Search size={14} className="shrink-0 text-muted" aria-hidden="true" />
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
            return;
          }
          if (event.key === "Enter" || event.key === "F3") {
            event.preventDefault();
            step(event.shiftKey ? -1 : 1);
          }
        }}
        placeholder="Find in chat…"
        aria-label="Find in conversation"
        className="w-44 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none sm:w-56"
      />
      {countText && (
        <span
          aria-live="polite"
          className="shrink-0 select-none px-1.5 text-xs tabular-nums text-muted"
        >
          {countText}
        </span>
      )}
      <button
        type="button"
        onClick={() => step(-1)}
        disabled={!matches.length}
        aria-label="Previous match"
        title="Previous match (Shift+Enter)"
        className="inline-flex size-7 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ChevronUp size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={() => step(1)}
        disabled={!matches.length}
        aria-label="Next match"
        title="Next match (Enter)"
        className="inline-flex size-7 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close find bar"
        title="Close (Esc)"
        className="inline-flex size-7 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink"
      >
        <X size={15} aria-hidden="true" />
      </button>
    </div>
  );
}
