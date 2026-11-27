/**
 * Speech synthesis and audio utilities for agent spoken replies and voice settings.
 * Supports Web Speech API with markdown normalization, voice profile configuration,
 * and half-duplex call session management.
 */

export interface VoiceProfile {
  voiceUri: string;
  name: string;
  lang: string;
  localService: boolean;
  rate: number; // 0.5 to 2.0 (default 1.0)
  pitch: number; // 0.5 to 1.5 (default 1.0)
  autoSpeak: boolean;
}

export const DEFAULT_VOICE_PROFILE: VoiceProfile = {
  voiceUri: "",
  name: "System Default",
  lang: "en-US",
  localService: true,
  rate: 1.0,
  pitch: 1.0,
  autoSpeak: false,
};

/**
 * Clean markdown and technical syntax from text before passing to speech synthesis.
 * Strips code fences, raw URLs, Markdown links, formatting tokens, and noisy symbols.
 */
export function normalizeTextForSpeech(markdown: string): string {
  if (!markdown) return "";

  let text = markdown;

  // Remove code blocks entirely (```...```)
  text = text.replace(/```[\s\S]*?```/g, " [code omitted] ");

  // Remove inline code spans (`...`)
  text = text.replace(/`([^`]+)`/g, "$1");

  // Replace Markdown links [text](url) with just text
  text = text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");

  // Remove raw URLs
  text = text.replace(/https?:\/\/\S+/g, " link ");

  // Remove Markdown images ![alt](url)
  text = text.replace(/!\[[^\]]*\]\([^)]+\)/g, "");

  // Remove HTML tags
  text = text.replace(/<[^>]+>/g, " ");

  // Remove headers (#, ##, etc.)
  text = text.replace(/^#{1,6}\s+/gm, "");

  // Remove bold / italic markers (* or _)
  text = text.replace(/[*_]{1,3}([^*_]+)[*_]{1,3}/g, "$1");

  // Remove blockquotes (> )
  text = text.replace(/^>\s+/gm, "");

  // Remove list bullets (*, -, +, 1.)
  text = text.replace(/^[\s]*[-*+]\s+/gm, "");
  text = text.replace(/^[\s]*\d+\.\s+/gm, "");

  // Remove horizontal rules
  text = text.replace(/^[-*_]{3,}\s*$/gm, "");

  // Collapse consecutive whitespace and trim
  text = text.replace(/\s+/g, " ").trim();

  return text;
}

export interface SpeechPlaybackOptions {
  voiceUri?: string;
  rate?: number;
  pitch?: number;
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (err: Error) => void;
}

export interface SpeechController {
  stop: () => void;
  pause: () => void;
  resume: () => void;
  speaking: boolean;
}

/**
 * Synthesize speech for a given text using browser SpeechSynthesis API.
 */
export function speakText(
  text: string,
  options: SpeechPlaybackOptions = {},
  synth: typeof window.speechSynthesis | null = typeof window !== "undefined" ? window.speechSynthesis : null
): SpeechController {
  const normalized = normalizeTextForSpeech(text);

  if (!synth || typeof SpeechSynthesisUtterance === "undefined") {
    options.onError?.(new Error("Speech synthesis is not supported on this device."));
    return {
      stop: () => {},
      pause: () => {},
      resume: () => {},
      speaking: false,
    };
  }

  // Cancel any ongoing speech
  try {
    synth.cancel();
  } catch {
    // Ignore cancel failure
  }

  if (!normalized) {
    options.onEnd?.();
    return {
      stop: () => {},
      pause: () => {},
      resume: () => {},
      speaking: false,
    };
  }

  const utterance = new SpeechSynthesisUtterance(normalized);
  utterance.rate = Math.max(0.5, Math.min(2.0, options.rate ?? 1.0));
  utterance.pitch = Math.max(0.5, Math.min(1.5, options.pitch ?? 1.0));

  if (options.voiceUri) {
    const voices = synth.getVoices?.() || [];
    const match = voices.find((v) => v.voiceURI === options.voiceUri);
    if (match) utterance.voice = match;
  }

  utterance.onstart = () => options.onStart?.();
  utterance.onend = () => options.onEnd?.();
  utterance.onerror = (e) => options.onError?.(new Error(`Speech synthesis error: ${e.error}`));

  synth.speak(utterance);

  return {
    stop: () => {
      try {
        synth.cancel();
      } catch {
        // Ignore
      }
    },
    pause: () => {
      try {
        synth.pause();
      } catch {
        // Ignore
      }
    },
    resume: () => {
      try {
        synth.resume();
      } catch {
        // Ignore
      }
    },
    get speaking() {
      return synth.speaking;
    },
  };
}

/**
 * Call session state machine for half-duplex voice interactions.
 */
export type CallState = "idle" | "connecting" | "listening" | "speaking" | "muted" | "ended";

export interface CallSession {
  state: CallState;
  captions: string[];
  isMuted: boolean;
  mute: () => void;
  unmute: () => void;
  endCall: () => void;
}
