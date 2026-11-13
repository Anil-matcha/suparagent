"use client";

import { Bell } from "lucide-react";
import { EmptyPanel, LoadingState } from "./PanelState";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AppNotification, type NotificationPreferences } from "@/lib/api";
import { badgeText, formatWhen } from "@/lib/routines";
import { useT } from "@/lib/use-i18n";
import { IconButton } from "@/components/ui/IconButton";
import { dispatchNotification, getNewUnreadNotifications } from "@/lib/desktop-notifications";

const POLL_MS = 15_000;

// The header's bell: the unread count, and the feed when opened. A click on an
// entry marks it read and opens its conversation when it has one.
export function NotificationBell({
  elevated = false,
  onOpen,
}: {
  elevated?: boolean;
  onOpen?: () => void;
}) {
  const t = useT();
  const router = useRouter();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const [feedFailed, setFeedFailed] = useState(false);
  const [markFailed, setMarkFailed] = useState(false);
  const [failed, setFailed] = useState(false);
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const seenNotificationIds = useRef<Set<number> | null>(null);
  const preferenceFailed = useRef(false);
  const preferenceRetrying = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    api.getNotificationPreferences().then((prefs) => {
      if (alive) setPreferences(prefs);
    }).catch(() => { if (alive) preferenceFailed.current = true; });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const bridge = (window as unknown as { suparagent?: { onNotificationClicked?: (cb: (data: { thread_id?: number }) => void) => () => void } }).suparagent;
    if (!bridge?.onNotificationClicked) return;
    return bridge.onNotificationClicked((data) => {
      if (data?.thread_id != null) {
        router.push(`/chat?thread=${data.thread_id}`);
      }
    });
  }, [router]);

  const refresh = useCallback(() => {
    if (!preferences && preferenceFailed.current && !preferenceRetrying.current) {
      preferenceRetrying.current = true;
      void api.getNotificationPreferences().then((prefs) => {
        if (mounted.current) {
          preferenceFailed.current = false;
          setPreferences(prefs);
        }
      }).catch(() => {}).finally(() => { preferenceRetrying.current = false; });
    }
    api.unreadNotifications()
      .then((r) => {
        setUnread(r.unread);
        setFailed(false);
        // Compare IDs so a read item being replaced by a new one cannot hide the alert.
        // The first successful list establishes a baseline without alerting old unread items.
        void api.listNotifications(200).then((latest) => {
          const seenIds = seenNotificationIds.current;
          if (!seenIds) {
            seenNotificationIds.current = new Set(latest.map((item) => item.id));
            return;
          }
          // Keep the baseline while preferences are loading so new alerts can
          // be evaluated when their category and quiet-hour settings arrive.
          if (!preferences) return;
          seenNotificationIds.current = new Set(latest.map((item) => item.id));
          if (!preferences.desktop_enabled) return;
          for (const item of getNewUnreadNotifications(latest, seenIds)) {
            dispatchNotification(item, preferences, (threadId) => {
              router.push(`/chat?thread=${threadId}`);
            });
          }
        }).catch(() => {});
      })
      .catch(() => setFailed(true));
  }, [preferences, router]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    api.listNotifications().then((list) => {
      if (active) {
        setItems(list);
        setFeedFailed(false);
      }
    }).catch(() => {
      if (active) setFeedFailed(true);
    });
    return () => { active = false; };
  }, [open, unread]);

  // Escape closes the feed and puts focus back on the bell, wherever focus is inside it.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      opener.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const choose = async (item: AppNotification) => {
    if (!item.read_at) {
      try {
        await api.readNotification(item.id);
        setMarkFailed(false);
      } catch {
        setMarkFailed(true);
        if (item.thread_id !== null) router.push(`/chat?thread=${item.thread_id}`);
        return;
      }
    }
    setOpen(false);
    refresh();
    if (item.thread_id !== null) router.push(`/chat?thread=${item.thread_id}`);
  };

  const readAll = async () => {
    const displayedIds = new Set(items?.map((item) => item.id));
    try {
      await api.readAllNotifications();
      setItems((current) => current?.map((item) => displayedIds.has(item.id)
        ? { ...item, read_at: item.read_at ?? new Date().toISOString() } : item) ?? null);
      setMarkFailed(false);
      refresh();
    } catch {
      setMarkFailed(true);
    }
  };

  if (failed && unread === 0 && !open) return null; // an older backend without the feed
  const badge = badgeText(unread);

  return (
    <div className={`relative ${elevated ? "z-50" : ""}`}>
      <IconButton
        ref={opener}
        label={badge ? t("notifications.unread", { count: unread }) : t("notifications.title")}
        aria-expanded={open}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen(!open);
        }}
        className="relative"
      >
        <Bell size={18} />
        {badge && (
          <span className="absolute right-0.5 top-0.5 min-w-4 rounded-full bg-danger px-1 text-center text-[10px] font-semibold leading-4 text-accent-fg">{badge}</span>
        )}
      </IconButton>
      {open && (
        <div role="dialog" aria-label={t("notifications.title")} className="absolute right-0 top-11 z-40 w-[min(20rem,calc(100vw-1.5rem))] animate-pop-in rounded-card border border-line bg-raised shadow-xl">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <p className="flex-1 text-xs font-semibold">{t("notifications.title")}</p>
            <button onClick={() => void readAll()} className="cursor-pointer text-xs text-accent underline">{t("notifications.markAll")}</button>
          </div>
          {markFailed && <p role="alert" className="px-3 py-2 text-xs text-danger">{t("notifications.markError")}</p>}
          <ul className="max-h-96 overflow-y-auto">
            {feedFailed ? (
              <li role="alert" className="p-3 text-xs text-danger">{t("notifications.loadError")}</li>
            ) : items === null ? (
              <li className="p-3"><LoadingState rows={3} /></li>
            ) : items.length === 0 ? (
              <li><EmptyPanel title={t("notifications.empty")} /></li>
            ) : (
              items.map((item) => (
                <li key={item.id}>
                  <button onClick={() => void choose(item)} className={`block w-full cursor-pointer px-3 py-2 text-left hover:bg-hover ${item.read_at ? "" : "bg-accent/10"}`}>
                    <p className="text-xs font-medium">{item.title}</p>
                    {item.body && <p className="line-clamp-2 text-xs text-muted">{item.body}</p>}
                    <p className="text-[10px] text-faint">{formatWhen(item.created_at)}</p>
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
