// Connected remote environments store, pairing verification, and isolated WebContentsView controller.
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { sameOrigin } = require("./origin");

const ENVIRONMENTS_FILE = "environments.json";
const MAX_REMOTES = 24;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function normalizeOrigin(raw) {
  if (typeof raw !== "string") return null;
  let parsed;
  try {
    parsed = new URL(raw.trim());
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (parsed.username || parsed.password) return null;
  return parsed.origin;
}

function parsePairingInput(rawInput, explicitCode = "") {
  if (typeof rawInput !== "string" || !rawInput.trim()) {
    throw new Error("Enter a server URL or pairing link.");
  }
  const trimmed = rawInput.trim();
  const origin = normalizeOrigin(trimmed);
  if (!origin) {
    throw new Error("Server URL must start with https:// or http:// and contain no embedded credentials.");
  }
  const parsed = new URL(trimmed);
  let code = typeof explicitCode === "string" ? explicitCode.trim() : "";
  if (!code && parsed.hash) {
    const hashParams = new URLSearchParams(parsed.hash.replace(/^#/, ""));
    code = (hashParams.get("code") ?? "").trim();
  }
  if (!code && parsed.searchParams.get("code")) {
    code = parsed.searchParams.get("code").trim();
  }
  return { origin, code: code || null };
}

async function verifyServerEnvironment(origin, expectedEnvironmentId = null, fetchImpl = fetch) {
  const cleanOrigin = normalizeOrigin(origin);
  if (!cleanOrigin) throw new Error("Invalid server origin.");

  let descriptor = null;
  for (const endpoint of ["/.well-known/suparagent/environment", "/api/v1/environment"]) {
    try {
      const response = await fetchImpl(`${cleanOrigin}${endpoint}`, {
        method: "GET",
        headers: { accept: "application/json" },
      });
      if (response.ok) {
        descriptor = await response.json();
        break;
      }
    } catch {
      // try next path
    }
  }
  if (!descriptor || typeof descriptor !== "object" || descriptor.service !== "suparagent") {
    throw new Error("Target server did not return a valid Suparagent environment descriptor.");
  }
  const envId = typeof descriptor.environment_id === "string" ? descriptor.environment_id.trim().toLowerCase() : "";
  if (!UUID_RE.test(envId)) {
    throw new Error("Target server returned an invalid environment identifier.");
  }
  if (expectedEnvironmentId && expectedEnvironmentId !== envId) {
    throw new Error("Server identity changed (environment ID mismatch). Remove and re-pair if this server was replaced.");
  }
  return {
    environmentId: envId,
    label: typeof descriptor.label === "string" && descriptor.label.trim() ? descriptor.label.trim() : new URL(cleanOrigin).host,
    platform: typeof descriptor.platform === "string" ? descriptor.platform : null,
    version: typeof descriptor.version === "string" ? descriptor.version : null,
  };
}

function createEnvironmentStore({ filePath, fsImpl = fs, now = Date.now }) {
  function read() {
    try {
      const parsed = JSON.parse(fsImpl.readFileSync(filePath, "utf8"));
      if (parsed && typeof parsed === "object" && Array.isArray(parsed.remotes)) {
        return {
          remotes: parsed.remotes.filter((r) => r && typeof r.id === "string" && normalizeOrigin(r.origin)),
          lastActiveRemoteId: typeof parsed.lastActiveRemoteId === "string" ? parsed.lastActiveRemoteId : null,
        };
      }
    } catch {
      // missing or unreadable
    }
    return { remotes: [], lastActiveRemoteId: null };
  }

  function write(data) {
    const tmp = `${filePath}.${process.pid}.tmp`;
    fsImpl.mkdirSync(path.dirname(filePath), { recursive: true });
    try {
      fsImpl.writeFileSync(tmp, JSON.stringify({ v: 1, ...data }, null, 2), { mode: 0o600 });
      fsImpl.renameSync(tmp, filePath);
    } catch (err) {
      try { fsImpl.rmSync(tmp, { force: true }); } catch {}
      throw err;
    }
  }

  function list() {
    return read().remotes;
  }

  function upsert(entry) {
    const data = read();
    if (!data.remotes.some((r) => r.id === entry.id) && data.remotes.length >= MAX_REMOTES) {
      throw new Error(`Maximum of ${MAX_REMOTES} remote servers reached.`);
    }
    const existingIdx = data.remotes.findIndex((r) => r.id === entry.id || (r.origin === entry.origin && r.environmentId === entry.environmentId));
    const saved = {
      id: existingIdx >= 0 ? data.remotes[existingIdx].id : entry.id,
      kind: "remote",
      name: String(entry.name || entry.origin).trim().slice(0, 60),
      origin: entry.origin,
      environmentId: entry.environmentId,
      partition: `persist:remote-${existingIdx >= 0 ? data.remotes[existingIdx].id : entry.id}`,
      paired: Boolean(entry.paired),
      createdAt: existingIdx >= 0 ? data.remotes[existingIdx].createdAt : now(),
    };
    if (existingIdx >= 0) data.remotes[existingIdx] = saved;
    else data.remotes.push(saved);
    write(data);
    return saved;
  }

  function remove(id) {
    const data = read();
    const removed = data.remotes.find((r) => r.id === id) ?? null;
    data.remotes = data.remotes.filter((r) => r.id !== id);
    if (data.lastActiveRemoteId === id) data.lastActiveRemoteId = null;
    write(data);
    return removed;
  }

  function setLastRemote(id) {
    const data = read();
    data.lastActiveRemoteId = id;
    write(data);
  }

  return { list, upsert, remove, setLastRemote, read };
}

function remoteWebPreferences(partition) {
  return {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webviewTag: false,
    partition,
  };
}

function createRemoteWorkspaceManager({
  window,
  WebContentsView,
  session,
  openExternal,
  store,
  fetchImpl = fetch,
  onChange = () => {},
}) {
  const views = new Map();
  let activeId = null;

  const fit = (view) => {
    if (!view || window.isDestroyed()) return;
    const [width, height] = window.getContentSize();
    view.setBounds({ x: 0, y: 0, width, height });
  };

  window.on?.("resize", () => {
    if (activeId && views.has(activeId)) fit(views.get(activeId));
  });

  function hideAll() {
    activeId = null;
    for (const v of views.values()) {
      v.setVisible(false);
    }
    store.setLastRemote(null);
    onChange(null);
  }

  async function pairSession(remote, code) {
    if (!code) return false;
    const targetSession = session.fromPartition(remote.partition);
    const body = JSON.stringify({ code, device_name: os.hostname() || "Suparagent Desktop" });
    const response = await targetSession.fetch(`${remote.origin}/api/v1/auth/pair`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body,
    });
    if (!response.ok) {
      let detail = `Pairing failed (${response.status}).`;
      try {
        const json = await response.json();
        if (json?.detail?.message) detail = json.detail.message;
      } catch {}
      throw new Error(detail);
    }
    return true;
  }

  async function connect({ input, code = "", name = "" }) {
    const parsed = parsePairingInput(input, code);
    const verified = await verifyServerEnvironment(parsed.origin, null, fetchImpl);
    const id = crypto.randomUUID().slice(0, 12);
    const remote = store.upsert({
      id,
      name: name.trim() || verified.label,
      origin: parsed.origin,
      environmentId: verified.environmentId,
      paired: Boolean(parsed.code),
    });
    if (parsed.code) {
      await pairSession(remote, parsed.code);
    }
    return remote;
  }

  async function showRemote(id) {
    const remote = store.list().find((r) => r.id === id);
    if (!remote) throw new Error("Saved server not found.");
    const targetSession = session.fromPartition(remote.partition);
    // Verify identity before loading remote origin into WebContentsView
    await verifyServerEnvironment(remote.origin, remote.environmentId, (url, init) => targetSession.fetch(url, init));

    let view = views.get(id);
    if (!view) {
      targetSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
      view = new WebContentsView({ webPreferences: remoteWebPreferences(remote.partition) });
      view.webContents.on("will-navigate", (event, url) => {
        if (!sameOrigin(url, remote.origin)) {
          event.preventDefault();
          void openExternal(url);
        }
      });
      view.webContents.on("will-redirect", (event, url) => {
        if (!sameOrigin(url, remote.origin)) event.preventDefault();
      });
      view.webContents.setWindowOpenHandler(({ url }) => {
        if (!sameOrigin(url, remote.origin)) void openExternal(url);
        return { action: "deny" };
      });
      view.webContents.on("will-attach-webview", (event) => event.preventDefault());
      window.contentView.addChildView(view);
      views.set(id, view);
      void view.webContents.loadURL(`${remote.origin}/chat/`).catch(() => {});
    }

    for (const [vid, v] of views.entries()) {
      v.setVisible(vid === id);
    }
    activeId = id;
    fit(view);
    view.webContents.focus();
    store.setLastRemote(id);
    onChange(remote);
    return remote;
  }

  async function forgetRemote(id) {
    if (activeId === id) hideAll();
    const view = views.get(id);
    if (view) {
      views.delete(id);
      try { window.contentView?.removeChildView?.(view); } catch {}
      try { view.webContents.close(); } catch {}
    }
    const removed = store.remove(id);
    if (removed) {
      try {
        await session.fromPartition(removed.partition).clearStorageData();
      } catch {}
    }
  }

  function destroy() {
    for (const view of views.values()) {
      try { window.contentView?.removeChildView?.(view); } catch {}
      try { view.webContents.close(); } catch {}
    }
    views.clear();
  }

  return {
    connect,
    showRemote,
    hideAll,
    forgetRemote,
    currentRemoteId: () => activeId,
    destroy,
  };
}

module.exports = {
  ENVIRONMENTS_FILE,
  normalizeOrigin,
  parsePairingInput,
  verifyServerEnvironment,
  createEnvironmentStore,
  createRemoteWorkspaceManager,
};
