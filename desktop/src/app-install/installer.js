// Installs and removes the operating-system launchers of saved apps and remembers what it made, in a small
// manifest in the user data folder: {version: 1, apps: {id: {name, files: [{path, kind}]}}}.
// Removal touches only files the manifest recorded AND that still carry this app's marker.
const nodePath = require("node:path");
const { APP_ID } = require("./names");
const { chooseIconPng } = require("./icons");

const MANIFEST = "installed-apps.json";
const KINDS = new Set(["launcher", "icon"]);

function readManifest(fs, file) {
  const empty = { version: 1, apps: {} };
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return empty;
  }
  if (!parsed || parsed.version !== 1 || !parsed.apps || typeof parsed.apps !== "object") return empty;
  const apps = {};
  for (const [id, entry] of Object.entries(parsed.apps)) {
    if (!APP_ID.test(id) || !entry || typeof entry.name !== "string" || !Array.isArray(entry.files)) continue;
    const files = entry.files.filter((f) => f && typeof f.path === "string" && nodePath.isAbsolute(f.path) && KINDS.has(f.kind)).map((f) => ({ path: f.path, kind: f.kind }));
    apps[id] = { name: entry.name, files };
  }
  return { version: 1, apps };
}

function writeManifest(fs, file, manifest) {
  fs.mkdirSync(nodePath.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
    fs.chmodSync(temporary, 0o600);
    fs.renameSync(temporary, file);
  } catch (error) {
    try {
      fs.rmSync(temporary, { force: true });
    } catch {
      /* nothing to clean */
    }
    throw error;
  }
}

function safeOwns(launcher, record, id) {
  try {
    return Boolean(launcher.owns(record, id));
  } catch {
    return false;
  }
}

function createInstaller({ fs, userData, launcher }) {
  const file = nodePath.join(userData, MANIFEST);
  let queue = Promise.resolve();
  // One change at a time, so two quick clicks cannot interleave their file work.
  const serial = (work) => {
    const run = queue.then(work, work);
    queue = run.catch(() => {});
    return run;
  };
  const supported = () => {
    if (!launcher) throw new Error("Adding a saved app to this computer is not supported here.");
    return launcher;
  };

  function removeOwned(id, files) {
    const removed = [];
    const skipped = [];
    for (const record of files) {
      if (!safeOwns(launcher, record, id)) {
        skipped.push(record.path);
        continue;
      }
      try {
        launcher.remove(record);
        removed.push(record.path);
      } catch {
        skipped.push(record.path);
      }
    }
    return { removed, skipped };
  }

  return {
    install({ id, name, iconPng }) {
      return serial(async () => {
        if (typeof id !== "string" || !APP_ID.test(id)) throw new Error("That saved app id is not valid.");
        const active = supported();
        const manifest = readManifest(fs, file);
        const previous = manifest.apps[id]?.files ?? [];
        const { png } = chooseIconPng({ id, name, iconPng });
        const made = await active.install({ id, name, png });
        const kept = new Set(made.files.map((f) => f.path));
        removeOwned(id, previous.filter((f) => !kept.has(f.path)));
        manifest.apps[id] = { name: made.name, files: made.files };
        writeManifest(fs, file, manifest);
        return { id, name: made.name };
      });
    },

    uninstall(id) {
      return serial(async () => {
        if (typeof id !== "string" || !APP_ID.test(id)) throw new Error("That saved app id is not valid.");
        supported();
        const manifest = readManifest(fs, file);
        const entry = manifest.apps[id];
        if (!entry) return { id, removed: [], skipped: [] };
        const result = removeOwned(id, entry.files);
        delete manifest.apps[id];
        writeManifest(fs, file, manifest);
        return { id, ...result };
      });
    },

    list() {
      return serial(async () => {
        if (!launcher) return [];
        const manifest = readManifest(fs, file);
        return Object.entries(manifest.apps)
          .filter(([id, entry]) => entry.files.some((record) => record.kind === "launcher" && safeOwns(launcher, record, id)))
          .map(([id]) => id);
      });
    },
  };
}

module.exports = { createInstaller, readManifest, MANIFEST };
