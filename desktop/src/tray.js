// Tray helpers for keeping Suparagent reachable after its window is hidden.
const fs = require("node:fs");
const path = require("node:path");

function trayIconPath({ appDir, resourcesPath, isPackaged, platform }) {
  const extension = platform === "win32" ? "ico" : "png";
  const candidate = isPackaged
    ? path.join(resourcesPath, `tray.${extension}`)
    : path.join(appDir, "build", `icon.${extension}`);
  return fs.existsSync(candidate) ? candidate : null;
}

function trayMenuTemplate({ appName, onOpen, onQuit }) {
  return [
    { label: `Open ${appName}`, click: onOpen },
    { type: "separator" },
    { label: `Quit ${appName}`, click: onQuit },
  ];
}

function showWindow({ app, window, platform }) {
  if (!window || window.isDestroyed()) return;
  if (platform === "darwin") app.dock?.show();
  if (!window.isVisible()) window.show();
  if (window.isMinimized()) window.restore();
  window.focus();
}

function installTrayLifecycle({ app, window, tray, Menu, appName, platform, isQuitting, setQuitting }) {
  const onOpen = () => showWindow({ app, window, platform });
  const onQuit = () => {
    setQuitting(true);
    app.quit();
  };
  const onWindowClose = (event) => {
    if (isQuitting()) return;
    event.preventDefault();
    if (platform === "darwin") app.dock?.hide();
    window.hide();
  };

  tray.setToolTip(appName);
  tray.setContextMenu(Menu.buildFromTemplate(trayMenuTemplate({ appName, onOpen, onQuit })));
  tray.on("click", onOpen);
  window.on("close", onWindowClose);
  app.on("before-quit", () => setQuitting(true));

  return { onOpen, onQuit, onWindowClose };
}

module.exports = { trayIconPath, trayMenuTemplate, showWindow, installTrayLifecycle };
