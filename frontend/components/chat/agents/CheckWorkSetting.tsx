"use client";

import { Select } from "@/components/ui/Select";
import type { Agent, CheckWorkConfig } from "@/lib/api";

export function CheckWorkSetting({
  value,
  agents,
  currentAgentId,
  disabled,
  onChange,
}: {
  value: CheckWorkConfig;
  agents: Agent[];
  currentAgentId: number | null;
  disabled?: boolean;
  onChange: (next: CheckWorkConfig) => void;
}) {
  const enabled = Boolean(value?.enabled);
  const checkerId = value?.checker_agent_id ?? null;
  const candidates = agents.filter((a) => !a.archived_at && a.id !== currentAgentId);

  return (
    <fieldset className="space-y-2 rounded-field border border-line p-3 text-xs">
      <legend className="px-1 text-sm font-medium text-ink">Post-turn verification (Check work)</legend>
      <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => {
            const on = e.target.checked;
            onChange(on ? { enabled: true, checker_agent_id: checkerId } : null);
          }}
        />
        Have another agent run a brief review after completed turns
      </label>
      {enabled && (
        <label className="block text-xs text-muted">
          Checker agent
          <Select
            aria-label="Checker agent"
            value={checkerId !== null ? String(checkerId) : ""}
            disabled={disabled}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null;
              onChange({ enabled: true, checker_agent_id: id });
            }}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
          >
            <option value="">Choose an agent…</option>
            {candidates.map((candidate) => (
              <option key={candidate.id} value={String(candidate.id)}>
                {candidate.name}
              </option>
            ))}
          </Select>
        </label>
      )}
      <p className="text-muted">Runs a single non-blocking verification pass and records a review note on the turn.</p>
    </fieldset>
  );
}
