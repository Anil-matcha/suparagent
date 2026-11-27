"use client";

import type { OutboundPolicy } from "@/lib/api";

export function OutboundPolicyControl({
  value,
  disabled,
  onChange,
}: {
  value: OutboundPolicy;
  disabled?: boolean;
  onChange: (next: OutboundPolicy) => void;
}) {
  return (
    <fieldset className="space-y-2 rounded-field border border-line p-3 text-xs">
      <legend className="px-1 text-sm font-medium text-ink">Outbound external actions</legend>
      <p className="text-muted">
        Controls whether curated outbound app actions (such as sending emails or posting messages on connected apps) ask you first or run without a card when the app already has write access.
      </p>
      <div className="flex flex-wrap gap-2">
        {(
          [
            { id: "ask", label: "Ask first (recommended)" },
            { id: "allow", label: "Allow automatically" },
          ] as const
        ).map((option) => (
          <label
            key={option.id}
            className={`cursor-pointer rounded-md border px-3 py-1.5 text-xs ${
              value === option.id ? "border-accent bg-inset text-ink" : "border-line text-muted"
            }`}
          >
            <input
              type="radio"
              name="outbound-policy"
              className="sr-only"
              checked={value === option.id}
              disabled={disabled}
              onChange={() => onChange(option.id)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
