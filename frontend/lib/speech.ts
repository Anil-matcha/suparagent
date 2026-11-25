// Spoken replies (BL-53). Pure helpers; the server holds the real limits and does the speaking.

export type SpokenReply = {
  /** An object URL for the audio. The caller revokes it. */
  url: string;
  /** MuAPI's quoted cost for this reply, when it said. Absent for a reply played from saved audio. */
  costUsd: number | null;
  /** True when the reply was longer than the voice allows and was cut at a sentence end. */
  truncated: boolean;
  /** True when the audio was already saved, so nothing was charged. */
  cached: boolean;
};

export type SpeechVoice = { id: string; label: string; max_chars: number; price_hint: string };
export type SpeechVoices = {
  available: boolean;
  default: string;
  voices: SpeechVoice[];
  saved: { clips: number; bytes: number };
};

export function costText(costUsd: number | null): string | null {
  if (costUsd === null || !Number.isFinite(costUsd) || costUsd < 0) return null;
  if (costUsd === 0) return "no charge";
  if (costUsd < 0.01) return "under $0.01";
  return `about $${costUsd.toFixed(2)}`;
}

// The line shown beside the button once a reply is playing: whether it was cut, and what it cost.
export function spokenNote(reply: Pick<SpokenReply, "truncated" | "costUsd" | "cached">): string {
  const parts: string[] = [];
  if (reply.truncated) parts.push("Read the first part only, cut at a sentence end.");
  if (reply.cached) parts.push("Played from saved audio. No charge.");
  else {
    const cost = costText(reply.costUsd);
    if (cost) parts.push(`Cost ${cost}.`);
  }
  return parts.join(" ");
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function savedAudioText(saved: SpeechVoices["saved"]): string {
  if (saved.clips === 0) return "No saved replies.";
  return `${saved.clips} saved ${saved.clips === 1 ? "reply" : "replies"} (${formatBytes(saved.bytes)}).`;
}
