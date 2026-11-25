// What each first-party integration offers, as plain data. The Integrations page draws one panel per
// entry, so adding an integration means adding an entry here and an adapter in
// components/settings/integrations/adapters.ts, not a new page.
//
// Two kinds of integration share the page. A "tools" integration (MuAPI) holds a key and lets the owner
// choose which agents may use its tools. A "channel" integration (Slack) lets the owner talk to an
// agent from a chat app, so it has no tool grants: it has its own card, SlackPanel.tsx.
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

export type ToolIntegrationManifest = {
  kind: "tools";
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

export type ChannelIntegrationManifest = {
  kind: "channel";
  id: string;
  name: string;
  /** The id of the connection card that Settings search and links scroll to. */
  anchorId: string;
  /** The id of the card that links the owner's account and chooses the agent. */
  chatAnchorId: string;
  description: string;
  helpUrl: string;
  helpLabel: string;
};

export type IntegrationManifest = ToolIntegrationManifest | ChannelIntegrationManifest;

/** The extra cards the page knows how to draw. A manifest may list only these. */
export type IntegrationBlockId = "spoken-replies";
export const INTEGRATION_BLOCK_ANCHORS: Record<IntegrationBlockId, string> = {
  "spoken-replies": "setting-integration-spoken-replies",
};

export const INTEGRATIONS: readonly IntegrationManifest[] = [
  {
    kind: "tools",
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
      { id: "muapi_media_search", label: "Search image, video and audio models", risk: "read" },
      { id: "muapi_media_generate", label: "Make one capped image, video or audio item", risk: "spends" },
      { id: "muapi_predict_result", label: "Check a prediction result", risk: "read" },
    ],
    blocks: ["spoken-replies"],
  },
  {
    kind: "channel",
    id: "slack",
    name: "Slack",
    anchorId: "setting-integration-slack",
    chatAnchorId: "setting-integration-slack-chat",
    description: "Talk to one of your agents from a Slack direct message, and answer its approvals with buttons. Slack connects to this app over an outbound connection, so nothing has to be reachable from the internet, but it only works while this app is running.",
    helpUrl: "https://api.slack.com/apps?new_app=1",
    helpLabel: "Create a Slack app",
  },
];

/** The anchors every integration puts on the page, for Settings search. */
export function integrationAnchors(manifest: IntegrationManifest): string[] {
  return manifest.kind === "channel" ? [manifest.anchorId, manifest.chatAnchorId] : [manifest.anchorId];
}

export function capabilityIds(manifest: ToolIntegrationManifest): string[] {
  return manifest.capabilities.map((capability) => capability.id);
}

export function spendingCapabilityIds(manifest: ToolIntegrationManifest): string[] {
  return manifest.capabilities.filter((capability) => capability.risk === "spends").map((capability) => capability.id);
}
