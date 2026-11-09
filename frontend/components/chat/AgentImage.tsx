"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Avatar } from "@/components/ui/Avatar";

// An agent's picture: its generated image when it has one, else the letter avatar.
// `tick` changes when the picture is set or removed, so it is fetched again.
export function AgentImage({ agentId, name, size = "md", tick = 0 }: { agentId: number; name: string; size?: "sm" | "md" | "lg"; tick?: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let url: string | null = null;
    api.agentAvatarUrl(agentId).then(
      (u) => {
        if (live) { url = u; setSrc(u); } else URL.revokeObjectURL(u);
      },
      () => { if (live) setSrc(null); },
    );
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [agentId, tick]);
  if (!src) return <Avatar agentId={agentId} name={name} size={size} />;
  const px = size === "lg" ? "size-11" : size === "sm" ? "size-6" : "size-8";
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" aria-hidden="true" className={`${px} shrink-0 rounded-full object-cover`} />;
}
