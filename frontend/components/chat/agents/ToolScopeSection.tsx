"use client";

import { useState } from "react";
import type { ToolScope } from "@/lib/api";

const TOOL_GROUPS = [
  { id: "images", label: "Image generation", tokens: 450, help: "Disable built-in image generation and editing tools." },
  { id: "speech", label: "Voice & audio", tokens: 350, help: "Disable voice synthesis / audio output tools." },
  { id: "computers", label: "Browser & remote computers", tokens: 600, help: "Disable browser / desktop computer toolsets for text-only tasks." },
  { id: "memory_tools", label: "Past conversation recall tools", tokens: 400, help: "Disable session_search and session_read MCP lookup tools." },
];

export function ToolScopeSection({
  toolScope,
  toolsOff,
  disabled,
  onToolScopeChange,
  onToolsOffChange,
}: {
  toolScope: ToolScope;
  toolsOff: string[];
  disabled?: boolean;
  onToolScopeChange: (next: ToolScope) => void;
  onToolsOffChange: (next: string[]) => void;
}) {
  const [allowInput, setAllowInput] = useState("");
  const [denyInput, setDenyInput] = useState("");
  const [ruleError, setRuleError] = useState<string | null>(null);

  const allowList = toolScope?.allow ?? [];
  const denyList = toolScope?.deny ?? [];

  const updateLists = (allow: string[], deny: string[]) => {
    const cleanAllow = allow.filter(Boolean);
    const cleanDeny = deny.filter(Boolean);
    if (cleanAllow.length === 0 && cleanDeny.length === 0) {
      onToolScopeChange(null);
      return;
    }
    const next: { allow?: string[]; deny?: string[] } = {};
    if (cleanAllow.length > 0) next.allow = cleanAllow;
    if (cleanDeny.length > 0) next.deny = cleanDeny;
    onToolScopeChange(next);
  };

  const addRule = (kind: "allow" | "deny", raw: string) => {
    const pattern = raw.trim();
    if (!pattern) return;
    if (!/^(native|mcp):[A-Za-z0-9_.:*-]{1,120}$/.test(pattern)) {
      setRuleError("Rules must look like native:Bash or mcp:gmail:*");
      return;
    }
    setRuleError(null);
    if (kind === "allow") {
      if (!allowList.includes(pattern)) updateLists([...allowList, pattern], denyList);
      setAllowInput("");
    } else {
      if (!denyList.includes(pattern)) updateLists(allowList, [...denyList, pattern]);
      setDenyInput("");
    }
  };

  const toggleGroup = (id: string) => {
    if (toolsOff.includes(id)) {
      onToolsOffChange(toolsOff.filter((g) => g !== id));
    } else {
      onToolsOffChange([...toolsOff, id]);
    }
  };

  return (
    <div className="space-y-4">
      <fieldset className="space-y-2 rounded-field border border-line p-3 text-xs">
        <legend className="px-1 text-sm font-medium text-ink">Tool scope allow / deny</legend>
        <p className="text-muted">
          Restrict tools using <code className="font-mono">native:&lt;tool&gt;</code> or{" "}
          <code className="font-mono">mcp:&lt;app&gt;:&lt;tool&gt;</code> patterns (supports{" "}
          <code className="font-mono">*</code> wildcards). Deny rules always win.
        </p>

        <div className="space-y-2 pt-1">
          <div className="flex gap-2">
            <input
              value={allowInput}
              disabled={disabled}
              onChange={(e) => setAllowInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addRule("allow", allowInput);
                }
              }}
              placeholder="Allow pattern, e.g. native:Read or mcp:gmail:*"
              className="min-w-0 flex-1 rounded-md border border-line bg-transparent px-2.5 py-1 font-mono text-xs placeholder:text-muted"
            />
            <button
              type="button"
              disabled={disabled || !allowInput.trim()}
              onClick={() => addRule("allow", allowInput)}
              className="cursor-pointer rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-hover disabled:opacity-50"
            >
              Add allow
            </button>
          </div>
          {allowList.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {allowList.map((rule) => (
                <span key={rule} className="inline-flex items-center gap-1 rounded-full border border-line bg-inset px-2 py-0.5 font-mono text-[11px] text-ink">
                  +{rule}
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`Remove allow rule ${rule}`}
                    onClick={() => updateLists(allowList.filter((r) => r !== rule), denyList)}
                    className="cursor-pointer text-muted hover:text-ink"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <input
              value={denyInput}
              disabled={disabled}
              onChange={(e) => setDenyInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addRule("deny", denyInput);
                }
              }}
              placeholder="Deny pattern, e.g. native:Bash or mcp:slack:*"
              className="min-w-0 flex-1 rounded-md border border-line bg-transparent px-2.5 py-1 font-mono text-xs placeholder:text-muted"
            />
            <button
              type="button"
              disabled={disabled || !denyInput.trim()}
              onClick={() => addRule("deny", denyInput)}
              className="cursor-pointer rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:bg-hover disabled:opacity-50"
            >
              Add deny
            </button>
          </div>
          {denyList.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {denyList.map((rule) => (
                <span key={rule} className="inline-flex items-center gap-1 rounded-full border border-danger/40 bg-danger/10 px-2 py-0.5 font-mono text-[11px] text-danger">
                  -{rule}
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`Remove deny rule ${rule}`}
                    onClick={() => updateLists(allowList, denyList.filter((r) => r !== rule))}
                    className="cursor-pointer hover:opacity-75"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {ruleError && <p className="text-danger">{ruleError}</p>}
        </div>
      </fieldset>

      <fieldset className="space-y-2 rounded-field border border-line p-3 text-xs">
        <legend className="px-1 text-sm font-medium text-ink">Optional tool groups disabled</legend>
        <p className="text-muted">Turn off unused built-in capabilities to keep prompts leaner.</p>
        <div className="space-y-1.5">
          {TOOL_GROUPS.map((group) => (
            <label key={group.id} className="flex cursor-pointer items-start gap-2 rounded-md border border-line p-2">
              <input
                type="checkbox"
                checked={toolsOff.includes(group.id)}
                disabled={disabled}
                onChange={() => toggleGroup(group.id)}
                className="mt-0.5"
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-center justify-between gap-2 font-medium text-ink">
                  <span>Disable {group.label}</span>
                  <span className="font-mono text-[11px] text-muted">~{group.tokens} tok saved</span>
                </span>
                <span className="block text-muted">{group.help}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
