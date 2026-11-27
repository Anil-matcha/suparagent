// Opens a web link outside this app. The browser opens a tab; the desktop shell denies window.open and offers no
// generic opener yet, so when it adds one (window.suparagent.openExternalLink) it is used here first.
type Bridge = { openExternalLink?: (url: string) => unknown };

export function openExternalLink(url: string): void {
  if (typeof window === "undefined") return;
  const bridge = (window as unknown as { suparagent?: Bridge }).suparagent;
  if (bridge?.openExternalLink) { void bridge.openExternalLink(url); return; }
  window.open(url, "_blank", "noopener,noreferrer");
}
