// Manages multiple isolated local data directories (profiles) on this machine.
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_ID = "default";
const PROFILES_FILE = "profiles.json";
const MAX_NAME = 40;
const MAX_PROFILES = 20;
const VALID_ID = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const COLORS = Object.freeze(["blue", "teal", "purple", "orange", "red", "yellow"]);

function cleanName(raw) {
  if (typeof raw !== "string") return null;
  const cleaned = raw.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_NAME);
  return cleaned || null;
}

function cleanColor(value) {
  return COLORS.includes(value) ? value : null;
}

function createLocalProfileStore({ userDataDir, fsImpl = fs, now = Date.now, random = () => crypto.randomBytes(3).toString("hex") }) {
  const registryPath = path.join(userDataDir, PROFILES_FILE);
  const profilesRoot = path.join(userDataDir, "profiles");

  function readRaw() {
    try {
      const parsed = JSON.parse(fsImpl.readFileSync(registryPath, "utf8"));
      if (parsed && typeof parsed === "object" && Array.isArray(parsed.profiles)) {
        return parsed;
      }
    } catch {
      // missing or invalid file falls back to default only
    }
    return null;
  }

  function dataDirFor(id) {
    if (id === DEFAULT_ID) return path.join(userDataDir, "data");
    if (!VALID_ID.test(id)) throw new Error("Invalid local profile identifier.");
    return path.join(profilesRoot, id, "data");
  }

  function state() {
    const raw = readRaw();
    const seen = new Set();
    const profiles = [];
    for (const item of raw?.profiles ?? []) {
      if (!item || typeof item.id !== "string" || !VALID_ID.test(item.id) || seen.has(item.id)) continue;
      seen.add(item.id);
      profiles.push({
        id: item.id,
        name: item.id === DEFAULT_ID ? (cleanName(item.name) ?? "Local (Default)") : (cleanName(item.name) ?? item.id),
        color: cleanColor(item.color),
        dataDir: dataDirFor(item.id),
        isDefault: item.id === DEFAULT_ID,
        createdAt: Number.isSafeInteger(item.createdAt) ? item.createdAt : now(),
      });
    }
    if (!seen.has(DEFAULT_ID)) {
      profiles.unshift({
        id: DEFAULT_ID,
        name: "Local (Default)",
        color: null,
        dataDir: dataDirFor(DEFAULT_ID),
        isDefault: true,
        createdAt: now(),
      });
    }
    const activeId = typeof raw?.activeId === "string" && profiles.some((p) => p.id === raw.activeId)
      ? raw.activeId
      : DEFAULT_ID;
    return { activeId, profiles };
  }

  function write(next) {
    const tmp = `${registryPath}.${process.pid}.tmp`;
    fsImpl.mkdirSync(path.dirname(registryPath), { recursive: true });
    const payload = {
      v: 1,
      activeId: next.activeId,
      profiles: next.profiles.map(({ id, name, color, createdAt }) => ({ id, name, color, createdAt })),
    };
    try {
      fsImpl.writeFileSync(tmp, JSON.stringify(payload, null, 2), { mode: 0o600 });
      fsImpl.renameSync(tmp, registryPath);
    } catch (err) {
      try { fsImpl.rmSync(tmp, { force: true }); } catch {}
      throw err;
    }
  }

  function makeId(name, existing) {
    const slug = name.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 36).replace(/-+$/, "");
    const used = (id) => id === DEFAULT_ID || existing.some((p) => p.id === id) || fsImpl.existsSync(path.join(profilesRoot, id));
    if (slug && VALID_ID.test(slug) && !used(slug)) return slug;
    for (;;) {
      const candidate = `${slug && VALID_ID.test(slug) ? slug : "ws"}-${random()}`;
      if (!used(candidate)) return candidate;
    }
  }

  function create(rawName, rawColor = null) {
    const name = cleanName(rawName);
    if (!name) throw new Error("Enter a workspace name.");
    const current = state();
    if (current.profiles.length >= MAX_PROFILES) {
      throw new Error(`You can keep up to ${MAX_PROFILES} local workspaces.`);
    }
    if (current.profiles.some((p) => p.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      throw new Error("A local workspace with that name already exists.");
    }
    const id = makeId(name, current.profiles);
    const dataDir = dataDirFor(id);
    fsImpl.mkdirSync(dataDir, { recursive: true, mode: 0o700 });
    const created = {
      id,
      name,
      color: cleanColor(rawColor),
      dataDir,
      isDefault: false,
      createdAt: now(),
    };
    const next = { activeId: current.activeId, profiles: [...current.profiles, created] };
    write(next);
    return created;
  }

  function rename(id, rawName) {
    const name = cleanName(rawName);
    if (!name) throw new Error("Enter a workspace name.");
    const current = state();
    const target = current.profiles.find((p) => p.id === id);
    if (!target) throw new Error("Workspace profile not found.");
    if (current.profiles.some((p) => p.id !== id && p.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      throw new Error("Another local workspace already has that name.");
    }
    target.name = name;
    write(current);
    return target;
  }

  function setActive(id) {
    const current = state();
    const target = current.profiles.find((p) => p.id === id);
    if (!target) throw new Error("Workspace profile not found.");
    current.activeId = id;
    write(current);
    return target;
  }

  function remove(id) {
    if (id === DEFAULT_ID) throw new Error("The default local workspace cannot be removed.");
    const current = state();
    if (current.activeId === id) throw new Error("Switch to another workspace before removing this one.");
    const idx = current.profiles.findIndex((p) => p.id === id);
    if (idx === -1) throw new Error("Workspace profile not found.");
    const profileFolder = path.join(profilesRoot, id);
    current.profiles.splice(idx, 1);
    write(current);
    try {
      fsImpl.rmSync(profileFolder, { recursive: true, force: true });
    } catch {
      // registry updated; ignore locked leftover files
    }
    return current;
  }

  return { state, dataDirFor, create, rename, setActive, remove, DEFAULT_ID };
}

module.exports = { createLocalProfileStore, DEFAULT_ID, cleanName };
