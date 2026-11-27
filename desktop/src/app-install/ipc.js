// IPC for adding a saved app to this computer. Same trust rule as the path picker: only the main window's
// main frame, at this launch's origin, may ask; every input is checked here before anything touches disk.
const { sameOrigin } = require("../origin");
const { APP_ID } = require("./names");
const { decodeIconPng } = require("./icons");

const CHANNELS = { install: "suparagent:app-install:install", uninstall: "suparagent:app-install:uninstall", list: "suparagent:app-install:list" };
const MAX_NAME = 80;

function trustedSender(event, window, origin) {
  if (!window || !origin) return false;
  const contents = window.webContents;
  if (!contents || event?.sender !== contents || event?.senderFrame !== contents.mainFrame) return false;
  return sameOrigin(event.senderFrame?.url, origin);
}

function validId(id) {
  if (typeof id !== "string" || !APP_ID.test(id)) throw new Error("That saved app id is not valid.");
  return id;
}

function validateInstall(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) throw new Error("The request is not valid.");
  const id = validId(request.id);
  const name = request.name;
  if (typeof name !== "string" || name.length > MAX_NAME) throw new Error("The app name is not valid.");
  let iconPng;
  if (request.iconPng !== undefined && request.iconPng !== null) iconPng = decodeIconPng(request.iconPng);
  return { id, name, iconPng };
}

function registerAppInstall({ ipcMain, installer, getWindow, getOrigin }) {
  const guard = (event) => {
    if (!trustedSender(event, getWindow(), getOrigin())) throw new Error("Adding apps to this computer is only available in the Suparagent window.");
  };
  ipcMain.handle(CHANNELS.install, async (event, request) => {
    guard(event);
    return installer.install(validateInstall(request));
  });
  ipcMain.handle(CHANNELS.uninstall, async (event, id) => {
    guard(event);
    return installer.uninstall(validId(id));
  });
  ipcMain.handle(CHANNELS.list, async (event) => {
    guard(event);
    return installer.list();
  });
}

module.exports = { CHANNELS, trustedSender, validateInstall, registerAppInstall };
