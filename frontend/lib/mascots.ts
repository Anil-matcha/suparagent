/**
 * Optional cursor mascots for agent computer and task activity.
 * Strict accessibility invariants:
 * - Must strictly respect prefers-reduced-motion media query (disables floating animations).
 * - Never captures or overrides OS pointer coordinates (pointer-events: none).
 * - Can be disabled by user preference (cursor_mascots_enabled: false by default).
 */

export type MascotState = "idle" | "thinking" | "typing" | "working" | "error" | "success";

export interface MascotConfig {
  enabled: boolean;
  reducedMotion: boolean;
  state: MascotState;
  position?: { x: number; y: number };
}

export const DEFAULT_MASCOT_CONFIG: MascotConfig = {
  enabled: false,
  reducedMotion: false,
  state: "idle",
};

/**
 * Determine if mascot animation should run based on user preference and OS accessibility settings.
 */
export function shouldAnimateMascot(config: MascotConfig, systemReducedMotion = false): boolean {
  if (!config.enabled) return false;
  if (config.reducedMotion || systemReducedMotion) return false;
  return config.state === "thinking" || config.state === "typing" || config.state === "working";
}

/**
 * Returns accessible label and icon descriptor for the current mascot state.
 */
export function getMascotDescriptor(state: MascotState): { label: string; expression: string } {
  switch (state) {
    case "thinking":
      return { label: "Agent is thinking", expression: "🤔" };
    case "typing":
      return { label: "Agent is typing", expression: "✍️" };
    case "working":
      return { label: "Agent is working on screen", expression: "⚙️" };
    case "error":
      return { label: "Agent encountered a problem", expression: "⚠️" };
    case "success":
      return { label: "Agent completed the task", expression: "✨" };
    case "idle":
    default:
      return { label: "Agent is ready", expression: "🤖" };
  }
}
