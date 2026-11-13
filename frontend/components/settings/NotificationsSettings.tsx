"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { SettingsCard } from "./SettingsCard";
import { Switch } from "@/components/ui/Checkbox";
import { Button } from "@/components/ui/Button";
import { Applies } from "./Applies";
import { SavedConfirmation, useSavedConfirmation } from "./SavedConfirmation";
import { api, type NotificationPreferences } from "@/lib/api";
import {
  canRequestBrowserPermission,
  isDesktopApp,
  playNotificationSound,
  requestNotificationPermission,
  showNotificationAlert,
} from "@/lib/desktop-notifications";

const defaultPrefs: NotificationPreferences = {
  desktop_enabled: true,
  notify_needs_you: true,
  notify_failures: true,
  notify_task_due: true,
  notify_turn_completed: true,
  sound_enabled: false,
  quiet_hours_enabled: false,
  quiet_hours_start: "22:00",
  quiet_hours_end: "08:00",
};

function getNotificationPermissionSnapshot(): NotificationPermission {
  if (typeof window !== "undefined" && "Notification" in window) {
    return Notification.permission;
  }
  return "default";
}

function subscribeToPermission(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("focus", callback);
  return () => window.removeEventListener("focus", callback);
}

export function NotificationsSettings() {
  const [prefs, setPrefs] = useState<NotificationPreferences>(defaultPrefs);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const savingRef = useRef(false);
  const browserPermission = useSyncExternalStore(
    subscribeToPermission,
    getNotificationPermissionSnapshot,
    () => "default" as NotificationPermission,
  );
  const [testSent, setTestSent] = useState(false);
  const confirmation = useSavedConfirmation();

  useEffect(() => {
    let alive = true;
    api.getNotificationPreferences().then((loaded) => {
      if (alive) { setPrefs(loaded); setLoaded(true); setLoadFailed(false); }
    }).catch(() => { if (alive) setLoadFailed(true); });
    return () => { alive = false; };
  }, [loadAttempt]);

  const saveOption = async <K extends keyof NotificationPreferences>(key: K, value: NotificationPreferences[K]) => {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setSaveFailed(false);
    confirmation.clearSaved(key);
    const updated = { ...prefs, [key]: value };
    setPrefs(updated);
    try {
      const saved = await api.updateNotificationPreferences(updated);
      setPrefs(saved);
      confirmation.showSaved(key);
    } catch {
      setSaveFailed(true);
      try {
        setPrefs(await api.getNotificationPreferences());
      } catch {
        setLoaded(false);
        setLoadFailed(true);
      }
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const handleRequestPermission = async () => {
    await requestNotificationPermission();
  };

  const handleTestNotification = () => {
    if (prefs.sound_enabled) {
      playNotificationSound();
    }
    const success = showNotificationAlert({
      title: "Suparagent Notification",
      body: "Desktop notifications are working! You will be alerted when agents need you or routines fail.",
      silent: !prefs.sound_enabled,
    });
    if (success) {
      setTestSent(true);
      setTimeout(() => setTestSent(false), 3000);
    }
  };

  const isDesktop = isDesktopApp();

  if (!loaded) return loadFailed ? (
    <div role="alert">Could not load notification settings. <button onClick={() => setLoadAttempt((n) => n + 1)}>Retry</button></div>
  ) : <div>Loading notification settings…</div>;

  return (
    <div className="space-y-6" data-setting-root data-setting-container tabIndex={-1}>
      {saveFailed && <p role="alert" className="text-sm text-danger">Could not confirm notification settings were saved.</p>}
      <fieldset disabled={saving} className="space-y-6 border-0 p-0">
      <div id="setting-notifications-desktop" data-setting-id="setting-notifications-desktop" data-setting-container tabIndex={-1}>
        <SettingsCard
          title="Desktop notifications"
          description="Receive native operating system alerts when agents need your input or background tasks encounter issues."
          action={
            <Button size="sm" variant="secondary" onClick={handleTestNotification}>
              {testSent ? "Test sent!" : "Send test alert"}
            </Button>
          }
        >
          <div className="space-y-3">
            <Switch
              label="Enable desktop notifications"
              description="Show OS notifications when Suparagent is running in the background."
              checked={prefs.desktop_enabled}
              onChange={(e) => saveOption("desktop_enabled", e.target.checked)}
            />

            {!isDesktop && canRequestBrowserPermission() && browserPermission !== "granted" && (
              <div className="flex items-center justify-between rounded-field border border-line bg-surface p-3 text-xs">
                <span className="text-muted">
                  Browser notification permission is currently <strong>{browserPermission}</strong>.
                </span>
                <Button size="sm" onClick={handleRequestPermission}>
                  Allow notifications
                </Button>
              </div>
            )}

            <div className="flex items-center gap-3 pt-1">
              <Applies when="now" />
              <SavedConfirmation visible={confirmation.saved.has("desktop_enabled")} />
            </div>
          </div>
        </SettingsCard>
      </div>

      <div id="setting-notifications-categories" data-setting-id="setting-notifications-categories" data-setting-container tabIndex={-1}>
        <SettingsCard
          title="What notifies you"
          description="Choose which categories of agent events should trigger desktop alerts."
        >
          <div className="space-y-4">
            <Switch
              label="Agent input & approvals"
              description="Notify when an agent asks a question, requests tool approval, or needs human guidance."
              disabled={!prefs.desktop_enabled}
              checked={prefs.notify_needs_you}
              onChange={(e) => saveOption("notify_needs_you", e.target.checked)}
            />

            <Switch
              label="Failures & errors"
              description="Notify when a background routine or delegated task fails or encounters an unhandled error."
              disabled={!prefs.desktop_enabled}
              checked={prefs.notify_failures}
              onChange={(e) => saveOption("notify_failures", e.target.checked)}
            />

            <Switch
              label="Task deadlines & reminders"
              description="Notify when a scheduled task reaches its reminder deadline."
              disabled={!prefs.desktop_enabled}
              checked={prefs.notify_task_due}
              onChange={(e) => saveOption("notify_task_due", e.target.checked)}
            />

            <Switch
              label="Agent responses & completions"
              description="Notify when an agent completes a reply while Suparagent is in the background or minimized."
              disabled={!prefs.desktop_enabled}
              checked={prefs.notify_turn_completed ?? true}
              onChange={(e) => saveOption("notify_turn_completed", e.target.checked)}
            />

            <div className="flex items-center gap-3 pt-1">
              <Applies when="now" />
              <SavedConfirmation
                visible={
                  confirmation.saved.has("notify_needs_you") ||
                  confirmation.saved.has("notify_failures") ||
                  confirmation.saved.has("notify_task_due") ||
                  confirmation.saved.has("notify_turn_completed")
                }
              />
            </div>
          </div>
        </SettingsCard>
      </div>

      <div id="setting-notifications-sound" data-setting-id="setting-notifications-sound" data-setting-container tabIndex={-1}>
        <SettingsCard
          title="Alert sound"
          description="Control audio alerts accompanying desktop notifications."
        >
          <div className="space-y-3">
            <Switch
              label="Notification chime"
              description="Play a subtle chime sound when a desktop notification arrives."
              disabled={!prefs.desktop_enabled}
              checked={prefs.sound_enabled}
              onChange={(e) => saveOption("sound_enabled", e.target.checked)}
            />

            <div className="flex items-center gap-3 pt-1">
              <Applies when="now" />
              <SavedConfirmation visible={confirmation.saved.has("sound_enabled")} />
            </div>
          </div>
        </SettingsCard>
      </div>

      <div id="setting-notifications-quiet" data-setting-id="setting-notifications-quiet" data-setting-container tabIndex={-1}>
        <SettingsCard
          title="Quiet hours"
          description="Automatically silence desktop alerts and chime sounds during your chosen resting hours."
        >
          <div className="space-y-4">
            <Switch
              label="Enable quiet hours"
              description="Do not disturb during scheduled hours."
              disabled={!prefs.desktop_enabled}
              checked={prefs.quiet_hours_enabled ?? false}
              onChange={(e) => saveOption("quiet_hours_enabled", e.target.checked)}
            />

            {(prefs.quiet_hours_enabled ?? false) && (
              <div className="flex flex-wrap items-center gap-4 pt-1 text-xs">
                <label className="flex items-center gap-2">
                  <span className="text-muted">From:</span>
                  <input
                    type="time"
                    disabled={!prefs.desktop_enabled}
                    value={prefs.quiet_hours_start ?? "22:00"}
                    onChange={(e) => saveOption("quiet_hours_start", e.target.value)}
                    className="rounded-field border border-line bg-surface px-2.5 py-1 text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </label>
                <label className="flex items-center gap-2">
                  <span className="text-muted">To:</span>
                  <input
                    type="time"
                    disabled={!prefs.desktop_enabled}
                    value={prefs.quiet_hours_end ?? "08:00"}
                    onChange={(e) => saveOption("quiet_hours_end", e.target.value)}
                    className="rounded-field border border-line bg-surface px-2.5 py-1 text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </label>
              </div>
            )}

            <div className="flex items-center gap-3 pt-1">
              <Applies when="now" />
              <SavedConfirmation
                visible={
                  confirmation.saved.has("quiet_hours_enabled") ||
                  confirmation.saved.has("quiet_hours_start") ||
                  confirmation.saved.has("quiet_hours_end")
                }
              />
            </div>
          </div>
        </SettingsCard>
      </div>
      </fieldset>
    </div>
  );
}
