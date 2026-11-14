"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Room } from "@/lib/collaboration";
import { roomWebhookCurl, roomWebhookState } from "@/lib/room-webhook";
import { webhookUrl } from "@/lib/routines";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { useT } from "@/lib/use-i18n";

const BTN = "rounded-field border border-line px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-50";

/** Owner-only controls for the room webhook. The secret is shown once, right after it is created. */
export function RoomWebhook({ room, onChanged, onBusyChange }: { room: Room; onChanged?: () => void; onBusyChange?: (busy: boolean) => void }) {
  const t = useT();
  const toast = useToast();
  const [enabled, setEnabled] = useState(Boolean(room.webhook_enabled));
  const [shown, setShown] = useState<{ url: string; secret: string } | null>(null);
  const [confirm, setConfirm] = useState<"rotate" | "disable" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = roomWebhookState(enabled, shown?.secret ?? null);

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); toast(t("roomWebhook.copied"), "success"); } catch { /* clipboard unavailable */ }
  };
  const enable = async () => {
    setBusy(true); onBusyChange?.(true); setError(null);
    try {
      const result = await api.enableRoomWebhook(room.id);
      setShown({ url: webhookUrl(result.path), secret: result.secret });
      setEnabled(true); onChanged?.();
    } catch (err) { setError(err instanceof ApiError ? err.message : t("roomWebhook.failed")); }
    finally { setBusy(false); onBusyChange?.(false); setConfirm(null); }
  };
  const disable = async () => {
    setBusy(true); onBusyChange?.(true); setError(null);
    try { await api.disableRoomWebhook(room.id); setShown(null); setEnabled(false); onChanged?.(); }
    catch (err) { setError(err instanceof ApiError ? err.message : t("roomWebhook.failed")); }
    finally { setBusy(false); onBusyChange?.(false); setConfirm(null); }
  };

  return <section className="space-y-2 rounded-field border border-line p-3" aria-label={t("roomWebhook.title")}>
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t("roomWebhook.title")}</h3>
      <span className="text-xs text-muted">{enabled ? t("roomWebhook.on") : t("roomWebhook.off")}</span>
    </div>
    <p className="text-xs text-muted">{t("roomWebhook.help")}</p>
    {state === "secret" && shown && <div className="space-y-2 rounded-field bg-hover p-2">
      <p className="text-xs">{t("roomWebhook.secretOnce")}</p>
      <code className="block break-all text-xs">{shown.url}</code>
      <code className="block break-all text-xs">{shown.secret}</code>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} onClick={() => void copy(shown.url)}>{t("roomWebhook.copyUrl")}</button>
        <button type="button" className={BTN} onClick={() => void copy(shown.secret)}>{t("roomWebhook.copySecret")}</button>
        <button type="button" className={BTN} onClick={() => void copy(roomWebhookCurl(shown.url, shown.secret))}>{t("roomWebhook.copyCurl")}</button>
      </div>
    </div>}
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    <div className="flex flex-wrap gap-2">
      {enabled
        ? <>
          <button type="button" disabled={busy} className={BTN} onClick={() => setConfirm("rotate")}>{t("roomWebhook.rotate")}</button>
          <button type="button" disabled={busy} className={BTN} onClick={() => setConfirm("disable")}>{t("roomWebhook.disable")}</button>
        </>
        : <button type="button" disabled={busy} className={BTN} onClick={() => void enable()}>{t("roomWebhook.enable")}</button>}
    </div>
    <ConfirmDialog
      open={confirm !== null}
      onOpenChange={open => { if (!open) setConfirm(null); }}
      title={confirm === "disable" ? t("roomWebhook.disableTitle") : t("roomWebhook.rotateTitle")}
      description={confirm === "disable" ? t("roomWebhook.disableBody") : t("roomWebhook.rotateBody")}
      confirmLabel={confirm === "disable" ? t("roomWebhook.disable") : t("roomWebhook.rotate")}
      cancelLabel={t("room.cancel")}
      danger
      loading={busy}
      onConfirm={() => void (confirm === "disable" ? disable() : enable())}
    />
  </section>;
}
