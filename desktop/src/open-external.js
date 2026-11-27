// Lets the chat page open a web link in the person's browser. Only the main window's main frame at this
// launch's origin may ask, and only for a plain http(s) link without credentials.
const { trustedSender } = require("./app-install/ipc");

const MAX_URL = 2048;

/** The normalised link, or throws unless it is an http or https address with no username or password. */
function validateExternalUrl(raw) {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > MAX_URL) throw new Error("That link is not valid.");
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("That link is not valid.");
  }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("That link is not valid.");
  return url.href;
}

function registerOpenExternal({ ipcMain, openExternal, getWindow, getOrigin }) {
  ipcMain.handle("suparagent:open-external", async (event, raw) => {
    if (!trustedSender(event, getWindow(), getOrigin())) throw new Error("Opening links is only available in the Suparagent window.");
    await openExternal(validateExternalUrl(raw));
  });
}

module.exports = { MAX_URL, validateExternalUrl, registerOpenExternal };
