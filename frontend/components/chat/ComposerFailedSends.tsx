"use client";

import { AlertCircle, Pencil, RotateCcw, X } from "lucide-react";
import type { FailedSend } from "@/lib/failed-sends";

export function ComposerFailedSends({
  items,
  retryingId,
  disabled = false,
  onRetry,
  onEdit,
  onDismiss,
}: {
  items: readonly FailedSend[];
  retryingId: string | null;
  disabled?: boolean;
  onRetry: (item: FailedSend) => void;
  onEdit: (item: FailedSend) => void;
  onDismiss: (id: string) => void;
}) {
  if (items.length === 0) return null;

  return (
    <div
      className="mx-auto mb-1.5 w-full max-w-3xl px-3 sm:px-4"
      role="region"
      aria-label="Failed messages"
    >
      <ul className="space-y-1.5">
        {items.map((item) => {
          const busy = retryingId === item.id;
          return (
            <li
              key={item.id}
              className="flex items-start gap-2.5 rounded-xl border border-danger/35 bg-danger/10 px-3 py-2 text-xs text-ink"
            >
              <AlertCircle
                size={15}
                className="mt-0.5 shrink-0 text-danger"
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-ink" title={item.displayText}>
                  {item.displayText}
                </p>
                <p className="mt-0.5 text-danger">{item.error}</p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  disabled={disabled || busy}
                  onClick={() => onRetry(item)}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-field border border-line bg-raised px-2 py-1 text-xs font-medium text-ink hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RotateCcw size={12} className={busy ? "animate-spin" : undefined} aria-hidden="true" />
                  <span>{busy ? "Retrying…" : "Retry"}</span>
                </button>
                <button
                  type="button"
                  disabled={disabled || busy}
                  onClick={() => onEdit(item)}
                  aria-label="Edit failed message"
                  title="Edit in composer"
                  className="inline-flex size-7 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Pencil size={13} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onDismiss(item.id)}
                  aria-label="Dismiss failed message"
                  title="Dismiss"
                  className="inline-flex size-7 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
