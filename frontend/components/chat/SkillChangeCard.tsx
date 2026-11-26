"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/components/AuthGate";
import { ApiError } from "@/lib/api";
import { collaboration } from "@/lib/collaboration";
import type { TranscriptItem } from "@/lib/transcript";

export function SkillChangeCard({ item }: { item: Extract<TranscriptItem, { kind: "skill_change" }> }) {
  const { session } = useSession();
  const admin = session?.scopes.includes("admin");
  const [receipt, setReceipt] = useState<{ state: string; undo_available: boolean; undo_reason: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!admin) return;
    let live = true;
    const refresh = () => collaboration.setupReceipt(item.requestId).then(result => {
      if (live) setReceipt(result);
    }, () => { if (live) setReceipt({ state: "unavailable", undo_available: false, undo_reason: "Could not check Undo availability." }); });
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    window.addEventListener("focus", refresh);
    return () => { live = false; clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [admin, item.requestId]);
  const undo = async () => {
    setBusy(true); setError(null);
    try {
      await collaboration.undoSetup(item.requestId);
      setReceipt({ state: "undone", undo_available: false, undo_reason: null });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo this skill change.");
      setReceipt(null);
    } finally { setBusy(false); }
  };
  const created = item.action === "create";
  return <article className="space-y-2 rounded-field border border-line p-3 text-sm">
    <p><strong>{created ? "Saved" : "Updated"} skill: {item.name}</strong></p>
    {!created && item.previousDescription !== null && item.previousDescription !== item.description && <p className="text-xs text-muted">Description: “{item.previousDescription}” → “{item.description}”</p>}
    {(created || item.previousDescription === item.description) && item.description && <p className="text-xs text-muted">{item.description}</p>}
    <p className="text-xs text-muted">Saved disabled. Review it in Agent settings, then enable it; skill text grants no permissions.</p>
    {item.warnings.length > 0 && <p className="text-xs text-danger">Review before enabling: {item.warnings.join("; ")}</p>}
    <p className="text-xs text-muted">{item.note}</p>
    {admin && receipt?.undo_available && <button type="button" disabled={busy} className="text-xs underline" onClick={() => void undo()}>{busy ? "Undoing…" : "Undo"}</button>}
    {receipt?.state === "undone" ? <p role="status" className="text-xs text-muted">Undone.</p> : receipt?.undo_reason && <p className="text-xs text-muted">{receipt.undo_reason}</p>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </article>;
}
