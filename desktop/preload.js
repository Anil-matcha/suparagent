// Sandboxed preload: one narrow native dialog capability, no Node or token.
const { contextBridge, ipcRenderer } = require("electron");

const SETTINGS_NAVIGATION_CHANNEL = "suparagent:navigate-settings";
const SETTINGS_ROUTE = "/settings/";
const settingsListeners = new Set();
let pendingSettingsNavigation = false;

function isTrustedSettingsNavigation(event, route) {
  if (route !== SETTINGS_ROUTE) return false;
  const senderUrl = event?.senderFrame?.url;
  if (typeof senderUrl !== "string" || window.location.origin === "null") return false;
  try {
    return new URL(senderUrl).origin === window.location.origin;
  } catch {
    return false;
  }
}

ipcRenderer.on(SETTINGS_NAVIGATION_CHANNEL, (event, route) => {
  if (!isTrustedSettingsNavigation(event, route)) return;
  if (settingsListeners.size === 0) {
    pendingSettingsNavigation = true;
    return;
  }
  for (const listener of settingsListeners) listener();
});

contextBridge.exposeInMainWorld("suparagent", {
  desktopPermissions: (action) => ipcRenderer.invoke("suparagent:desktop-permissions", action),
  choosePath: (kind) => ipcRenderer.invoke("suparagent:choose-path", kind),
  openEngineSignIn: (url) => ipcRenderer.invoke("suparagent:engine-sign-in", url),
  openGoogleSignIn: (url) => ipcRenderer.invoke("suparagent:google-sign-in", url),
  openComposioLink: (url) => ipcRenderer.invoke("suparagent:open-composio-link", url),
  nativeControl: (request) => ipcRenderer.invoke("suparagent:native-control", request),
  hostedWorkspace: (action) => ipcRenderer.invoke("suparagent:hosted-workspace", action),
  copyText: (text) => ipcRenderer.invoke("suparagent:copy-text", text),
  showNotification: (payload) => ipcRenderer.invoke("suparagent:show-notification", payload),
  onNotificationClicked: (listener) => {
    if (typeof listener !== "function") throw new TypeError("A notification click listener is required.");
    const handler = (_event, data) => listener(data);
    ipcRenderer.on("suparagent:notification-clicked", handler);
    return () => ipcRenderer.removeListener("suparagent:notification-clicked", handler);
  },
  onSettingsRequested: (listener) => {
    if (typeof listener !== "function") throw new TypeError("A settings navigation listener is required.");
    settingsListeners.add(listener);
    if (pendingSettingsNavigation) {
      pendingSettingsNavigation = false;
      listener();
    }
    return () => settingsListeners.delete(listener);
  },
  updates: {
    run: (action) => ipcRenderer.invoke("suparagent:update", action),
    onState: (listener) => {
      const handler = (_event, state) => listener(state);
      ipcRenderer.on("suparagent:update-state", handler);
      return () => ipcRenderer.removeListener("suparagent:update-state", handler);
    },
  },
});
