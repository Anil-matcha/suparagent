"use client";

import { Pin, Trash2, X } from "lucide-react";
import type { PinnedMessage } from "@/lib/pins";
import { Dialog } from "@/components/ui/Dialog";
import { Select, type SelectOption } from "@/components/ui/Select";

export function PinnedMessagesBar({
  pins,
  onJump,
  onUnpin,
  onManage,
}: {
  pins: readonly PinnedMessage[];
  onJump: (itemKey: string) => void;
  onUnpin: (itemKey: string) => void;
  onManage?: () => void;
}) {
  if (pins.length === 0) return null;
  const active = pins[0]!;

  const options: SelectOption[] = [
    ...pins.map((pin) => ({
      value: pin.itemKey,
      label: (
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="shrink-0 text-xs font-medium text-muted">
            {pin.role === "user" ? "You:" : "Agent:"}
          </span>
          <span className="truncate">{pin.excerpt}</span>
        </span>
      ),
    })),
    ...(onManage
      ? [
          {
            value: "__manage__",
            label: <span className="text-xs font-medium text-accent">Manage pinned messages…</span>,
          } satisfies SelectOption,
        ]
      : []),
  ];

  return (
    <div
      aria-label="Pinned messages"
      className="flex shrink-0 items-center gap-2 border-b border-line bg-surface/90 px-3 py-1.5 text-xs sm:px-4"
    >
      <Pin size={13} className="shrink-0 text-accent" aria-hidden="true" />
      <button
        type="button"
        onClick={() => onJump(active.itemKey)}
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 truncate rounded-field px-1.5 py-0.5 text-left text-ink transition-colors hover:bg-hover"
        title={`Jump to pinned message: ${active.excerpt}`}
      >
        <span className="shrink-0 font-medium text-muted">
          {active.role === "user" ? "You:" : "Agent:"}
        </span>
        <span className="truncate">{active.excerpt}</span>
      </button>
      <Select
        aria-label="Pinned messages list"
        value="__none__"
        onChange={(event) => {
          if (event.target.value === "__manage__") {
            onManage?.();
            return;
          }
          onJump(event.target.value);
        }}
        containerClassName="w-auto shrink-0"
        triggerClassName="h-7 border-0 bg-transparent px-2 text-xs font-medium text-muted hover:border-0 hover:bg-hover hover:text-ink focus:ring-0"
        menuClassName="min-w-64 max-w-80"
        menuAlign="end"
        triggerLabel={<span>{pins.length === 1 ? "1 pinned" : `${pins.length} pinned`}</span>}
        options={options}
      />
      <button
        type="button"
        onClick={() => onUnpin(active.itemKey)}
        aria-label="Unpin latest pinned message"
        title="Unpin"
        className="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink"
      >
        <X size={13} aria-hidden="true" />
      </button>
    </div>
  );
}

export function PinnedMessagesDialog({
  open,
  onOpenChange,
  pins,
  onJump,
  onUnpin,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pins: readonly PinnedMessage[];
  onJump: (itemKey: string) => void;
  onUnpin: (itemKey: string) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Pinned messages" size="md">
      {pins.length === 0 ? (
        <p className="py-4 text-sm text-muted">
          No pinned messages in this conversation yet. Open a message&apos;s More menu to pin it.
        </p>
      ) : (
        <ul className="space-y-2">
          {pins.map((pin) => (
            <li
              key={pin.itemKey}
              className="flex items-start gap-2 rounded-field border border-line bg-surface p-2.5 text-sm"
            >
              <button
                type="button"
                onClick={() => {
                  onOpenChange(false);
                  onJump(pin.itemKey);
                }}
                className="min-w-0 flex-1 cursor-pointer text-left hover:text-accent"
              >
                <span className="mr-1.5 text-xs font-semibold text-muted">
                  {pin.role === "user" ? "You" : "Agent"}
                </span>
                <span className="break-words text-ink">{pin.excerpt}</span>
              </button>
              <button
                type="button"
                onClick={() => onUnpin(pin.itemKey)}
                aria-label="Unpin message"
                title="Unpin"
                className="inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-danger"
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
