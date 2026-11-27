const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { createLauncher, infoPlist, launcherScript, desktopEntry, execArgument, shellQuote } = require("../src/app-install/launchers");
const { createInstaller } = require("../src/app-install/installer");
const { appKey, appUserModelId, appLink } = require("../src/app-install/names");
const { letterTilePng } = require("../src/app-install/icons");

const ID = "sa_AbCdEf0123456789";
const ID2 = "sa_ZyXwVu9876543210";
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "sup-launch-"));

function fakeExec({ failSips = false } = {}) {
  const calls = [];
  const exec = async (file, args) => {
    calls.push([file, ...args]);
    if (file.endsWith("sips")) {
      if (failSips) throw new Error("sips failed");
      fs.writeFileSync(args[args.indexOf("--out") + 1], "png");
    } else {
      fs.writeFileSync(args[args.indexOf("-o") + 1], "icns");
    }
  };
  return { exec, calls };
}

function mac(home, userData, extra = {}) {
  const x = fakeExec(extra);
  const launcher = createLauncher({ platform: "darwin", fs, homedir: home, env: {}, exec: x.exec, userData });
  return { launcher, installer: createInstaller({ fs, userData, launcher }), ...x, dir: path.join(home, "Applications", "Suparagent Apps") };
}

test("plist carries the bundle id, icon, hidden dock entry and the ownership marker", () => {
  const plist = infoPlist({ id: ID, name: "A & <B>", key: appKey(ID), hasIcon: true });
  assert.match(plist, new RegExp(`<key>CFBundleIdentifier</key>\\s*<string>app\\.suparagent\\.desktop\\.localapp\\.${appKey(ID)}</string>`));
  assert.match(plist, /<key>CFBundleExecutable<\/key>\s*<string>launcher<\/string>/);
  assert.match(plist, /<key>CFBundleIconFile<\/key>\s*<string>AppIcon<\/string>/);
  assert.match(plist, /<key>LSUIElement<\/key>\s*<true\/>/);
  assert.match(plist, /<key>NSHighResolutionCapable<\/key>\s*<true\/>/);
  assert.match(plist, new RegExp(`<key>SuparagentLocalApp</key>\\s*<string>${ID}</string>`));
  assert.ok(plist.includes("A &amp; &lt;B&gt;"));
  assert.ok(!infoPlist({ id: ID, name: "n", key: "k", hasIcon: false }).includes("CFBundleIconFile"));
});

test("the macOS script opens the link in this app and escapes quotes", () => {
  const link = appLink(ID);
  assert.equal(launcherScript(link), `#!/bin/sh\n/usr/bin/open -b 'app.suparagent.desktop' '${link}' || exec /usr/bin/open '${link}'\n`);
  assert.equal(shellQuote("it's"), "'it'\\''s'");
});

test("macOS install builds the bundle in staging, then renames it", async () => {
  const root = tmp();
  const { installer, dir, calls } = mac(path.join(root, "home"), path.join(root, "ud"));
  const result = await installer.install({ id: ID, name: "My Notes" });
  assert.deepEqual(result, { id: ID, name: "My Notes" });
  const app = path.join(dir, "My Notes.app");
  assert.ok(fs.existsSync(path.join(app, "Contents", "Info.plist")));
  assert.equal(fs.readFileSync(path.join(app, "Contents", "PkgInfo"), "utf8"), "APPL????");
  assert.equal(fs.statSync(path.join(app, "Contents", "MacOS", "launcher")).mode & 0o777, 0o755);
  assert.ok(fs.existsSync(path.join(app, "Contents", "Resources", "AppIcon.icns")));
  assert.match(fs.readFileSync(path.join(app, "Contents", "Info.plist"), "utf8"), /CFBundleIconFile/);
  assert.deepEqual(fs.readdirSync(dir), ["My Notes.app"]); // no staging left
  assert.equal(calls.filter((c) => c[0] === "/usr/bin/sips").length, 10);
  assert.equal(calls.at(-1)[0], "/usr/bin/iconutil");
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "ud", "installed-apps.json"), "utf8"));
  assert.deepEqual(manifest, { version: 1, apps: { [ID]: { name: "My Notes", files: [{ path: app, kind: "launcher" }] } } });
  assert.equal(fs.statSync(path.join(root, "ud", "installed-apps.json")).mode & 0o777, 0o600);
  assert.deepEqual(await installer.list(), [ID]);
});

test("macOS without a working sips gets no icon but still installs", async () => {
  const root = tmp();
  const { installer, dir } = mac(path.join(root, "h"), path.join(root, "u"), { failSips: true });
  await installer.install({ id: ID, name: "Plain" });
  const app = path.join(dir, "Plain.app");
  assert.ok(!fs.existsSync(path.join(app, "Contents", "Resources", "AppIcon.icns")));
  assert.ok(!fs.readFileSync(path.join(app, "Contents", "Info.plist"), "utf8").includes("CFBundleIconFile"));
  assert.ok(!fs.existsSync(path.join(app, "Contents", "Resources", ".icon-work")));
});

test("macOS reinstall replaces its own launcher; another app's or a stranger's is never replaced", async () => {
  const root = tmp();
  const { installer, dir } = mac(path.join(root, "h"), path.join(root, "u"));
  await installer.install({ id: ID, name: "Notes" });
  await installer.install({ id: ID, name: "Notes" });
  assert.deepEqual(fs.readdirSync(dir), ["Notes.app"]);
  assert.deepEqual(await installer.install({ id: ID2, name: "notes" }), { id: ID2, name: "notes 2" });
  fs.mkdirSync(path.join(dir, "Stranger.app", "Contents"), { recursive: true });
  fs.writeFileSync(path.join(dir, "Stranger.app", "Contents", "Info.plist"), "<plist/>");
  const third = await installer.install({ id: "sa_QqQqQqQqQqQqQqQq", name: "Stranger" });
  assert.equal(third.name, "Stranger 2");
  assert.equal(fs.readFileSync(path.join(dir, "Stranger.app", "Contents", "Info.plist"), "utf8"), "<plist/>");
});

test("renaming an app on reinstall removes its old launcher", async () => {
  const root = tmp();
  const { installer, dir } = mac(path.join(root, "h"), path.join(root, "u"));
  await installer.install({ id: ID, name: "Old" });
  await installer.install({ id: ID, name: "New" });
  assert.deepEqual(fs.readdirSync(dir), ["New.app"]);
});

test("uninstall removes only recorded launchers that carry the marker", async () => {
  const root = tmp();
  const { installer, dir } = mac(path.join(root, "h"), path.join(root, "u"));
  await installer.install({ id: ID, name: "Mine" });
  const manifestFile = path.join(root, "u", "installed-apps.json");
  // Tampered manifest: a file outside the launcher folder, a stranger inside it, and an unmarked bundle.
  const outside = path.join(root, "precious");
  fs.mkdirSync(outside);
  const stranger = path.join(dir, "Stranger.app");
  fs.mkdirSync(path.join(stranger, "Contents"), { recursive: true });
  fs.writeFileSync(path.join(stranger, "Contents", "Info.plist"), "<plist/>");
  const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
  manifest.apps[ID].files.push({ path: outside, kind: "launcher" }, { path: stranger, kind: "launcher" }, { path: path.join(root, "h"), kind: "icon" });
  fs.writeFileSync(manifestFile, JSON.stringify(manifest));
  const result = await installer.uninstall(ID);
  assert.deepEqual(result.removed, [path.join(dir, "Mine.app")]);
  assert.equal(result.skipped.length, 3);
  assert.ok(fs.existsSync(outside) && fs.existsSync(stranger) && fs.existsSync(path.join(root, "h")));
  assert.ok(!fs.existsSync(path.join(dir, "Mine.app")));
  assert.deepEqual(await installer.list(), []);
  assert.deepEqual(await installer.uninstall(ID2), { id: ID2, removed: [], skipped: [] });
});

test("a symbolic link in place of a launcher is not followed", async () => {
  const root = tmp();
  const { installer, dir } = mac(path.join(root, "h"), path.join(root, "u"));
  await installer.install({ id: ID, name: "Mine" });
  const target = path.join(root, "victim");
  fs.mkdirSync(path.join(target, "Contents"), { recursive: true });
  fs.writeFileSync(path.join(target, "Contents", "Info.plist"), `<key>SuparagentLocalApp</key><string>${ID}</string>`);
  fs.rmSync(path.join(dir, "Mine.app"), { recursive: true });
  fs.symlinkSync(target, path.join(dir, "Mine.app"));
  const result = await installer.uninstall(ID);
  assert.deepEqual(result.removed, []);
  assert.ok(fs.existsSync(target));
});

test("a corrupt manifest is treated as empty", async () => {
  const root = tmp();
  const { installer } = mac(path.join(root, "h"), path.join(root, "u"));
  fs.mkdirSync(path.join(root, "u"));
  fs.writeFileSync(path.join(root, "u", "installed-apps.json"), "{nope");
  assert.deepEqual(await installer.list(), []);
});

test("an unsupported platform refuses to install and lists nothing", async () => {
  const installer = createInstaller({ fs, userData: tmp(), launcher: createLauncher({ platform: "freebsd" }) });
  await assert.rejects(installer.install({ id: ID, name: "x" }));
  assert.deepEqual(await installer.list(), []);
});

test("desktop entry escapes quotes, backticks, dollars, backslashes and percent signs", () => {
  assert.equal(execArgument('/opt/My "App"/run'), '"/opt/My \\\\"App\\\\"/run"');
  assert.equal(execArgument("a$b`c%d"), '"a\\\\$b\\\\`c%%d"');
  assert.equal(execArgument("a\\b"), '"a\\\\\\\\b"');
  const entry = desktopEntry({ id: ID, name: "Notes", execPath: "/opt/Suparagent/suparagent", link: appLink(ID), iconPath: "/i/x.png" });
  assert.equal(entry, [
    "[Desktop Entry]", "Type=Application", "Version=1.0", "Name=Notes",
    `Exec="/opt/Suparagent/suparagent" "suparagent://app/${ID}?window=1"`,
    "Icon=/i/x.png", "Terminal=false", "Categories=Utility;", `X-Suparagent-LocalApp=${ID}`, "",
  ].join("\n"));
});

test("Linux install writes the entry (0644) and the icon, honouring XDG_DATA_HOME, and uninstalls by marker", async () => {
  const root = tmp();
  const data = path.join(root, "xdg");
  const launcher = createLauncher({ platform: "linux", fs, homedir: path.join(root, "h"), env: { XDG_DATA_HOME: data }, execPath: "/opt/S/suparagent", userData: path.join(root, "u") });
  const installer = createInstaller({ fs, userData: path.join(root, "u"), launcher });
  assert.deepEqual(await installer.install({ id: ID, name: "Notes: v2", iconPng: letterTilePng(ID, "N") }), { id: ID, name: "Notes v2" });
  const file = path.join(data, "applications", `suparagent-app-${ID}.desktop`);
  const icon = path.join(data, "icons", "suparagent-apps", `suparagent-app-${ID}.png`);
  assert.equal(fs.statSync(file).mode & 0o777, 0o644);
  const text = fs.readFileSync(file, "utf8");
  assert.ok(text.includes(`X-Suparagent-LocalApp=${ID}`) && text.includes(`Icon=${icon}`) && text.includes("Name=Notes v2"));
  assert.ok(fs.existsSync(icon));
  assert.deepEqual(await installer.list(), [ID]);
  const result = await installer.uninstall(ID);
  assert.deepEqual(result.removed.sort(), [file, icon].sort());
});

test("Linux defaults to ~/.local/share and will not overwrite a foreign file", async () => {
  const root = tmp();
  const launcher = createLauncher({ platform: "linux", fs, homedir: path.join(root, "h"), env: {}, execPath: "/opt/S/s", userData: path.join(root, "u") });
  const installer = createInstaller({ fs, userData: path.join(root, "u"), launcher });
  await installer.install({ id: ID, name: "A" });
  const file = path.join(root, "h", ".local", "share", "applications", `suparagent-app-${ID}.desktop`);
  assert.ok(fs.existsSync(file));
  fs.writeFileSync(file, "[Desktop Entry]\nName=Other\n");
  await assert.rejects(installer.install({ id: ID, name: "A" }));
  assert.equal((await installer.uninstall(ID)).removed.length, 1); // only the icon: the entry lost its marker
  assert.ok(fs.existsSync(file));
});

test("Linux and Windows refuse without an installed executable path", async () => {
  const root = tmp();
  const linux = createLauncher({ platform: "linux", fs, homedir: root, env: {}, execPath: null, userData: root });
  await assert.rejects(createInstaller({ fs, userData: root, launcher: linux }).install({ id: ID, name: "A" }));
});

function fakeShell() {
  const links = new Map();
  return {
    links,
    writeShortcutLink(file, operation, options) {
      assert.equal(operation, "create");
      fs.writeFileSync(file, "lnk");
      links.set(file, options);
      return true;
    },
    readShortcutLink(file) {
      if (!links.has(file)) throw new Error("no shortcut");
      return links.get(file);
    },
  };
}

test("Windows writes a Start Menu shortcut with the link, icon and its own taskbar identity", async () => {
  const root = tmp();
  const shell = fakeShell();
  const launcher = createLauncher({ platform: "win32", fs, homedir: root, env: { APPDATA: path.join(root, "Roaming") }, shell, execPath: path.join(root, "Suparagent.exe"), userData: path.join(root, "u") });
  const installer = createInstaller({ fs, userData: path.join(root, "u"), launcher });
  await installer.install({ id: ID, name: "Notes" });
  const dir = path.join(root, "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Suparagent Apps");
  const file = path.join(dir, "Notes.lnk");
  const options = shell.links.get(file);
  assert.equal(options.args, appLink(ID));
  assert.equal(options.target, path.join(root, "Suparagent.exe"));
  assert.equal(options.appUserModelId, appUserModelId(ID));
  assert.equal(options.description, "Notes");
  const ico = fs.readFileSync(options.icon);
  assert.equal(ico.readUInt16LE(2), 1);
  assert.deepEqual(await installer.list(), [ID]);
  // A second app with the same name gets a numbered shortcut; ours is replaced in place.
  assert.equal((await installer.install({ id: ID2, name: "Notes" })).name, "Notes 2");
  assert.equal((await installer.install({ id: ID, name: "Notes" })).name, "Notes");
  const removed = await installer.uninstall(ID);
  assert.equal(removed.removed.length, 2);
  assert.ok(fs.existsSync(path.join(dir, "Notes 2.lnk")));
});

test("Windows does not trust a shortcut it cannot read back as its own", async () => {
  const root = tmp();
  const shell = fakeShell();
  const launcher = createLauncher({ platform: "win32", fs, homedir: root, env: { APPDATA: root }, shell, execPath: path.join(root, "S.exe"), userData: root });
  const installer = createInstaller({ fs, userData: root, launcher });
  await installer.install({ id: ID, name: "Notes" });
  const file = [...shell.links.keys()][0];
  shell.links.set(file, { args: "https://evil.example", appUserModelId: "x" });
  assert.deepEqual((await installer.uninstall(ID)).removed.filter((p) => p.endsWith(".lnk")), []);
  assert.ok(fs.existsSync(file));
  shell.writeShortcutLink = () => false;
  await assert.rejects(installer.install({ id: ID2, name: "Other" }));
});
