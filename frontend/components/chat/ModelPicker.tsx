"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Download,
  Lightbulb,
  LogIn,
  Search,
  Settings,
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

export function EngineMark({ engineId, size = 14, className }: { engineId: string; size?: number; className?: string }) {
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
  if (id.includes("cursor")) {
    return (
      <svg width={size} height={size} viewBox="0 0 466.73 532.09" className={cn("fill-current text-sky-400 shrink-0", className)} aria-hidden>
        <path d="M457.43,125.94L244.42,2.96c-6.84-3.95-15.28-3.95-22.12,0L9.3,125.94c-5.75,3.32-9.3,9.46-9.3,16.11v247.99c0,6.65,3.55,12.79,9.3,16.11l213.01,122.98c6.84,3.95,15.28,3.95,22.12,0l213.01-122.98c5.75-3.32,9.3-9.46,9.3-16.11v-247.99c0-6.65-3.55-12.79-9.3-16.11h-.01ZM444.05,151.99l-205.63,356.16c-1.39,2.4-5.06,1.42-5.06-1.36v-233.21c0-4.66-2.49-8.97-6.53-11.31L24.87,145.67c-2.4-1.39-1.42-5.06,1.36-5.06h411.26c5.84,0,9.49,6.33,6.57,11.39h-.01Z" />
      </svg>
    );
  }
  return <Sparkles size={size} className={cn("text-accent shrink-0", className)} aria-hidden />;
}

/** Four rising bars indicating the remaining usage signal: empty and struck through when out. */
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

const EFFORT_DESCRIPTIONS: Record<string, string> = {
  auto: "The model decides how long to think.",
  none: "Reasoning disabled.",
  low: "Fast response with lightweight reasoning.",
  medium: "Balanced speed and reasoning depth.",
  high: "Deep reasoning for complex multi-step problems.",
  xhigh: "Maximum reasoning effort for difficult tasks.",
};

const SUGGESTIONS_LIMIT = 4;

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
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({});
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [usageReport, setUsageReport] = useState<EngineUsageReport | null>(null);

  const popoverRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Active engine and model details
  const activeEngine = engines.find((e) => e.id === selectedProvider) ?? engines[0];
  const activeModelId = selectedModel ?? activeEngine?.default_model ?? activeEngine?.models[0]?.id ?? null;
  const activeModelObj = activeEngine?.models.find((m) => m.id === activeModelId);

  // Fetch real usage limits whenever provider changes or popover opens
  useEffect(() => {
    let active = true;
    api.getEngineUsageLimits(selectedProvider)
      .then((rep) => {
        if (active) setUsageReport(rep);
      })
      .catch(() => {
        if (active) setUsageReport(null);
      });
    return () => {
      active = false;
    };
  }, [selectedProvider, open]);

  // Reasoning efforts support
  const supportsEffort = Boolean(
    activeModelObj?.supported_reasoning_efforts &&
    activeModelObj.supported_reasoning_efforts.length > 1 &&
    activeModelObj.reasoning_effort_configurable !== false
  );
  const currentEffort = supportsEffort
    ? (selectedEffort ?? activeModelObj?.default_reasoning_effort)
    : null;

  // Filtered engines & models by search
  const filteredEngines = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return engines;
    return engines
      .map((engine) => {
        const matchesEngine = engine.label.toLowerCase().includes(q) || engine.id.toLowerCase().includes(q);
        const matchedModels = engine.models.filter(
          (m) => m.label.toLowerCase().includes(q) || m.id.toLowerCase().includes(q)
        );
        if (matchesEngine) return engine;
        if (matchedModels.length > 0) return { ...engine, models: matchedModels };
        return null;
      })
      .filter((e): e is Engine => e !== null);
  }, [engines, search]);

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
      setThinkingOpen(false);
    }
  }, [open]);

  // Display label for the pill
  const pillTitle = useMemo(() => {
    const engineName = activeEngine?.label ?? selectedProvider;
    const modelName = activeModelObj?.label ?? (selectedModel ? selectedModel : "Default");
    const effortPart = currentEffort ? ` · ${currentEffort.charAt(0).toUpperCase() + currentEffort.slice(1)}` : "";
    return `${engineName} · ${modelName}${effortPart}`;
  }, [activeEngine, selectedProvider, activeModelObj, selectedModel, currentEffort]);

  const pillShortName = useMemo(() => {
    const modelName = activeModelObj?.label ?? (selectedModel ? selectedModel : activeEngine?.label ?? "Model");
    const effortPart = currentEffort ? ` · ${currentEffort.charAt(0).toUpperCase() + currentEffort.slice(1)}` : "";
    return `${modelName}${effortPart}`;
  }, [activeModelObj, selectedModel, activeEngine, currentEffort]);

  const toggleExpand = (providerId: string) => {
    setExpandedProviders((prev) => ({ ...prev, [providerId]: !prev[providerId] }));
  };

  const handleSelectModel = (engineId: string, model: EngineModel) => {
    const nextEffort = model.default_reasoning_effort ?? null;
    onChange(engineId, model.id, nextEffort);
    setOpen(false);
  };

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
          "group inline-flex h-8 max-w-[200px] sm:max-w-[280px] shrink-0 items-center gap-1.5 rounded-full border border-line bg-raised/70 px-2.5 text-[13px] font-medium text-ink transition-colors duration-150 hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 select-none shadow-xs cursor-pointer",
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
          className="absolute bottom-full left-0 mb-2 w-[324px] sm:w-[340px] max-h-[500px] origin-bottom-left rounded-2xl border border-line bg-raised/95 shadow-2xl backdrop-blur-md z-50 flex flex-col overflow-hidden text-xs text-ink transition-all duration-150 animate-in fade-in zoom-in-95"
        >
          {/* Real Usage Limits Card at top of dropdown (matches screenshot exactly) */}
          {usageReport && usageReport.windows.length > 0 && (
            <div className="border-b border-line/60 bg-inset/30 p-2.5 shrink-0">
              <div className="flex items-baseline justify-between gap-2 mb-2">
                <span className="text-[12px] font-semibold text-ink">{usageReport.name} usage</span>
                <span className="text-[11px] text-muted">checked just now</span>
              </div>
              <div className="flex flex-col gap-2">
                {usageReport.windows.map((win) => (
                  <div key={win.name} className="flex flex-col gap-1">
                    <div className="flex items-baseline justify-between text-[11px]">
                      <span className="text-muted font-medium">{win.name}</span>
                      <span className="tabular-nums text-muted">
                        <span className="font-semibold text-ink">{win.left_percent}% left</span>
                        {win.resets_at && ` · resets ${formatResetTime(win.resets_at)}`}
                      </span>
                    </div>
                    <div className="h-1 w-full overflow-hidden rounded-full bg-ink/[0.08]">
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

          {/* Top Search Input */}
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-line/60 bg-inset/20 px-3">
            <Search size={15} className="shrink-0 text-muted" aria-hidden="true" />
            <input
              ref={searchInputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models"
              className="w-full bg-transparent text-[13px] text-ink placeholder:text-muted outline-none focus:outline-none"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="flex size-4 shrink-0 items-center justify-center rounded-full text-muted hover:text-ink cursor-pointer"
              >
                <X size={13} />
              </button>
            )}
          </div>

          {/* Main Scrollable Model List */}
          <div className="flex-1 overflow-y-auto overscroll-contain p-1.5 flex flex-col gap-0.5">
            {/* Active / Current Model Pinned at Top (When no active search) */}
            {!search && activeModelObj && (
              <>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="group flex min-h-[34px] w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-[13px] text-ink bg-accent/10 transition-colors cursor-pointer"
                >
                  <span className="flex size-4 shrink-0 items-center justify-center">
                    <EngineMark engineId={selectedProvider} size={14} />
                  </span>
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-ink">{activeModelObj.label}</span>
                    <span className="truncate text-[11px] text-muted">
                      {activeEngine?.label ?? selectedProvider} · Active
                    </span>
                  </div>
                  <Check size={14} className="shrink-0 text-accent" aria-hidden="true" />
                </button>
                <div role="separator" className="mx-2 my-1 h-[0.5px] shrink-0 bg-line/60" />
              </>
            )}

            {/* Filtered Engine Groups */}
            {filteredEngines.length === 0 ? (
              <div className="py-6 text-center text-xs text-muted">No models match &ldquo;{search}&rdquo;</div>
            ) : (
              filteredEngines.map((engine, index) => {
                const isSelectedEngine = engine.id === selectedProvider;
                const isExpanded = Boolean(expandedProviders[engine.id]) || Boolean(search);
                const hasManyModels = engine.models.length > SUGGESTIONS_LIMIT;
                const visibleModels = isExpanded ? engine.models : engine.models.slice(0, SUGGESTIONS_LIMIT);

                return (
                  <div key={engine.id} className={cn("flex flex-col gap-0.5", index > 0 && "mt-1")}>
                    {/* Provider Group Header */}
                    <div className="flex h-6 shrink-0 items-center justify-between px-2 text-[11px] font-semibold text-muted uppercase tracking-wider">
                      <div className="flex items-center gap-1.5 truncate">
                        <span className="flex size-4 shrink-0 items-center justify-center">
                          <EngineMark engineId={engine.id} size={14} />
                        </span>
                        <span className="truncate">{engine.label}</span>
                      </div>
                      {!engine.available && (
                        <span className="text-[10px] text-warning bg-warning/10 px-1.5 py-0.2 rounded font-normal normal-case">
                          Needs setup
                        </span>
                      )}
                    </div>

                    {/* Engine Available: Model Rows */}
                    {engine.available ? (
                      <>
                        {visibleModels.map((m) => {
                          const isCurrent =
                            isSelectedEngine &&
                            (selectedModel === m.id || (!selectedModel && m.id === engine.default_model));

                          return (
                            <button
                              key={m.id}
                              type="button"
                              onClick={() => handleSelectModel(engine.id, m)}
                              className={cn(
                                "group flex min-h-[32px] w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-[13px] transition-colors cursor-pointer",
                                isCurrent
                                  ? "bg-accent/15 text-accent font-medium"
                                  : "text-ink hover:bg-hover"
                              )}
                            >
                              <span className="flex size-4 shrink-0 items-center justify-center text-muted">
                                {isCurrent ? <span className="size-1.5 rounded-full bg-accent" /> : null}
                              </span>
                              <div className="flex min-w-0 flex-1 flex-col">
                                <span className="truncate">{m.label}</span>
                                {m.id !== m.label && (
                                  <span className="truncate text-[11px] text-muted opacity-80">{m.id}</span>
                                )}
                              </div>
                              {isCurrent && <Check size={14} className="shrink-0 text-accent" aria-hidden="true" />}
                            </button>
                          );
                        })}

                        {/* Show all / Show suggested toggle */}
                        {hasManyModels && !search && (
                          <button
                            type="button"
                            onClick={() => toggleExpand(engine.id)}
                            className="flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-[12px] text-muted hover:text-ink hover:bg-hover cursor-pointer"
                          >
                            <span className="flex size-4 shrink-0 items-center justify-center" />
                            <span className="min-w-0 flex-1 truncate">
                              {isExpanded ? "Show suggested" : `Show all (${engine.models.length})`}
                            </span>
                            {isExpanded ? (
                              <ChevronUp size={14} className="shrink-0 text-muted" aria-hidden="true" />
                            ) : (
                              <ChevronDown size={14} className="shrink-0 text-muted" aria-hidden="true" />
                            )}
                          </button>
                        )}
                      </>
                    ) : (
                      /* Engine Needs Setup: Clean Action Row */
                      <Link
                        href="/settings/engines"
                        onClick={() => setOpen(false)}
                        className="group flex min-h-[32px] w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] text-muted hover:text-ink hover:bg-hover transition-colors cursor-pointer"
                      >
                        <span className="flex size-4 shrink-0 items-center justify-center">
                          {engine.installed ? (
                            <LogIn size={15} className="text-muted group-hover:text-ink" aria-hidden="true" />
                          ) : (
                            <Download size={15} className="text-muted group-hover:text-ink" aria-hidden="true" />
                          )}
                        </span>
                        <div className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate font-medium text-ink">
                            {engine.installed ? `Sign in to ${engine.label}` : `Set up ${engine.label}`}
                          </span>
                          <span className="truncate text-[11px] text-muted">
                            {engine.reason ?? "Configure in settings"}
                          </span>
                        </div>
                        <ChevronRight size={14} className="shrink-0 text-muted" aria-hidden="true" />
                      </Link>
                    )}
                  </div>
                );
              })
            )}
          </div>

          {/* Thinking / Reasoning Effort Band (Collapsible) */}
          {supportsEffort && activeModelObj?.supported_reasoning_efforts && (
            <div className="flex shrink-0 flex-col gap-0.5 p-1.5 pt-0">
              <div role="separator" className="mx-2 my-1 h-[0.5px] bg-line/60" />
              <button
                type="button"
                onClick={() => setThinkingOpen(!thinkingOpen)}
                aria-expanded={thinkingOpen}
                className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-[13px] text-ink hover:bg-hover transition-colors cursor-pointer"
              >
                <span className="flex size-4 shrink-0 items-center justify-center">
                  <Lightbulb size={15} className="text-muted" aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1 truncate font-medium">Thinking</span>
                <span className="shrink-0 text-xs text-muted capitalize">
                  {currentEffort ?? "Auto"}
                </span>
                {thinkingOpen ? (
                  <ChevronUp size={14} className="shrink-0 text-muted" aria-hidden="true" />
                ) : (
                  <ChevronDown size={14} className="shrink-0 text-muted" aria-hidden="true" />
                )}
              </button>

              {thinkingOpen && (
                <div className="px-1.5 pb-1">
                  <div className="my-1 flex rounded-full bg-inset/80 p-0.5 border border-line/40">
                    <button
                      type="button"
                      onClick={() => onChange(selectedProvider, selectedModel, null)}
                      className={cn(
                        "h-7 flex-1 rounded-full text-xs font-medium transition-colors cursor-pointer",
                        !selectedEffort
                          ? "bg-raised text-ink font-semibold shadow-xs ring-1 ring-line/50"
                          : "text-muted hover:text-ink"
                      )}
                    >
                      Auto
                    </button>
                    {activeModelObj.supported_reasoning_efforts.map((level) => {
                      const isActive = selectedEffort?.toLowerCase() === level.toLowerCase();
                      return (
                        <button
                          key={level}
                          type="button"
                          onClick={() => onChange(selectedProvider, selectedModel, level)}
                          className={cn(
                            "h-7 flex-1 rounded-full text-xs font-medium capitalize transition-colors cursor-pointer",
                            isActive
                              ? "bg-raised text-ink font-semibold shadow-xs ring-1 ring-line/50"
                              : "text-muted hover:text-ink"
                          )}
                        >
                          {level}
                        </button>
                      );
                    })}
                  </div>
                  <p className="px-2 pt-0.5 text-[11px] leading-4 text-muted">
                    {EFFORT_DESCRIPTIONS[selectedEffort?.toLowerCase() ?? "auto"] ?? "Adjust reasoning effort depth."}
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Manage Engines Footer Link */}
          <div className="flex shrink-0 flex-col gap-0.5 p-1.5 pt-0">
            <div role="separator" className="mx-2 my-1 h-[0.5px] bg-line/60" />
            <Link
              href="/settings/engines"
              onClick={() => setOpen(false)}
              className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-[13px] text-muted hover:text-ink hover:bg-hover transition-colors cursor-pointer"
            >
              <span className="flex size-4 shrink-0 items-center justify-center">
                <Settings size={15} className="text-muted" aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1 truncate">Manage engines</span>
              <ChevronRight size={14} className="shrink-0 text-muted" aria-hidden="true" />
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
