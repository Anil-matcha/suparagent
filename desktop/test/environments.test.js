const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createLocalProfileStore, DEFAULT_ID } = require("../src/local-profiles");
const { parsePairingInput, verifyServerEnvironment, createEnvironmentStore } = require("../src/environments");
const { registerEnvironmentsIpc } = require("../src/environments-ipc");
const { createApplicationMenuTemplate } = require("../src/application-menu");

test("local profiles: default exists, create, rename, reject active/default removal, delete inactive", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lp-"));
  try {
    const store = createLocalProfileStore({ userDataDir: dir });
    assert.equal(store.state().activeId, DEFAULT_ID);
    assert.equal(store.state().profiles.length, 1);

    const work = store.create("Work Projects");
    assert.equal(work.name, "Work Projects");
    assert.ok(fs.existsSync(work.dataDir));
    assert.throws(() => store.create("work projects"), /already exists/);

    store.rename(work.id, "Client Work");
    assert.equal(store.state().profiles.find((p) => p.id === work.id).name, "Client Work");

    assert.throws(() => store.remove(DEFAULT_ID), /default/);
    store.setActive(work.id);
    assert.throws(() => store.remove(work.id), /Switch to another/);

    store.setActive(DEFAULT_ID);
    store.remove(work.id);
    assert.equal(store.state().profiles.length, 1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("remote environments: pairing URL extraction, environmentId verification, and store CRUD", async () => {
  const parsed = parsePairingInput("https://mybox.example.com/pair#code=ABCD1234EFGH");
  assert.equal(parsed.origin, "https://mybox.example.com");
  assert.equal(parsed.code, "ABCD1234EFGH");
  assert.throws(() => parsePairingInput("https://user:pass@mybox.example.com"), /credentials/);

  const validUuid = "11111111-2222-4333-8444-555555555555";
  const otherUuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
  const fakeFetch = async () => ({
    ok: true,
    json: async () => ({ service: "suparagent", environment_id: validUuid, label: "Dev Box", version: "0.8.8" }),
  });

  const info = await verifyServerEnvironment("https://mybox.example.com", validUuid, fakeFetch);
  assert.equal(info.environmentId, validUuid);
  assert.equal(info.label, "Dev Box");
  await assert.rejects(() => verifyServerEnvironment("https://mybox.example.com", otherUuid, fakeFetch), /mismatch/);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "env-"));
  try {
    const store = createEnvironmentStore({ filePath: path.join(dir, "environments.json") });
    store.upsert({ id: "r1", name: "Dev Box", origin: "https://mybox.example.com", environmentId: validUuid, paired: true });
    assert.equal(store.list().length, 1);
    store.remove("r1");
    assert.equal(store.list().length, 0);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("environments IPC rejects foreign origins and application menu renders extra workspaces cleanly", async () => {
  let handler = null;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "envipc-"));
  try {
    const localProfiles = createLocalProfileStore({ userDataDir: dir });
    const environmentStore = createEnvironmentStore({ filePath: path.join(dir, "environments.json") });
    const win = { webContents: { getURL: () => "http://127.0.0.1:5000/settings/account/" } };

    registerEnvironmentsIpc({
      ipcMain: { handle: (_ch, fn) => { handler = fn; } },
      getWindow: () => win,
      getOrigin: () => "http://127.0.0.1:5000",
      localProfiles,
      environmentStore,
      getRemoteManager: () => null,
      onMenuUpdate() {},
      onRestart() {},
      onShowLocal() {},
    });

    await assert.rejects(() => handler({ sender: { foreign: true } }, { action: "state" }), /only accessible/);
    const state = await handler({ sender: win.webContents }, { action: "state" });
    assert.equal(state.activeLocalProfileId, "default");

    const menu = createApplicationMenuTemplate({
      platform: "win32",
      workspace: {
        active: "local",
        signedIn: false,
        localProfiles: [{ id: "default", name: "Local (Default)", isDefault: true }, { id: "work", name: "Work", isDefault: false }],
        activeLocalProfileId: "default",
        remotes: [{ id: "r1", name: "VPS" }],
        activeRemoteId: null,
        onSelect() {},
        onSignOut() {},
        onSelectLocalProfile() {},
        onSelectRemote() {},
      },
    }).find((m) => m.label === "Workspace");

    assert.ok(menu.submenu.some((i) => i.label === "Work"));
    assert.ok(menu.submenu.some((i) => i.label === "VPS"));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
