export type CustomMcpAuthMode = "none" | "bearer" | "header" | "oauth";
export type CustomMcpState = "not_tested" | "connected" | "unreachable" | "needs_sign_in" | "disabled";

export type CustomMcpConnection = {
  id: string;
  name: string;
  transport: string;
  endpoint: string;
  auth_mode: CustomMcpAuthMode;
  auth_header_name: string | null;
  enabled: boolean;
  revision: number;
  state: CustomMcpState;
  last_error_code: string | null;
  credential_configured: boolean;
  oauth_signed_in: boolean;
  oauth_client_id: string | null;
  oauth_scopes: string | null;
  server_name: string | null;
  server_version: string | null;
  tool_count: number | null;
  last_tested_at: string | null;
};

export type CustomMcpTool = { name: string; description: string; input_schema: Record<string, unknown>; schema_digest: string };

export type CustomMcpDraft = {
  name: string;
  endpoint: string;
  auth_mode: CustomMcpAuthMode;
  auth_header_name?: string;
  credential?: string;
  oauth_client_id?: string;
  oauth_scopes?: string;
};

export const STATE_LABEL: Record<CustomMcpState, string> = {
  not_tested: "Not tested",
  connected: "Connected",
  unreachable: "Unreachable",
  needs_sign_in: "Needs sign-in",
  disabled: "Disabled",
};

// A connection is "on" for agents only when it is enabled; "connected" is a tested server that is still off.
export function stateLabel(connection: Pick<CustomMcpConnection, "state" | "enabled">): string {
  if (connection.state === "connected" && !connection.enabled) return STATE_LABEL.disabled;
  return connection.enabled && connection.state === "connected" ? "Enabled" : STATE_LABEL[connection.state];
}

const ERROR_TEXT: Record<string, string> = {
  connection_changed: "This connection changed in another window. Reload the list and try again.",
  credential_required: "Enter the secret before testing this connection.",
  connection_not_tested: "Test the connection successfully before enabling it.",
  connection_not_enabled: "Enable the connection before granting tools.",
  tool_schema_changed: "A tool changed on the server. Test the connection again and review its tools.",
  authentication_failed: "The server rejected the saved secret.",
  needs_sign_in: "Sign in to this server first.",
  oauth_not_supported: "This server does not offer browser sign-in that Suparagent can use (it needs PKCE S256).",
  oauth_client_required: "This server does not register apps automatically. Enter the client ID you registered with it, then try again.",
  oauth_discovery_failed: "Could not read this server's sign-in settings.",
  oauth_registration_failed: "The server refused to register Suparagent for sign-in.",
  invalid_oauth_client: "The client ID must be one word of plain characters.",
  invalid_oauth_scopes: "Scopes must be plain words separated by spaces.",
  unsafe_endpoint: "Use a public https:// address without a port, a query string, or a login in the URL.",
  invalid_header_name: "Choose a plain header name such as X-Api-Key. Reserved names are not allowed.",
  invalid_credential: "The secret must be plain text on one line, up to 4096 characters.",
  too_many_connections: "You have reached the limit of 50 custom servers.",
  agent_toolset_too_large: "That is more tool metadata than one agent can carry. Grant fewer tools.",
};

// Prefer our own wording for known causes; otherwise keep the backend's message.
export function describeError(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  if (typeof code === "string" && ERROR_TEXT[code]) return ERROR_TEXT[code];
  return error instanceof Error ? error.message : "The request failed.";
}

// What the form may send: a blank secret keeps the saved one, and a header name only travels with header auth.
export function buildUpdate(
  current: Pick<CustomMcpConnection, "revision" | "name" | "endpoint" | "auth_mode" | "auth_header_name"> & Partial<Pick<CustomMcpConnection, "oauth_client_id" | "oauth_scopes">>,
  form: { name: string; endpoint: string; auth_mode: CustomMcpAuthMode; auth_header_name: string; credential: string; oauth_client_id?: string; oauth_scopes?: string },
): Record<string, unknown> | null {
  const body: Record<string, unknown> = { revision: current.revision };
  const name = form.name.trim();
  const endpoint = form.endpoint.trim();
  const header = form.auth_mode === "header" ? form.auth_header_name.trim() : null;
  if (name !== current.name) body.name = name;
  if (endpoint !== current.endpoint) body.endpoint = endpoint;
  if (form.auth_mode !== current.auth_mode) body.auth_mode = form.auth_mode;
  if (header !== current.auth_header_name) body.auth_header_name = header ?? "";
  if (form.credential !== "") body.credential = form.credential;
  if (form.auth_mode === "oauth") {
    const client = (form.oauth_client_id ?? "").trim();
    const scopes = (form.oauth_scopes ?? "").trim().split(/\s+/).filter(Boolean).join(" ");
    if (client !== (current.oauth_client_id ?? "")) body.oauth_client_id = client;
    if (scopes !== (current.oauth_scopes ?? "")) body.oauth_scopes = scopes;
  }
  return Object.keys(body).length > 1 ? body : null;
}
