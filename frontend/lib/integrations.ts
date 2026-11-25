// What each first-party integration offers, as plain data. The Integrations page draws one panel per
// entry, so adding an integration means adding an entry here and an adapter in
// components/settings/integrations/adapters.ts, not a new page.
//
// A capability's risk decides what the page shows around it: "spends" means a call can use the
// owner's money, so the page offers the "scheduled runs may spend" choice for agents that hold it.

export type CapabilityRisk = "read" | "spends";

export type IntegrationCapability = {
  /** The exact tool name an agent is granted. It must match the backend's tool definition. */
  id: string;
  label: string;
  risk: CapabilityRisk;
};

export type IntegrationManifest = {
  id: string;
  name: string;
  /** The id of the block that Settings search and links scroll to. Keep it in step with settings-registry. */
  anchorId: string;
  description: string;
  secret: {
    label: string;
    placeholder: string;
    replacePlaceholder: string;
    helpUrl: string;
    helpLabel: string;
  };
  access: {
    title: string;
    description: string;
    emptyMessage: string;
    scheduledSpendNote: string;
  };
  capabilities: readonly IntegrationCapability[];
  /** Extra cards this integration adds once it is connected, drawn by components/settings/integrations/blocks.tsx. */
  blocks: readonly IntegrationBlockId[];
};

/** The extra cards the page knows how to draw. A manifest may list only these. */
export type IntegrationBlockId = "spoken-replies";
export const INTEGRATION_BLOCK_ANCHORS: Record<IntegrationBlockId, string> = {
  "spoken-replies": "setting-integration-spoken-replies",
};

export const INTEGRATIONS: readonly IntegrationManifest[] = [
  {
    id: "muapi",
    name: "MuAPI",
    anchorId: "setting-integration-muapi",
    description: "This first-party connection calls MuAPI's REST API directly. Its data API key is separate from the key used by MuAPI cloud computers. Once connected, MuAPI is also offered as an image provider for agent pictures.",
    secret: {
      label: "MuAPI API key",
      placeholder: "Paste your MuAPI API key",
      replacePlaceholder: "Enter a replacement key",
      helpUrl: "https://muapi.ai/access-keys",
      helpLabel: "Get an API key",
    },
    access: {
      title: "Agent access",
      description: "Each grant is exact and starts off. Paid calls still need approval in chats and stay within your spending limits.",
      emptyMessage: "Create an agent to configure MuAPI access.",
      scheduledSpendNote: "Allow scheduled runs to spend within the configured limits without a per-call approval. Off by default.",
    },
    capabilities: [
      { id: "muapi_data_api_search", label: "Search data APIs", risk: "read" },
      { id: "muapi_data_api_inspect", label: "Inspect model schema", risk: "read" },
      { id: "muapi_data_api_estimate", label: "Estimate a call", risk: "read" },
      { id: "muapi_data_api_call", label: "Make a capped paid call", risk: "spends" },
      { id: "muapi_predict_result", label: "Check a prediction result", risk: "read" },
    ],
    blocks: ["spoken-replies"],
  },
];

export function capabilityIds(manifest: IntegrationManifest): string[] {
  return manifest.capabilities.map((capability) => capability.id);
}

export function spendingCapabilityIds(manifest: IntegrationManifest): string[] {
  return manifest.capabilities.filter((capability) => capability.risk === "spends").map((capability) => capability.id);
}
