"use client";

import type { ReplyStyle } from "@/lib/api";

export function ReplyStyleField({
  value,
  disabled,
  onChange,
}: {
  value: ReplyStyle;
  disabled?: boolean;
  onChange: (next: ReplyStyle) => void;
}) {
  return (
    <fieldset className="space-y-1.5 text-xs text-muted">
      <legend>Reply style</legend>
      <div className="flex flex-wrap gap-2">
        {(
          [
            { id: "default", label: "Default" },
            { id: "conversational", label: "Conversational" },
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
              name="reply-style"
              className="sr-only"
              checked={value === option.id}
              disabled={disabled}
              onChange={() => onChange(option.id)}
            />
            {option.label}
          </label>
        ))}
      </div>
      <p>
        {value === "conversational"
          ? "Instructs the agent to reply naturally and concisely without heavy boilerplate headings."
          : "Uses the agent's standard instructions and formatting."}
      </p>
    </fieldset>
  );
}
