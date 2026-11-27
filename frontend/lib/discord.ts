export type DiscordStatus = {
  available: boolean;
  configured: boolean;
  state: string;
  detail: string | null;
  linked?: boolean;
  bot?: string;
  agent_id?: number | null;
  link_code_active?: boolean;
};

export type DiscordLinkCode = { code: string; expires_in: number };

export function checkDiscordToken(value: string): string | null {
  const token = value.trim();
  if (!token || token.length > 500 || /[^\x21-\x7e]/.test(token)) {
    return "Enter a Discord bot token on one line.";
  }
  return null;
}

export function discordStateText(status: DiscordStatus): string {
  if (!status.available) return "Discord is not available on a hosted workspace.";
  if (!status.configured) return "Not connected";
  const bot = status.bot ? ` as ${status.bot}` : "";
  switch (status.state) {
    case "connected": return `Connected${bot}`;
    case "connecting": return `Connecting${bot}…`;
    case "reconnecting": return `Reconnecting${bot}…`;
    case "failed": return status.detail === "gateway_4014"
      ? "Discord refused the Gateway intents. Check the bot settings and reconnect."
      : "Discord stopped the connection. Check or replace the bot token.";
    case "standby": return "Another Suparagent on this database is running Discord.";
    default: return "Checking the connection…";
  }
}
