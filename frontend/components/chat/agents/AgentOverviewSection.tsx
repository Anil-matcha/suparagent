"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type AgentOverview } from "@/lib/api";
import type { EditorSection } from "./AgentSettingsSections";
import { PromptPreview } from "./PromptPreview";

export function AgentOverviewSection({
  agentId,
  onNavigate,
}: {
  agentId: number;
  onNavigate: (section: EditorSection) => void;
}) {
  const [overview, setOverview] = useState<AgentOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAgentOverview(agentId)
      .then((res) => {
        if (!cancelled) {
          setOverview(res);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load overview.");
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  if (error) {
    return <p className="text-xs text-danger">{error}</p>;
  }
  if (!overview) {
    return <p className="text-xs text-muted">Loading overview…</p>;
  }

  const { summary, setup, who } = overview;

  return (
    <div className="space-y-4">
      <div className="rounded-field border border-line p-3 text-xs">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="text-sm font-medium text-ink">{who.name}</h3>
            {who.title && <p className="text-muted">{who.title}</p>}
          </div>
          <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
            {summary.provider_label}
            {summary.model ? ` · ${summary.model}` : ""}
          </span>
        </div>
        {who.instructions_lead && (
          <p className="mt-2 line-clamp-2 text-muted">{who.instructions_lead}</p>
        )}
        <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-2 sm:grid-cols-4">
          <div>
            <dt className="text-muted">Approvals</dt>
            <dd className="font-medium text-ink">{summary.approval_mode}</dd>
          </div>
          <div>
            <dt className="text-muted">Skills</dt>
            <dd className="font-medium text-ink">{summary.skills_count}</dd>
          </div>
          <div>
            <dt className="text-muted">Apps</dt>
            <dd className="font-medium text-ink">{summary.apps_count}</dd>
          </div>
          <div>
            <dt className="text-muted">Routines / Revs</dt>
            <dd className="font-medium text-ink">
              {summary.routines_count} / {summary.revisions_count}
            </dd>
          </div>
        </dl>
        <p className="mt-2 truncate font-mono text-[11px] text-muted">
          Workspace: {summary.workspace_path || "Isolated per-agent default"}
        </p>
      </div>

      <div className="rounded-field border border-line p-3 text-xs">
        <p className="mb-2 font-medium text-ink">Setup checklist</p>
        <ul className="space-y-1.5">
          {setup.map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-2">
              <span className={item.done ? "text-ink" : "text-muted"}>
                {item.done ? "✓ " : "○ "}
                {item.label}
              </span>
              <button
                type="button"
                onClick={() => onNavigate(item.section as EditorSection)}
                className="cursor-pointer text-[11px] font-medium text-accent hover:underline"
              >
                {item.done ? "Review" : "Set up"}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <PromptPreview agentId={agentId} />
    </div>
  );
}
