/** Sample command shown once next to a new room webhook secret. */
export function roomWebhookCurl(url: string, secret: string): string {
  return [
    `curl -X POST '${url}'`,
    `  -H 'Authorization: Bearer ${secret}'`,
    `  -H 'Content-Type: application/json'`,
    `  -d '{"text": "Hello from a webhook", "sender": "Build bot"}'`,
  ].join(" \\\n");
}

/** What the section shows: the secret is only available right after enabling or rotating. */
export function roomWebhookState(enabled: boolean | undefined, secret: string | null): "off" | "on" | "secret" {
  if (secret) return "secret";
  return enabled ? "on" : "off";
}
