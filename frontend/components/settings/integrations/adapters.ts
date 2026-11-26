import { api } from "@/lib/api";

// How the Integrations page talks to each integration's backend routes. The routes themselves are
// unchanged; this only maps an integration id to them.
export type IntegrationApi = {
  status: () => Promise<{ configured: boolean }>;
  save: (secret: string) => Promise<{ configured: boolean }>;
  remove: () => Promise<void>;
  getGrants: (agentId: number) => Promise<{ tool_names: string[] }>;
  setGrants: (agentId: number, tools: string[]) => Promise<{ tool_names: string[] }>;
};

export const INTEGRATION_APIS: Record<string, IntegrationApi> = {
  muapi: {
    status: () => api.getMuapiIntegration(),
    save: (secret) => api.saveMuapiIntegration(secret),
    remove: () => api.deleteMuapiIntegration(),
    getGrants: (agentId) => api.getMuapiAgentGrants(agentId),
    setGrants: (agentId, tools) => api.setMuapiAgentGrants(agentId, tools),
  },
};
