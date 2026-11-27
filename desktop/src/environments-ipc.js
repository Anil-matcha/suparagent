// Origin-locked IPC handlers for managing local workspace profiles and connected remote servers.
const { sameOrigin } = require("./origin");

function registerEnvironmentsIpc({
  ipcMain,
  getWindow,
  getOrigin,
  localProfiles,
  environmentStore,
  getRemoteManager,
  onMenuUpdate,
  onRestart,
  onShowLocal,
}) {
  ipcMain.handle("suparagent:environments", async (event, payload) => {
    const win = getWindow();
    if (!win || event.sender !== win.webContents || !sameOrigin(win.webContents.getURL(), getOrigin())) {
      throw new Error("Environment settings are only accessible from the local Suparagent app.");
    }
    const action = payload?.action;
    if (action === "state") {
      const localState = localProfiles.state();
      const remoteMgr = getRemoteManager?.();
      return {
        activeLocalProfileId: localState.activeId,
        activeRemoteId: remoteMgr?.currentRemoteId() ?? null,
        localProfiles: localState.profiles,
        remotes: environmentStore.list(),
      };
    }
    if (action === "createLocal") {
      const created = localProfiles.create(payload?.name, payload?.color);
      onMenuUpdate?.();
      return { profile: created, state: localProfiles.state() };
    }
    if (action === "renameLocal") {
      const renamed = localProfiles.rename(payload?.id, payload?.name);
      onMenuUpdate?.();
      return { profile: renamed, state: localProfiles.state() };
    }
    if (action === "removeLocal") {
      const next = localProfiles.remove(payload?.id);
      onMenuUpdate?.();
      return { state: next };
    }
    if (action === "switchLocal") {
      const current = localProfiles.state().activeId;
      const target = localProfiles.setActive(payload?.id);
      const remoteMgr = getRemoteManager?.();
      remoteMgr?.hideAll();
      onShowLocal?.();
      if (target.id !== current) {
        onRestart?.();
      }
      return { profile: target, restarting: target.id !== current };
    }
    if (action === "connectRemote") {
      const remoteMgr = getRemoteManager?.();
      if (!remoteMgr) throw new Error("Remote environment manager is unavailable.");
      const connected = await remoteMgr.connect({
        input: payload?.input,
        code: payload?.code,
        name: payload?.name,
      });
      onMenuUpdate?.();
      return { remote: connected, remotes: environmentStore.list() };
    }
    if (action === "switchRemote") {
      const remoteMgr = getRemoteManager?.();
      if (!remoteMgr) throw new Error("Remote environment manager is unavailable.");
      const active = await remoteMgr.showRemote(payload?.id);
      onMenuUpdate?.();
      return { remote: active };
    }
    if (action === "forgetRemote") {
      const remoteMgr = getRemoteManager?.();
      if (remoteMgr) await remoteMgr.forgetRemote(payload?.id);
      else environmentStore.remove(payload?.id);
      onMenuUpdate?.();
      return { remotes: environmentStore.list() };
    }
    if (action === "restartApp") {
      onRestart?.();
      return { restarting: true };
    }
    throw new Error("Unknown environment action.");
  });
}

module.exports = { registerEnvironmentsIpc };
