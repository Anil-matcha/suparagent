"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  Check,
  ChevronDown,
  Download,
  KeyRound,
  LogIn,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { api, type Engine, type EngineModel, type EngineUsageReport } from "@/lib/api";
import { cn } from "@/lib/cn";

export interface ModelPickerProps {
  engines: Engine[];
  selectedProvider: string;
  selectedModel: string | null;
  selectedEffort?: string | null;
  onChange: (provider: string, model: string | null, effort?: string | null) => void;
  disabled?: boolean;
  className?: string;
}

export function EngineMark({ engineId, size = 16, className }: { engineId: string; size?: number; className?: string }) {
  const id = engineId.toLowerCase();
  if (id.includes("antigravity") || id.includes("google") || id.includes("gemini")) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" className={cn("fill-current text-blue-400 shrink-0", className)} aria-hidden>
        <path
          fillRule="evenodd"
          d="M21.751 22.607c1.34 1.005 3.35.335 1.508-1.508C17.73 15.74 18.904 1 12.037 1 5.17 1 6.342 15.74.815 21.1c-2.01 2.009.167 2.511 1.507 1.506 5.192-3.517 4.857-9.714 9.715-9.714 4.857 0 4.522 6.197 9.714 9.715z"
        />
      </svg>
    );
  }
  if (id.includes("claude") || id.includes("anthropic")) {
    return (
      <svg width={size} height={size} viewBox="0 0 256 257" preserveAspectRatio="xMidYMid" className={cn("fill-[#d97757] shrink-0", className)} aria-hidden>
        <path d="m50.228 170.321 50.357-28.257.843-2.463-.843-1.361h-2.462l-8.426-.518-28.775-.778-24.952-1.037-24.175-1.296-6.092-1.297L0 125.796l.583-3.759 5.12-3.434 7.324.648 16.202 1.101 24.304 1.685 17.629 1.037 26.118 2.722h4.148l.583-1.685-1.426-1.037-1.101-1.037-25.147-17.045-27.22-18.017-14.258-10.37-7.713-5.25-3.888-4.925-1.685-10.758 7-7.713 9.397.649 2.398.648 9.527 7.323 20.35 15.75L94.817 91.9l3.889 3.24 1.555-1.102.195-.777-1.75-2.917-14.453-26.118-15.425-26.572-6.87-11.018-1.814-6.61c-.648-2.723-1.102-4.991-1.102-7.778l7.972-10.823L71.42 0 82.05 1.426l4.472 3.888 6.61 15.101 10.694 23.786 16.591 32.34 4.861 9.592 2.592 8.879.973 2.722h1.685v-1.556l1.36-18.211 2.528-22.36 2.463-28.776.843-8.1 4.018-9.722 7.971-5.25 6.222 2.981 5.12 7.324-.713 4.73-3.046 19.768-5.962 30.98-3.889 20.739h2.268l2.593-2.593 10.499-13.934 17.628-22.036 7.778-8.749 9.073-9.657 5.833-4.601h11.018l8.1 12.055-3.628 12.443-11.342 14.388-9.398 12.184-13.48 18.147-8.426 14.518.778 1.166 2.01-.194 30.46-6.481 16.462-2.982 19.637-3.37 8.88 4.148.971 4.213-3.5 8.62-20.998 5.184-24.628 4.926-36.682 8.685-.454.324.519.648 16.526 1.555 7.065.389h17.304l32.21 2.398 8.426 5.574 5.055 6.805-.843 5.184-12.962 6.611-17.498-4.148-40.83-9.721-14-3.5h-1.944v1.167l11.666 11.406 21.387 19.314 26.767 24.887 1.36 6.157-3.434 4.86-3.63-.518-23.526-17.693-9.073-7.972-20.545-17.304h-1.36v1.814l4.73 6.935 25.017 37.59 1.296 11.536-1.814 3.76-6.481 2.268-7.13-1.297-14.647-20.544-15.1-23.138-12.185-20.739-1.49.843-7.194 77.448-3.37 3.953-7.778 2.981-6.48-4.925-3.436-7.972 3.435-15.749 4.148-20.544 3.37-16.333 3.046-20.285 1.815-6.74-.13-.454-1.49.194-15.295 20.999-23.267 31.433-18.406 19.702-4.407 1.75-7.648-3.954.713-7.064 4.277-6.286 25.47-32.405 15.36-20.092 9.917-11.6-.065-1.686h-.583L44.07 198.125l-12.055 1.555-5.185-4.86.648-7.972 2.463-2.593 20.35-13.999-.064.065Z" />
      </svg>
    );
  }
  if (id.includes("codex") || id.includes("openai")) {
    return (
      <svg width={size} height={size} viewBox="0 0 256 260" preserveAspectRatio="xMidYMid" className={cn("fill-current text-emerald-400 shrink-0", className)} aria-hidden>
        <path d="M239.184 106.203a64.716 64.716 0 0 0-5.576-53.103C219.452 28.459 191 15.784 163.213 21.74A65.586 65.586 0 0 0 52.096 45.22a64.716 64.716 0 0 0-43.23 31.36c-14.31 24.602-11.061 55.634 8.033 76.74a64.665 64.665 0 0 0 5.525 53.102c14.174 24.65 42.644 37.324 70.446 31.36a64.72 64.72 0 0 0 48.754 21.744c28.481.025 53.714-18.361 62.414-45.481a64.767 64.767 0 0 0 43.229-31.36c14.137-24.558 10.875-55.423-8.083-76.483Zm-97.56 136.338a48.397 48.397 0 0 1-31.105-11.255l1.535-.87 51.67-29.825a8.595 8.595 0 0 0 4.247-7.367v-72.85l21.845 12.636c.218.111.37.32.409.563v60.367c-.056 26.818-21.783 48.545-48.601 48.601Zm-104.466-44.61a48.345 48.345 0 0 1-5.781-32.589l1.534.921 51.722 29.826a8.339 8.339 0 0 0 8.441 0l63.181-36.425v25.221a.87.87 0 0 1-.358.665l-52.335 30.184c-23.257 13.398-52.97 5.431-66.404-17.803ZM23.549 85.38a48.499 48.499 0 0 1 25.58-21.333v61.39a8.288 8.288 0 0 0 4.195 7.316l62.874 36.272-21.845 12.636a.819.819 0 0 1-.767 0L41.353 151.53c-23.211-13.454-31.171-43.144-17.804-66.405v.256Zm179.466 41.695-63.08-36.63L161.73 77.86a.819.819 0 0 1 .768 0l52.233 30.184a48.6 48.6 0 0 1-7.316 87.635v-61.391a8.544 8.544 0 0 0-4.4-7.213Zm21.742-32.69-1.535-.922-51.619-30.081a8.39 8.39 0 0 0-8.492 0L99.98 99.808V74.587a.716.716 0 0 1 .307-.665l52.233-30.133a48.652 48.652 0 0 1 72.236 50.391v.205ZM88.061 139.097l-21.845-12.585a.87.87 0 0 1-.41-.614V65.685a48.652 48.652 0 0 1 79.757-37.346l-1.535.87-51.67 29.825a8.595 8.595 0 0 0-4.246 7.367l-.051 72.697Zm11.868-25.58 28.138-16.217 28.188 16.218v32.434l-28.086 16.218-28.188-16.218-.052-32.434Z" />
      </svg>
    );
  }
  return <Sparkles size={size} className={cn("text-accent shrink-0", className)} aria-hidden />;
}

export function UsageGlyph({
  leftPercent,
  tone,
  className,
}: {
  leftPercent: number;
  tone: "fine" | "near" | "out";
  className?: string;
}) {
  const filled = leftPercent <= 0 ? 0 : leftPercent <= 20 ? 1 : leftPercent <= 40 ? 2 : leftPercent <= 70 ? 3 : 4;
  const color = tone === "out" ? "text-red-500" : tone === "near" ? "text-amber-500" : "text-muted group-hover:text-ink";
  return (
    <svg width="14" height="12" viewBox="0 0 14 12" className={cn("shrink-0", color, className)} aria-hidden="true">
      <rect x="0" y="8" width="2" height="4" rx="0.5" fill="currentColor" opacity={filled >= 1 ? 1 : 0.25} />
      <rect x="4" y="5.5" width="2" height="6.5" rx="0.5" fill="currentColor" opacity={filled >= 2 ? 1 : 0.25} />
      <rect x="8" y="3" width="2" height="9" rx="0.5" fill="currentColor" opacity={filled >= 3 ? 1 : 0.25} />
      <rect x="12" y="0" width="2" height="12" rx="0.5" fill="currentColor" opacity={filled >= 4 ? 1 : 0.25} />
      {tone === "out" && <path d="M1 11L13 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />}
    </svg>
  );
}

function formatResetTime(isoString: string): string {
  try {
    const at = Date.parse(isoString);
    if (!Number.isFinite(at)) return isoString;
    const date = new Date(at);
    const now = new Date();
    const isSameDay = date.toDateString() === now.toDateString();
    return date.toLocaleTimeString(undefined, isSameDay ? { hour: "numeric", minute: "2-digit" } : { weekday: "short", hour: "numeric", minute: "2-digit" });
  } catch {
    return isoString;
  }
}

export const EFFORT_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra",
  max: "Max",
  ultra: "Ultra",
};

export function effortName(effort: string | null | undefined): string {
  if (!effort) return "Default";
  return EFFORT_LABELS[effort.toLowerCase()] ?? (effort.charAt(0).toUpperCase() + effort.slice(1));
}

export function ModelPicker({
  engines,
  selectedProvider,
  selectedModel,
  selectedEffort,
  onChange,
  disabled = false,
  className,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [activeRailEngineId, setActiveRailEngineId] = useState<string>(selectedProvider);
  const [usageReport, setUsageReport] = useState<EngineUsageReport | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [threadScope, setThreadScope] = useState<"thread" | "default">("thread");

  const popoverRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Sync active rail provider with selected provider
  useEffect(() => {
    setActiveRailEngineId(selectedProvider);
  }, [selectedProvider]);

  // Selected engine & model details
  const activeEngine = engines.find((e) => e.id === selectedProvider) ?? engines[0];
  const activeModelId = selectedModel ?? activeEngine?.default_model ?? activeEngine?.models[0]?.id ?? null;
  const activeModelObj = activeEngine?.models.find((m) => m.id === activeModelId);

  // The engine currently viewed in the right pane (from left rail selection)
  const viewedEngine = engines.find((e) => e.id === activeRailEngineId) ?? activeEngine;

  // Real usage limits: query for active rail engine or selected provider
  const fetchUsageLimits = () => {
    api.getEngineUsageLimits(selectedProvider)
      .then((rep) => setUsageReport(rep))
      .catch(() => setUsageReport(null));
  };

  useEffect(() => {
    fetchUsageLimits();
  }, [selectedProvider, open]);

  const handleRefreshEngines = async () => {
    setRefreshing(true);
    try {
      await api.getEngines(true);
      fetchUsageLimits();
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  };

  // Click outside to close
  useEffect(() => {
    if (!open) return;
    const handleDown = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleDown);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleDown);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  // Focus search input when popover opens
  useEffect(() => {
    if (open) {
      setTimeout(() => searchInputRef.current?.focus(), 50);
    } else {
      setSearch("");
    }
  }, [open]);

  // Pill Title & Short Label for trigger button
  const currentEffort = selectedEffort ?? activeModelObj?.default_reasoning_effort ?? null;
  const pillTitle = useMemo(() => {
    const engineName = activeEngine?.label ?? selectedProvider;
    const modelName = activeModelObj?.label ?? (selectedModel ? selectedModel : "Default");
    const effortPart = currentEffort ? ` · ${effortName(currentEffort)}` : "";
    return `${engineName} · ${modelName}${effortPart}`;
  }, [activeEngine, selectedProvider, activeModelObj, selectedModel, currentEffort]);

  const pillShortName = useMemo(() => {
    const modelName = activeModelObj?.label ?? (selectedModel ? selectedModel : activeEngine?.label ?? "Model");
    const effortPart = currentEffort ? ` · ${effortName(currentEffort)}` : "";
    return `${modelName}${effortPart}`;
  }, [activeModelObj, selectedModel, activeEngine, currentEffort]);

  // Generate model variant choices (modelChoices architecture)
  // Each model with reasoning efforts is expanded into separate rows e.g. "Gemini 4 Hydrogen · High"
  type Choice = {
    engineId: string;
    modelId: string;
    modelLabel: string;
    variantId?: string;
    displayLabel: string;
    isCurrent: boolean;
  };

  const choices: Choice[] = useMemo(() => {
    if (!viewedEngine || !viewedEngine.models) return [];
    const list: Choice[] = [];
    const isThisEngineSelected = viewedEngine.id === selectedProvider;

    for (const m of viewedEngine.models) {
      const efforts = m.supported_reasoning_efforts;
      if (efforts && efforts.length > 0 && m.reasoning_effort_configurable !== false) {
        for (const eff of efforts) {
          const isCurrent =
            isThisEngineSelected &&
            (selectedModel === m.id || (!selectedModel && m.id === viewedEngine.default_model)) &&
            (currentEffort?.toLowerCase() === eff.toLowerCase() || (!currentEffort && eff === m.default_reasoning_effort));
          list.push({
            engineId: viewedEngine.id,
            modelId: m.id,
            modelLabel: m.label,
            variantId: eff,
            displayLabel: `${m.label} · ${effortName(eff)}`,
            isCurrent,
          });
        }
      } else {
        const isCurrent =
          isThisEngineSelected &&
          (selectedModel === m.id || (!selectedModel && m.id === viewedEngine.default_model));
        list.push({
          engineId: viewedEngine.id,
          modelId: m.id,
          modelLabel: m.label,
          displayLabel: m.label,
          isCurrent,
        });
      }
    }
    return list;
  }, [viewedEngine, selectedProvider, selectedModel, currentEffort]);

  const filteredChoices = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return choices;
    return choices.filter(
      (c) =>
        c.displayLabel.toLowerCase().includes(q) ||
        c.modelId.toLowerCase().includes(q) ||
        c.modelLabel.toLowerCase().includes(q)
    );
  }, [choices, search]);

  // Currently selected model's available reasoning efforts for the bottom dropdown
  const activeModelEfforts = useMemo(() => {
    if (!activeModelObj?.supported_reasoning_efforts || activeModelObj.supported_reasoning_efforts.length <= 1) {
      return [];
    }
    return activeModelObj.supported_reasoning_efforts;
  }, [activeModelObj]);

  const handleSelectChoice = (choice: Choice) => {
    onChange(choice.engineId, choice.modelId, choice.variantId ?? null);
    setOpen(false);
  };

  const handleEffortDropdownChange = (newEffort: string) => {
    onChange(selectedProvider, selectedModel, newEffort);
  };

  // Rail engines categorized
  const cloudEngines = useMemo(() => {
    return engines.filter(
      (e) =>
        e.id === "openai_compat" ||
        e.id === "claude" ||
        e.id === "antigravity" ||
        e.id === "gemini" ||
        e.id === "codex" ||
        e.id === "cursor"
    );
  }, [engines]);

  return (
    <div ref={popoverRef} className="relative inline-block text-left">
      {/* Trigger Pill Button in Composer Footer */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-label={`Model: ${pillTitle}`}
        title={pillTitle}
        className={cn(
          "group inline-flex h-8 max-w-[220px] sm:max-w-[300px] shrink-0 items-center gap-1.5 rounded-full border border-line bg-raised/70 px-2.5 text-[13px] font-medium text-ink transition-colors duration-150 hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 select-none shadow-xs cursor-pointer",
          open && "ring-1 ring-accent/60 bg-hover text-ink",
          className
        )}
      >
        <span className="flex size-4 shrink-0 items-center justify-center">
          <EngineMark engineId={selectedProvider} size={14} />
        </span>
        <span className="truncate">{pillShortName}</span>
        {usageReport && (
          <UsageGlyph
            leftPercent={usageReport.primary_left_percent}
            tone={usageReport.tone}
            className="ms-0.5"
          />
        )}
        <ChevronDown
          size={12}
          strokeWidth={2}
          className={cn("shrink-0 text-muted transition-transform duration-200 group-hover:text-ink", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>

      {/* Upward Dropdown Popover */}
      {open && (
        <div
          role="dialog"
          aria-label="Model picker"
          className="absolute bottom-full left-0 mb-2 w-[340px] sm:w-[380px] max-h-[580px] origin-bottom-left rounded-2xl border border-line bg-raised shadow-2xl backdrop-blur-md z-50 flex flex-col overflow-hidden text-xs text-ink transition-all duration-150 animate-in fade-in zoom-in-95"
        >
          {/* Top Section: Real Usage Limits Card */}
          {usageReport && usageReport.windows.length > 0 && (
            <div className="border-b border-line/60 bg-inset/30 p-3 shrink-0">
              <div className="flex items-baseline justify-between gap-2 mb-2">
                <span className="text-[13px] font-semibold text-ink">{usageReport.name} usage</span>
                <span className="text-[11px] text-muted">checked just now</span>
              </div>
              <div className="flex flex-col gap-2.5">
                {usageReport.windows.slice(0, 2).map((win) => (
                  <div key={win.name} className="flex flex-col gap-1">
                    <div className="flex items-baseline justify-between text-[11px]">
                      <span className="text-muted font-medium">{win.name}</span>
                      <span className="tabular-nums text-muted">
                        <span className="font-semibold text-ink">{win.left_percent}% left</span>
                        {win.resets_at && ` · resets ${formatResetTime(win.resets_at)}`}
                      </span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink/[0.08]">
                      <div
                        className={cn(
                          "h-full rounded-full transition-all duration-300",
                          win.left_percent <= 10
                            ? "bg-red-500"
                            : win.left_percent <= 25
                              ? "bg-amber-500"
                              : "bg-ink-tertiary/70"
                        )}
                        style={{ width: `${100 - win.left_percent}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Thread Scope Toggle */}
          <div className="border-b border-line/60 bg-inset/15 px-3 py-2 shrink-0">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setThreadScope("thread")}
                className={cn(
                  "rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors cursor-pointer",
                  threadScope === "thread"
                    ? "bg-ink/[0.12] text-ink font-semibold"
                    : "text-muted hover:text-ink"
                )}
              >
                Only this thread
              </button>
              <button
                type="button"
                onClick={() => setThreadScope("default")}
                className={cn(
                  "rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors cursor-pointer",
                  threadScope === "default"
                    ? "bg-ink/[0.12] text-ink font-semibold"
                    : "text-muted hover:text-ink"
                )}
              >
                Thread + default
              </button>
            </div>
            <p className="mt-1 text-[11px] text-muted">
              {threadScope === "thread"
                ? "This thread only. It keeps this model when the default model changes."
                : "Applies to this thread and becomes the default for new threads."}
            </p>
          </div>

          {/* Main 2-Column Split: Left Rail + Right Content */}
          <div className="flex flex-1 min-h-[280px] max-h-[360px] overflow-hidden">
            {/* Left Vertical Provider Rail */}
            <div className="flex w-12 shrink-0 flex-col items-center gap-1.5 border-r border-line/60 bg-inset/20 p-1.5 overflow-y-auto">
              <span className="text-[9px] uppercase tracking-wider text-muted font-semibold mt-1">Cloud</span>
              {cloudEngines.map((eng) => {
                const isViewed = eng.id === viewedEngine.id;
                const hasDot = !eng.available || (eng.installed && !eng.available);
                return (
                  <button
                    key={eng.id}
                    type="button"
                    onClick={() => setActiveRailEngineId(eng.id)}
                    title={eng.label}
                    className={cn(
                      "relative flex size-8 shrink-0 items-center justify-center rounded-lg transition-colors cursor-pointer",
                      isViewed
                        ? "bg-ink/[0.12] ring-1 ring-line text-ink"
                        : "text-muted hover:bg-hover hover:text-ink"
                    )}
                  >
                    <EngineMark engineId={eng.id} size={16} />
                    {hasDot && (
                      <span className="absolute bottom-1 right-1 size-1.5 rounded-full bg-amber-500 ring-1 ring-raised" />
                    )}
                  </button>
                );
              })}

              <div role="separator" className="my-1 h-[0.5px] w-6 bg-line/60" />
              <span className="text-[9px] uppercase tracking-wider text-muted font-semibold">API</span>

              <Link
                href="/settings/engines"
                onClick={() => setOpen(false)}
                title="Add API keys"
                className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-ink cursor-pointer"
              >
                <KeyRound size={15} />
              </Link>
              <Link
                href="/settings/engines"
                onClick={() => setOpen(false)}
                title="Add new engine"
                className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-dashed border-line text-muted hover:bg-hover hover:text-ink cursor-pointer"
              >
                <Plus size={15} />
              </Link>
            </div>

            {/* Right Content Panel for Viewed Engine */}
            <div className="flex flex-1 flex-col overflow-hidden bg-raised">
              {/* Provider Header with Status Badge & Account */}
              <div className="flex flex-col gap-0.5 border-b border-line/60 px-3 py-2 shrink-0">
                <div className="flex items-center justify-between gap-1.5">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="truncate text-[13px] font-semibold text-ink">{viewedEngine.label}</span>
                    <button
                      type="button"
                      onClick={() => void handleRefreshEngines()}
                      disabled={refreshing}
                      title="Refresh models and status"
                      className="flex size-5 shrink-0 items-center justify-center rounded text-muted hover:text-ink cursor-pointer"
                    >
                      <RefreshCw size={12} className={cn(refreshing && "animate-spin")} />
                    </button>
                  </div>
                  {viewedEngine.available && viewedEngine.version && (
                    <span className="shrink-0 rounded-full bg-emerald-500/15 px-1.5 py-0.2 text-[10px] font-medium text-emerald-500">
                      {viewedEngine.version}
                    </span>
                  )}
                  {!viewedEngine.available && (
                    <span className="shrink-0 rounded-full bg-amber-500/15 px-1.5 py-0.2 text-[10px] font-medium text-amber-500">
                      Needs setup
                    </span>
                  )}
                </div>

                {/* Real User Email if reported */}
                {viewedEngine.id === selectedProvider && usageReport?.user_email && (
                  <span className="truncate text-[11px] text-muted">
                    {usageReport.user_email}
                  </span>
                )}
              </div>

              {/* Search Models Input */}
              <div className="flex h-8 shrink-0 items-center gap-2 border-b border-line/60 px-3 bg-inset/10">
                <Search size={14} className="shrink-0 text-muted" aria-hidden="true" />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search models"
                  className="w-full bg-transparent text-[12px] text-ink placeholder:text-muted outline-none focus:outline-none"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setSearch("")}
                    className="flex size-4 shrink-0 items-center justify-center rounded-full text-muted hover:text-ink cursor-pointer"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>

              {/* Model Choice Rows */}
              <div className="flex-1 overflow-y-auto overscroll-contain p-1.5 flex flex-col gap-0.5">
                {!search && (
                  <div className="px-2 pt-1 pb-0.5 text-[11px] font-semibold text-muted">
                    Suggested
                  </div>
                )}

                {viewedEngine.available ? (
                  filteredChoices.length === 0 ? (
                    <div className="py-6 text-center text-xs text-muted">No models match &ldquo;{search}&rdquo;</div>
                  ) : (
                    filteredChoices.map((choice) => (
                      <button
                        key={`${choice.modelId}-${choice.variantId ?? "default"}`}
                        type="button"
                        onClick={() => handleSelectChoice(choice)}
                        className={cn(
                          "group flex min-h-[30px] w-full items-center justify-between gap-2 rounded-lg px-2 py-1 text-left text-[12px] transition-colors cursor-pointer",
                          choice.isCurrent
                            ? "bg-accent/15 text-accent font-medium"
                            : "text-ink hover:bg-hover"
                        )}
                      >
                        <span className="truncate">{choice.displayLabel}</span>
                        {choice.isCurrent && (
                          <Check size={14} className="shrink-0 text-accent" aria-hidden="true" />
                        )}
                      </button>
                    ))
                  )
                ) : (
                  /* Engine Needs Setup link */
                  <Link
                    href="/settings/engines"
                    onClick={() => setOpen(false)}
                    className="group flex flex-col gap-1 rounded-lg p-2.5 text-left text-muted hover:bg-hover transition-colors cursor-pointer"
                  >
                    <div className="flex items-center gap-1.5 text-[12px] font-medium text-ink">
                      {viewedEngine.installed ? <LogIn size={14} /> : <Download size={14} />}
                      <span>{viewedEngine.installed ? `Sign in to ${viewedEngine.label}` : `Set up ${viewedEngine.label}`}</span>
                    </div>
                    <span className="text-[11px] text-muted">
                      {viewedEngine.reason ?? "Configure in settings"}
                    </span>
                  </Link>
                )}
              </div>

              {/* Reasoning Effort Dropdown Selector (Custom Radix Component) */}
              {activeModelEfforts.length > 0 && viewedEngine.id === selectedProvider && (
                <div className="border-t border-line/60 bg-inset/20 px-3 py-1.5 shrink-0 flex items-center justify-between gap-2">
                  <span className="text-[12px] text-ink font-medium">Reasoning</span>
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger asChild>
                      <button
                        type="button"
                        aria-label="Reasoning effort"
                        className="inline-flex h-7 min-w-[84px] items-center justify-between gap-2 rounded-md border border-line bg-raised px-2.5 text-[12px] font-medium text-ink hover:bg-hover transition-colors cursor-pointer outline-none focus:ring-1 focus:ring-accent"
                      >
                        <span className="truncate">{effortName(currentEffort ?? activeModelObj?.default_reasoning_effort)}</span>
                        <ChevronDown size={12} className="shrink-0 text-muted" aria-hidden="true" />
                      </button>
                    </DropdownMenu.Trigger>
                    <DropdownMenu.Portal>
                      <DropdownMenu.Content
                        align="end"
                        sideOffset={4}
                        className="z-50 min-w-[120px] rounded-xl border border-line bg-raised p-1 text-xs text-ink shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 focus:outline-none"
                      >
                        {activeModelEfforts.map((eff) => {
                          const isCurrent =
                            (currentEffort ?? activeModelObj?.default_reasoning_effort)?.toLowerCase() ===
                            eff.toLowerCase();
                          return (
                            <DropdownMenu.Item
                              key={eff}
                              onSelect={() => handleEffortDropdownChange(eff)}
                              className={cn(
                                "flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-[12px] outline-none transition-colors data-[highlighted]:bg-hover",
                                isCurrent ? "font-semibold text-accent bg-accent/10" : "text-ink"
                              )}
                            >
                              <span>{effortName(eff)}</span>
                              {isCurrent && <Check size={13} className="shrink-0 text-accent" />}
                            </DropdownMenu.Item>
                          );
                        })}
                      </DropdownMenu.Content>
                    </DropdownMenu.Portal>
                  </DropdownMenu.Root>
                </div>
              )}
            </div>
          </div>

          {/* Footer Bar */}
          <div className="border-t border-line/60 bg-inset/30 px-3 py-2 shrink-0 flex items-center justify-between text-[11px] text-muted">
            <Link
              href="/settings/engines"
              onClick={() => setOpen(false)}
              className="hover:text-ink transition-colors"
            >
              Model providers and accounts
            </Link>
            <Link
              href="/settings/engines"
              onClick={() => setOpen(false)}
              className="flex items-center gap-1 hover:text-ink transition-colors font-medium text-ink"
            >
              <KeyRound size={12} />
              <span>Add API keys</span>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
