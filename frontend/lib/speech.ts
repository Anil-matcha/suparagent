// Spoken replies (BL-53). Pure helpers; the server holds the real limits and does the speaking.

export type SpokenReply = {
  /** An object URL for the audio. The caller revokes it. */
  url: string;
  /** MuAPI's quoted cost for this reply, when it said. */
  costUsd: number | null;
  /** True when the reply was longer than the voice allows and was cut at a sentence end. */
  truncated: boolean;
};

export function costText(costUsd: number | null): string | null {
  if (costUsd === null || !Number.isFinite(costUsd) || costUsd < 0) return null;
  if (costUsd === 0) return "no charge";
  if (costUsd < 0.01) return "under $0.01";
  return `about $${costUsd.toFixed(2)}`;
}

// The line shown beside the button once a reply is playing: whether it was cut, and what it cost.
export function spokenNote(reply: Pick<SpokenReply, "truncated" | "costUsd">): string {
  const parts: string[] = [];
  if (reply.truncated) parts.push("Read the first part only, cut at a sentence end.");
  const cost = costText(reply.costUsd);
  if (cost) parts.push(`Cost ${cost}.`);
  return parts.join(" ");
}
