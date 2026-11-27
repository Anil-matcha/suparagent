"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AgentPromptPreview } from "@/lib/api";

export function PromptPreview({ agentId }: { agentId: number }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<AgentPromptPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getAgentPromptPreview(agentId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load prompt preview.");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    if (open && !data && !loading) {
      void load();
    }
  }, [open, data, loading, load]);

  return (
    <div className="rounded-field border border-line bg-surface/40 p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium text-ink">System prompt preview</p>
          <p className="text-muted">
            {data
              ? `${data.total_bytes.toLocaleString()} bytes (~${data.approx_tokens.toLocaleString()} tokens) across ${data.sections.length} section${data.sections.length === 1 ? "" : "s"}`
              : "Inspect exact instructions, memory, workspace context, and active skills loaded each turn."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {open && (
            <button
              type="button"
              disabled={loading}
              onClick={() => void load()}
              className="cursor-pointer rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:opacity-50"
            >
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="cursor-pointer rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-hover"
          >
            {open ? "Hide preview" : "Preview prompt"}
          </button>
        </div>
      </div>

      {open && (
        <div className="mt-3 space-y-2 border-t border-line pt-3">
          {error && <p className="text-danger">{error}</p>}
          {data && data.sections.length === 0 && (
            <p className="text-muted">No standing prompt sections are currently active for this agent.</p>
          )}
          {data?.sections.map((section) => (
            <details key={section.id} className="rounded-md border border-line bg-bg/50 p-2" open={section.id === "instructions"}>
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 font-medium text-ink">
                <span>{section.label}</span>
                <span className="font-mono text-[11px] text-muted">{section.bytes.toLocaleString()} B</span>
              </summary>
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-muted">
                {section.text}
              </pre>
            </details>
          ))}
          {data && <p className="text-[11px] text-muted">{data.note}</p>}
        </div>
      )}
    </div>
  );
}
