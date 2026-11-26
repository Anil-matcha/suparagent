"use client";

import { Loader2, Square, Volume2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { spokenNote } from "@/lib/speechReplies";

// Only one reply speaks at a time: starting another stops the one in progress.
let stopCurrent: (() => void) | null = null;

const SIZE = "inline-flex size-7 cursor-pointer items-center justify-center rounded-field text-muted transition-colors hover:bg-hover hover:text-ink touch:size-10";

// A small button under an assistant reply that reads it aloud, and stops it. Nothing is spoken until
// it is pressed. It says when a long reply was cut and what the reading cost.
export function SpeakButton({ text }: { text: string }) {
  const [phase, setPhase] = useState<"idle" | "loading" | "playing">("idle");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const objectUrl = useRef<string | null>(null);

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    if (audio.current) {
      audio.current.onended = null;
      audio.current.onerror = null;
      audio.current.pause();
      audio.current.removeAttribute("src");
      audio.current = null;
    }
    if (objectUrl.current) {
      URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = null;
    }
    setPhase("idle");
  }, []);

  useEffect(() => () => {
    if (stopCurrent === stop) stopCurrent = null;
    abort.current?.abort();
    audio.current?.pause();
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, [stop]);

  const start = async () => {
    stopCurrent?.();
    stopCurrent = stop;
    setError(null);
    setNote("");
    setPhase("loading");
    const controller = new AbortController();
    abort.current = controller;
    try {
      const reply = await api.speak({ text }, controller.signal);
      if (controller.signal.aborted) {
        URL.revokeObjectURL(reply.url);
        return;
      }
      objectUrl.current = reply.url;
      const player = new Audio(reply.url);
      audio.current = player;
      player.onended = stop;
      player.onerror = () => {
        setError("The audio could not be played.");
        stop();
      };
      setNote(spokenNote(reply));
      await player.play();
      setPhase("playing");
    } catch (cause) {
      if (controller.signal.aborted) return;
      const blocked = cause instanceof DOMException && cause.name === "NotAllowedError";
      setError(blocked ? "Your browser blocked playback. Press the button again." : cause instanceof ApiError || cause instanceof Error ? cause.message : "Could not read this aloud.");
      setNote("");
      stop();
    }
  };

  const busy = phase !== "idle";
  const label = phase === "loading" ? "Preparing audio. Press to cancel." : phase === "playing" ? "Stop reading" : "Read aloud";
  return (
    <>
      <button
        type="button"
        onClick={(event) => { event.preventDefault(); event.stopPropagation(); if (busy) stop(); else void start(); }}
        aria-label={label}
        aria-pressed={phase === "playing"}
        title={label}
        className={cn(SIZE, phase === "playing" && "text-accent")}
      >
        {phase === "loading" ? <Loader2 size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
          : phase === "playing" ? <Square size={13} aria-hidden="true" className="fill-current" />
            : <Volume2 size={14} aria-hidden="true" />}
      </button>
      {note && <span role="status" className="text-[11px] font-normal text-muted/80">{note}</span>}
      {error && <span role="alert" className="text-[11px] font-normal text-danger">{error}</span>}
    </>
  );
}
