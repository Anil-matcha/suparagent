// The "connect an app" card an agent asks for with request_app_connection: the plain logic, apart from the component.
import { accountScopesToSend, accountsOf, appLabel, levelOf, scopesToSend, withAccount, withApp } from "./connected-apps.ts";
import type { AccountScopes, ComposioAccount, Scopes } from "./connected-apps.ts";

export type AppConnectionState = "needs_key" | "needs_connection" | "needs_access" | "ready";
const STATES: readonly string[] = ["needs_key", "needs_connection", "needs_access", "ready"];
const APP = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export function isAppConnectionState(value: unknown): value is AppConnectionState {
  return typeof value === "string" && STATES.includes(value);
}

// A well-formed event, or null: anything else is ignored rather than shown half-drawn.
export function parseAppConnection(data: Record<string, unknown>) {
  if (typeof data.request_id !== "string" || !data.request_id || typeof data.app !== "string" || !APP.test(data.app)
      || !isAppConnectionState(data.state)) return null;
  return {
    requestId: data.request_id, app: data.app, state: data.state,
    reason: typeof data.reason === "string" ? data.reason : "",
    agentId: typeof data.agent_id === "number" ? data.agent_id : 0,
    agentName: typeof data.agent_name === "string" ? data.agent_name : "",
  };
}

// Where the person is: what is already true decides the step, whatever state the agent saw.
export type Step = "key" | "connect" | "allow" | "continue";

export function stepFor(state: AppConnectionState, accounts: ComposioAccount[] | null, scopes: Scopes | null | undefined, app: string): Step {
  if (state === "needs_key") return "key";
  if (accounts) {
    if (!accountsOf(accounts, app).some((account) => account.status === "ACTIVE")) return "connect";
    return levelOf(scopes, app) === "off" ? "allow" : "continue";
  }
  return state === "needs_connection" ? "connect" : state === "needs_access" ? "allow" : "continue";
}

// What to save to let the agent read the app. Never raises a level (an app already at "write" stays) and keeps every
// other app and account the agent has. Account ids: the one just connected, else every active account of the app.
export function readGrant(agent: { provider: string; connector_scopes?: Scopes | null; connector_account_scopes?: AccountScopes },
                          app: string, accounts: ComposioAccount[], connectedId?: string | null) {
  const level = levelOf(agent.connector_scopes, app) === "write" ? "write" : "read";
  const ids = connectedId ? [connectedId] : accountsOf(accounts, app).filter((account) => account.status === "ACTIVE").map((account) => account.id);
  let accountScopes: AccountScopes = { ...(agent.connector_account_scopes ?? {}) };
  for (const id of ids) accountScopes = withAccount(accountScopes, app, id, true);
  const scopes = withApp(agent.connector_scopes, app, level);
  return {
    connector_scopes: scopesToSend(scopes, agent.provider),
    connector_account_scopes: accountScopesToSend(accountScopes, scopes, agent.provider),
  };
}

// Remembered per card, so a reload or a second tab never asks the agent to continue twice. Storage can be blocked or
// empty, in which case the card simply offers the Continue button again.
export const resumedKey = (requestId: string) => `app-connection-resumed:${requestId}`;
export function wasResumed(requestId: string, storage?: Pick<Storage, "getItem"> | null): boolean {
  try { return storage?.getItem(resumedKey(requestId)) === "1"; } catch { return false; }
}
export function markResumed(requestId: string, storage?: Pick<Storage, "setItem"> | null): void {
  try { storage?.setItem(resumedKey(requestId), "1"); } catch { /* keep going; the button is the fallback */ }
}

export function continueText(app: string): string {
  return `I connected ${appLabel(app)} and allowed you to use it. Please continue.`;
}
