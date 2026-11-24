import { describeError } from "./custom-mcp.ts";

export type ServiceProviderName = "agentmail" | "agentphone" | "other";
export type ServiceKind = "email" | "phone";

export type ServicePreset = { preset: "agentmail" | "agentphone"; label: string; kind: ServiceKind; endpoint: string; summary: string };

export type ServiceProvider = {
  connection_id: string;
  connection_name: string;
  provider: ServiceProviderName;
  resource_limit: number;
  resource_count: number;
  created_at: string;
};

export type ServiceResource = {
  id: string;
  connection_id: string;
  kind: ServiceKind;
  address: string;
  label: string;
  provider_resource_id: string | null;
  agent_ids: number[];
  created_at: string;
};

export type ServiceAuditOutcome = "dispatched" | "completed" | "failed" | "outcome_unknown";

export type ServiceAudit = {
  id: string;
  agent_id: number;
  agent_name: string;
  connection_id: string;
  connection_name: string;
  provider: string;
  tool_name: string;
  resources: string[];
  outcome: ServiceAuditOutcome;
  approved_by: string | null;
  created_at: string;
};

// What a provider holds. "other" may hold either, so the form asks.
export function kindOf(provider: ServiceProviderName): ServiceKind | null {
  return provider === "agentmail" ? "email" : provider === "agentphone" ? "phone" : null;
}

export const KIND_LABEL: Record<ServiceKind, string> = { email: "Email address", phone: "Phone number" };

const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;
const PHONE = /^\+[1-9][0-9]{6,14}$/;

// What the backend will accept, so a typo is caught before the request. Email is stored lowercase, a phone number as +E.164.
export function cleanAddress(kind: ServiceKind, value: string): string | null {
  const text = value.trim();
  if (kind === "email") {
    const lower = text.toLowerCase();
    return text.length <= 200 && EMAIL.test(lower) ? lower : null;
  }
  const digits = text.replace(/[\s().-]/g, "");
  return PHONE.test(digits) ? digits : null;
}

export const OUTCOME_LABEL: Record<ServiceAuditOutcome, string> = {
  dispatched: "Sent, no answer yet",
  completed: "Done",
  failed: "Failed",
  outcome_unknown: "Unknown, check the service",
};

export function outcomeTone(outcome: ServiceAuditOutcome): "success" | "danger" | "neutral" {
  return outcome === "completed" ? "success" : outcome === "failed" || outcome === "outcome_unknown" ? "danger" : "neutral";
}

const ERROR_TEXT: Record<string, string> = {
  provider_not_found: "That connection is not set up as an email or phone provider.",
  resource_limit_reached: "This provider is at its limit. Raise the limit first if you want to add another.",
  limit_below_existing: "The limit cannot be lower than the number you already added.",
  resource_exists: "That address is already added to this provider.",
  invalid_address: "That does not look like a valid address. Use name@example.com, or a number such as +14155550100.",
  kind_not_supported_by_provider: "This provider holds a different kind of address.",
  provider_kind_mismatch: "This provider already holds the other kind of address.",
  resource_not_found: "That address was removed in another window. Reload the list.",
  agent_not_found: "One of the agents no longer exists. Reload the list.",
  provider_resource_id_required: "Enter the AgentPhone number id. Calls and texts name the sending number by it.",
  tool_not_allowed_for_agents: "That tool is never given to agents for this service (it spends money, deletes data or changes inbound settings). Uncheck it and save again.",
};

export function describeServiceError(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  if (typeof code === "string" && ERROR_TEXT[code]) return ERROR_TEXT[code];
  return describeError(error);
}

// "Mail helper, Research" for the agents an address is given to; "No agent" when none.
export function agentNames(agentIds: number[], agents: { id: number; name: string }[]): string {
  const names = agentIds.map((id) => agents.find((agent) => agent.id === id)?.name).filter((name): name is string => Boolean(name));
  return names.length ? names.join(", ") : "No agent";
}

// The next set of agent ids when one agent is switched on or off for an address.
export function toggleAgent(current: number[], agentId: number, on: boolean): number[] {
  const rest = current.filter((id) => id !== agentId);
  return (on ? [...rest, agentId] : rest).sort((a, b) => a - b);
}

// A mailbox or number the service reports for the signed-in account (GET .../providers/{id}/lookup).
export type ServiceCandidate = { address: string; provider_resource_id: string; label: string; added: boolean };

export type SetupStep = "sign_in" | "needs_secret" | "test" | "turn_on" | "ready";

// What the owner has to do next to make a provider connection usable, from the connection's own state.
export function setupStep(connection: {
  auth_mode: "none" | "bearer" | "header" | "oauth"; oauth_signed_in: boolean; credential_configured: boolean;
  state: "not_tested" | "connected" | "unreachable" | "needs_sign_in" | "disabled"; enabled: boolean;
}): SetupStep {
  if (connection.auth_mode === "oauth" && !connection.oauth_signed_in) return "sign_in";
  if ((connection.auth_mode === "bearer" || connection.auth_mode === "header") && !connection.credential_configured) return "needs_secret";
  if (connection.state === "not_tested" || connection.state === "unreachable" || connection.state === "needs_sign_in") return "test";
  if (!connection.enabled) return "turn_on";
  return "ready";
}

export const SETUP_TEXT: Record<SetupStep, string> = {
  sign_in: "Step 1 of 3: sign in to the service in your browser.",
  needs_secret: "This connection needs its secret. Add it under “Custom MCP servers” below, then come back.",
  test: "Step 2 of 3: test the connection.",
  turn_on: "Step 3 of 3: turn it on. Until then no agent can use it.",
  ready: "On. Agents only get the tools you grant them, and only for the addresses you assign.",
};

// One page of the activity list is this many rows; a full page means there may be more.
export const AUDIT_PAGE = 20;
export function hasMoreAudit(pageSize: number): boolean {
  return pageSize >= AUDIT_PAGE;
}

// Connections that could be used as a provider but are not one yet.
export function notProviders<T extends { id: string; can_manage: boolean }>(connections: T[], providers: { connection_id: string }[]): T[] {
  const used = new Set(providers.map((item) => item.connection_id));
  return connections.filter((item) => item.can_manage && !used.has(item.id));
}
