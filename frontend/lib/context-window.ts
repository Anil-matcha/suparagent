import type { Engine } from "./api";
import type { TranscriptItem } from "./transcript";

export type ContextSeverity = "none" | "warn" | "critical";

export type ContextWindowStatus = {
  usedTokens: number;
  limitTokens: number;
  percent: number;
  severity: ContextSeverity;
};

const WARN_PERCENT = 70;
const CRITICAL_PERCENT = 90;

function readPositiveInt(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return Math.floor(value);
}

export function resolveModelContextLimit(
  model: string | null | undefined,
  engine?: Pick<Engine, "id" | "config"> | null,
): number {
  const configured = readPositiveInt(engine?.config?.context_tokens);
  if (configured !== null) return configured;

  const id = (model ?? "").trim().toLowerCase();
  const provider = (engine?.id ?? "").trim().toLowerCase();

  if (id.includes("[1m]") || id.includes("1m")) return 1_000_000;
  if (id.includes("gemini") || id.includes("hydrogen") || id.includes("oxygen")) return 1_000_000;
  if (id.includes("gpt-4.1")) return 1_000_000;
  if (id.includes("gpt-4o") || id.includes("gpt-4-turbo")) return 128_000;
  if (id.includes("claude")) return 200_000;
  if (id.includes("codex") || id.includes("gpt-5") || /^o[134]\b/.test(id)) return 200_000;

  if (provider === "antigravity" || provider === "gemini") return 1_000_000;
  if (provider === "claude" || provider === "codex") return 200_000;
  return 128_000;
}

export function extractLatestTurnContextTokens(
  items: readonly TranscriptItem[],
): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i]!;
    if (item.kind === "session_reset") return 0;
    if (item.kind === "turn_end" && item.usage && typeof item.usage === "object") {
      const usage = item.usage;
      const total =
        (readPositiveInt(usage.input_tokens) ?? 0) +
        (readPositiveInt(usage.cache_read_input_tokens) ?? 0) +
        (readPositiveInt(usage.cache_creation_input_tokens) ?? 0) +
        (readPositiveInt(usage.output_tokens) ?? 0);
      if (total > 0) return total;
    }
  }
  return 0;
}

export function measureContextWindow(
  items: readonly TranscriptItem[],
  model: string | null | undefined,
  engine?: Pick<Engine, "id" | "config"> | null,
): ContextWindowStatus {
  const limitTokens = resolveModelContextLimit(model, engine);
  const usedTokens = extractLatestTurnContextTokens(items);
  const percent =
    usedTokens <= 0
      ? 0
      : Math.min(100, Math.max(1, Math.round((usedTokens / limitTokens) * 100)));
  const severity: ContextSeverity =
    percent >= CRITICAL_PERCENT
      ? "critical"
      : percent >= WARN_PERCENT
        ? "warn"
        : "none";

  return { usedTokens, limitTokens, percent, severity };
}
