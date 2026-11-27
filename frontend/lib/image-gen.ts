// Pure helpers for the avatar image generator (BL-114). The server holds the real limits.
export const PROMPT_MAX = 1000;
export const IMAGE_SIZES = [
  { value: "1024x1024", label: "Square" },
  { value: "1536x1024", label: "Wide" },
  { value: "1024x1536", label: "Tall" },
] as const;

export type ImageProvider = { id: string; label: string; available: boolean };
export type GeneratedImage = { id: string; mime: string; bytes: number; provider: string };

// A reason the prompt cannot be sent, or null when it can.
export function promptProblem(text: string): string | null {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return "Describe the image first.";
  if (clean.length > PROMPT_MAX) return `Keep the description under ${PROMPT_MAX} characters.`;
  return null;
}

// The provider to preselect: the first one that is set up, else the first listed.
export function pickProvider(providers: ImageProvider[]): ImageProvider | null {
  return providers.find((p) => p.available) ?? providers[0] ?? null;
}
