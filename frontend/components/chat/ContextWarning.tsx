"use client";

import { AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { formatTokens } from "@/lib/everyday";
import type { ContextWindowStatus } from "@/lib/context-window";

export function ContextWarning({
  status,
  onNewChat,
  onDismiss,
}: {
  status: ContextWindowStatus;
  onNewChat?: () => void;
  onDismiss?: () => void;
}) {
  if (status.severity === "none") return null;
  const critical = status.severity === "critical";

  return (
    <div
      className="mx-auto mb-1.5 w-full max-w-3xl px-3 sm:px-4"
      role="status"
      aria-live="polite"
    >
      <div
        className={cn(
          "flex flex-wrap items-center gap-2 rounded-xl border px-3 py-1.5 text-xs",
          critical
            ? "border-danger/40 bg-danger/10 text-ink"
            : "border-warning/40 bg-warning/10 text-ink",
        )}
      >
        <AlertTriangle
          size={14}
          className={cn("shrink-0", critical ? "text-danger" : "text-warning")}
          aria-hidden="true"
        />
        <div
          aria-hidden="true"
          className="h-1.5 w-14 shrink-0 overflow-hidden rounded-full bg-inset"
        >
          <div
            className={cn("h-full rounded-full", critical ? "bg-danger" : "bg-warning")}
            style={{ width: `${status.percent}%` }}
          />
        </div>
        <p className="min-w-0 flex-1 text-ink">
          <span className="font-medium">
            {critical ? `Context nearly full (${status.percent}%)` : `Context ${status.percent}% full`}
          </span>
          <span className="text-muted">
            {" · "}
            {formatTokens(status.usedTokens)} / {formatTokens(status.limitTokens)} tokens
            {critical
              ? ". Start a new chat soon to avoid losing earlier context."
              : ". Starting a new chat keeps replies fast and accurate."}
          </span>
        </p>
        {onNewChat && (
          <button
            type="button"
            onClick={onNewChat}
            className="shrink-0 cursor-pointer rounded-field border border-line bg-raised px-2 py-0.5 text-xs font-medium text-ink hover:bg-hover"
          >
            New chat
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss context warning"
            title="Dismiss"
            className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink"
          >
            <X size={13} aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}
