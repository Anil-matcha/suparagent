// What the Slack card needs that is not drawing: the app manifest a person pastes into Slack, the same
// token checks the backend makes (so a mistake is caught before a request is sent), and the words for
// the connection's state. The backend is the authority on every rule here; see
// backend/app/channels/slack/service.py.

export type SlackStatus = {
  /** False on a hosted workspace, which never offers Slack. */
  available: boolean;
  configured: boolean;
  /** not_configured, connecting, connected, reconnecting, failed, standby or unavailable. */
  state: string;
  /** Slack's own short error code or the connection error's name; never a token. */
  detail: string | null;
  linked?: boolean;
  team?: string;
  agent_id?: number | null;
  link_code_active?: boolean;
};

export type SlackLinkCode = { code: string; expires_in: number };

export const SLACK_APP_NAME = "Suparagent";

// "Create New App" > "From a manifest" in Slack. Everything the channel needs: a bot user a person can
// message, the two bot scopes (post, and read direct messages), the one event, buttons, and Socket
// Mode so no public address is needed. Slack cannot create the app-level token from a manifest; the
// card tells the person to make it.
export const SLACK_APP_MANIFEST = {
  display_information: {
    name: SLACK_APP_NAME,
    description: "Talk to your Suparagent agents from Slack.",
  },
  features: {
    app_home: { home_tab_enabled: false, messages_tab_enabled: true, messages_tab_read_only_enabled: false },
    bot_user: { display_name: SLACK_APP_NAME, always_online: false },
  },
  oauth_config: { scopes: { bot: ["chat:write", "im:history"] } },
  settings: {
    event_subscriptions: { bot_events: ["message.im"] },
    interactivity: { is_enabled: true },
    org_deploy_enabled: false,
    socket_mode_enabled: true,
    token_rotation_enabled: false,
  },
} as const;

export function slackManifestJson(): string {
  return JSON.stringify(SLACK_APP_MANIFEST, null, 2);
}

const MAX_TOKEN_CHARS = 500;

/** The message to show for tokens that are not worth sending, or null. Never repeats a token. */
export function checkSlackTokens(botToken: string, appToken: string): string | null {
  const bot = botToken.trim();
  const app = appToken.trim();
  if (!bot || !app) return "Paste both tokens.";
  if (bot.startsWith("xapp-") && app.startsWith("xoxb-")) {
    return "The two tokens are the wrong way round: the bot token starts xoxb-, the app-level token starts xapp-.";
  }
  if (!bot.startsWith("xoxb-")) return "The bot token must start with xoxb-.";
  if (!app.startsWith("xapp-")) return "The app-level token must start with xapp-.";
  for (const token of [bot, app]) {
    if (token.length > MAX_TOKEN_CHARS || /[^\x21-\x7e]/.test(token)) {
      return "A token must be one line of letters, numbers and dashes.";
    }
  }
  return null;
}

export type SlackTone = "good" | "warn" | "bad" | "muted";

const REFUSED = new Set(["invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired", "not_allowed_token_type", "missing_scope", "org_login_required"]);

export function slackStateText(status: SlackStatus): { text: string; tone: SlackTone } {
  if (!status.available) return { text: "Slack is not available on a hosted workspace.", tone: "muted" };
  if (!status.configured) return { text: "Not connected", tone: "muted" };
  const team = status.team ? ` to ${status.team}` : "";
  switch (status.state) {
    case "connected":
      return { text: `Connected${team}`, tone: "good" };
    case "connecting":
      return { text: `Connecting${team}…`, tone: "muted" };
    case "reconnecting":
      return { text: `Reconnecting${team}…${status.detail ? ` (${status.detail})` : ""}`, tone: "warn" };
    case "failed":
      return {
        text: status.detail && REFUSED.has(status.detail)
          ? "Slack refused the tokens. Replace them to connect again."
          : `Slack stopped the connection${status.detail ? ` (${status.detail})` : ""}. Replace the tokens to connect again.`,
        tone: "bad",
      };
    case "standby":
      return { text: "Another Suparagent on this database is running Slack.", tone: "warn" };
    default:
      return { text: "Checking the connection…", tone: "muted" };
  }
}

/** The message to send to the app in Slack to link the owner's account. */
export function linkMessage(code: string): string {
  return `link ${code}`;
}

export function minutesLeft(seconds: number): number {
  return Math.max(0, Math.ceil(seconds / 60));
}
