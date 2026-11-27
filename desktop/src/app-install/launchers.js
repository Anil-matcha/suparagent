// Per-system launchers for saved apps: a small .app on macOS, a Start Menu shortcut on Windows and a .desktop
// entry on Linux. Everything the system gives us (file system, shell, process runner, platform) is injected so
// each branch can be tested on any machine.
//
// A launcher only ever opens suparagent://app/<id>?window=1. It carries a marker with the app's id, and we
// only replace or delete launchers that carry it.
const nodePath = require("node:path");
const { appKey, appUserModelId, appLink, chooseLauncherName, launcherName, foldName, APP_ID } = require("./names");
const { chooseIconPng, icoFromPng } = require("./icons");

const BUNDLE_ID = "app.suparagent.desktop";
const FOLDER = "Suparagent Apps";
const MARKER_KEY = "SuparagentLocalApp";
const LINUX_MARKER = "X-Suparagent-LocalApp";

const xml = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");
const shellQuote = (text) => `'${String(text).replace(/'/g, `'\\''`)}'`;

function infoPlist({ id, name, key, hasIcon }) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleIdentifier</key>
  <string>${xml(`${BUNDLE_ID}.localapp.${key}`)}</string>
  <key>CFBundleName</key>
  <string>${xml(name)}</string>
  <key>CFBundleDisplayName</key>
  <string>${xml(name)}</string>
  <key>CFBundleExecutable</key>
  <string>launcher</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleSignature</key>
  <string>????</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
${hasIcon ? "  <key>CFBundleIconFile</key>\n  <string>AppIcon</string>\n" : ""}  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>${MARKER_KEY}</key>
  <string>${xml(id)}</string>
</dict>
</plist>
`;
}

function launcherScript(link) {
  return `#!/bin/sh\n/usr/bin/open -b ${shellQuote(BUNDLE_ID)} ${shellQuote(link)} || exec /usr/bin/open ${shellQuote(link)}\n`;
}

// Desktop Entry values: backslash is escaped once for the file, and the Exec line is quoted as the spec asks.
const desktopValue = (text) => String(text).replace(/\\/g, "\\\\");
function execArgument(text) {
  const quoted = String(text).replace(/([\\"`$])/g, "\\$1").replace(/%/g, "%%");
  return `"${desktopValue(quoted)}"`;
}

function desktopEntry({ id, name, execPath, link, iconPath }) {
  return [
    "[Desktop Entry]",
    "Type=Application",
    "Version=1.0",
    `Name=${desktopValue(name)}`,
    `Exec=${execArgument(execPath)} ${execArgument(link)}`,
    ...(iconPath ? [`Icon=${desktopValue(iconPath)}`] : []),
    "Terminal=false",
    "Categories=Utility;",
    `${LINUX_MARKER}=${id}`,
    "",
  ].join("\n");
}

function isPlainDirectory(fs, target) {
  try {
    const stat = fs.lstatSync(target);
    return stat.isDirectory() && !stat.isSymbolicLink();
  } catch {
    return false;
  }
}

function isPlainFile(fs, target) {
  try {
    const stat = fs.lstatSync(target);
    return stat.isFile() && !stat.isSymbolicLink();
  } catch {
    return false;
  }
}

function writeFileAtomic(fs, target, data, mode) {
  const temporary = `${target}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, data, { mode });
    fs.chmodSync(temporary, mode);
    fs.renameSync(temporary, target);
  } catch (error) {
    try {
      fs.rmSync(temporary, { force: true });
    } catch {
      /* nothing to clean */
    }
    throw error;
  }
}

function requireExecutable(execPath) {
  if (typeof execPath !== "string" || !nodePath.isAbsolute(execPath) || /[\u0000-\u001f]/.test(execPath)) {
    throw new Error("Saved apps can only be added from the installed Suparagent app.");
  }
}

function listNames(fs, dir, extension) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return [];
  }
  return entries.filter((entry) => entry.endsWith(extension) && !entry.startsWith(".")).map((entry) => entry.slice(0, -extension.length));
}

function folded(names, candidate) {
  return names.find((existing) => foldName(existing) === foldName(candidate));
}

// ---- macOS -------------------------------------------------------------------------------------------------

function macLauncher({ fs, homedir, exec }) {
  const dir = nodePath.join(homedir, "Applications", FOLDER);
  const plistMarker = (id) => new RegExp(`<key>${MARKER_KEY}</key>\\s*<string>${id}</string>`);

  const carries = (app, id) => {
    if (!isPlainDirectory(fs, app)) return false;
    try {
      return plistMarker(id).test(fs.readFileSync(nodePath.join(app, "Contents", "Info.plist"), "utf8"));
    } catch {
      return false;
    }
  };

  async function buildIcns(png, workDir, output) {
    const source = nodePath.join(workDir, "source.png");
    const set = nodePath.join(workDir, "AppIcon.iconset");
    fs.mkdirSync(set, { recursive: true });
    fs.writeFileSync(source, png);
    const sizes = [[16, 1], [16, 2], [32, 1], [32, 2], [128, 1], [128, 2], [256, 1], [256, 2], [512, 1], [512, 2]];
    for (const [size, scale] of sizes) {
      const pixels = size * scale;
      const file = nodePath.join(set, `icon_${size}x${size}${scale === 2 ? "@2x" : ""}.png`);
      await exec("/usr/bin/sips", ["-z", String(pixels), String(pixels), source, "--out", file]);
    }
    await exec("/usr/bin/iconutil", ["-c", "icns", set, "-o", output]);
    if (!isPlainFile(fs, output)) throw new Error("No icon was produced.");
  }

  return {
    async install({ id, name, png }) {
      const key = appKey(id);
      const link = appLink(id);
      fs.mkdirSync(dir, { recursive: true });
      const taken = listNames(fs, dir, ".app");
      const chosen = chooseLauncherName(name, taken, (candidate) => {
        const existing = folded(taken, candidate);
        return existing !== undefined && carries(nodePath.join(dir, `${existing}.app`), id);
      });
      const existing = folded(taken, chosen);
      const target = nodePath.join(dir, `${existing ?? chosen}.app`);
      const staging = nodePath.join(dir, `.suparagent-${key}.staging`);
      const backup = nodePath.join(dir, `.suparagent-${key}.old`);
      fs.rmSync(staging, { recursive: true, force: true });
      fs.mkdirSync(nodePath.join(staging, "Contents", "MacOS"), { recursive: true });
      fs.mkdirSync(nodePath.join(staging, "Contents", "Resources"), { recursive: true });
      try {
        let hasIcon = false;
        const work = nodePath.join(staging, ".icon-work");
        try {
          fs.mkdirSync(work, { recursive: true });
          await buildIcns(png, work, nodePath.join(staging, "Contents", "Resources", "AppIcon.icns"));
          hasIcon = true;
        } catch {
          // No icon is better than no launcher.
          fs.rmSync(nodePath.join(staging, "Contents", "Resources", "AppIcon.icns"), { force: true });
        }
        fs.rmSync(work, { recursive: true, force: true });
        fs.writeFileSync(nodePath.join(staging, "Contents", "Info.plist"), infoPlist({ id, name: chosen, key, hasIcon }));
        fs.writeFileSync(nodePath.join(staging, "Contents", "PkgInfo"), "APPL????");
        const script = nodePath.join(staging, "Contents", "MacOS", "launcher");
        fs.writeFileSync(script, launcherScript(link), { mode: 0o755 });
        fs.chmodSync(script, 0o755);

        fs.rmSync(backup, { recursive: true, force: true });
        const replacing = isPlainDirectory(fs, target);
        if (replacing) fs.renameSync(target, backup);
        try {
          fs.renameSync(staging, target);
        } catch (error) {
          if (replacing) fs.renameSync(backup, target);
          throw error;
        }
        fs.rmSync(backup, { recursive: true, force: true });
      } catch (error) {
        fs.rmSync(staging, { recursive: true, force: true });
        throw error;
      }
      return { name: chosen, files: [{ path: target, kind: "launcher" }] };
    },

    owns(record, id) {
      return record.kind === "launcher" && nodePath.dirname(record.path) === dir && record.path.endsWith(".app") && carries(record.path, id);
    },

    remove(record) {
      fs.rmSync(record.path, { recursive: true, force: true });
    },
  };
}

// ---- Windows -----------------------------------------------------------------------------------------------

function windowsLauncher({ fs, env, shell, execPath, userData }) {
  const appData = env.APPDATA;
  if (typeof appData !== "string" || !nodePath.isAbsolute(appData)) throw new Error("The Start Menu folder could not be found.");
  const dir = nodePath.join(appData, "Microsoft", "Windows", "Start Menu", "Programs", FOLDER);
  const iconDir = nodePath.join(userData, "app-icons");

  const carries = (file, id) => {
    if (!isPlainFile(fs, file)) return false;
    try {
      const shortcut = shell.readShortcutLink(file);
      return shortcut?.appUserModelId === appUserModelId(id) && shortcut?.args === appLink(id);
    } catch {
      return false;
    }
  };

  return {
    async install({ id, name, png }) {
      requireExecutable(execPath);
      const link = appLink(id);
      fs.mkdirSync(dir, { recursive: true });
      fs.mkdirSync(iconDir, { recursive: true });
      const taken = listNames(fs, dir, ".lnk");
      const chosen = chooseLauncherName(name, taken, (candidate) => {
        const existing = folded(taken, candidate);
        return existing !== undefined && carries(nodePath.join(dir, `${existing}.lnk`), id);
      });
      const file = nodePath.join(dir, `${folded(taken, chosen) ?? chosen}.lnk`);
      let ico;
      try {
        ico = icoFromPng(png);
      } catch {
        // Larger than an .ico may hold: use the tile instead.
        ico = icoFromPng(chooseIconPng({ id, name: chosen }).png);
      }
      const iconFile = nodePath.join(iconDir, `suparagent-app-${id}.ico`);
      writeFileAtomic(fs, iconFile, ico, 0o600);
      const written = shell.writeShortcutLink(file, "create", {
        target: execPath,
        args: link,
        description: chosen,
        icon: iconFile,
        iconIndex: 0,
        cwd: nodePath.dirname(execPath),
        appUserModelId: appUserModelId(id),
      });
      if (written === false || !carries(file, id)) {
        if (isPlainFile(fs, file)) fs.rmSync(file, { force: true });
        fs.rmSync(iconFile, { force: true });
        throw new Error("The Start Menu shortcut could not be created.");
      }
      return { name: chosen, files: [{ path: file, kind: "launcher" }, { path: iconFile, kind: "icon" }] };
    },

    owns(record, id) {
      if (record.kind === "launcher") return nodePath.dirname(record.path) === dir && record.path.endsWith(".lnk") && carries(record.path, id);
      return record.kind === "icon" && record.path === nodePath.join(iconDir, `suparagent-app-${id}.ico`) && isPlainFile(fs, record.path);
    },

    remove(record) {
      fs.rmSync(record.path, { force: true });
    },
  };
}

// ---- Linux -------------------------------------------------------------------------------------------------

function linuxLauncher({ fs, env, homedir, execPath }) {
  const dataHome = typeof env.XDG_DATA_HOME === "string" && nodePath.isAbsolute(env.XDG_DATA_HOME) ? env.XDG_DATA_HOME : nodePath.join(homedir, ".local", "share");
  const dir = nodePath.join(dataHome, "applications");
  const iconDir = nodePath.join(dataHome, "icons", "suparagent-apps");
  const fileFor = (id) => nodePath.join(dir, `suparagent-app-${id}.desktop`);
  const iconFor = (id) => nodePath.join(iconDir, `suparagent-app-${id}.png`);

  const carries = (file, id) => {
    if (!isPlainFile(fs, file)) return false;
    try {
      return fs.readFileSync(file, "utf8").split("\n").includes(`${LINUX_MARKER}=${id}`);
    } catch {
      return false;
    }
  };

  return {
    async install({ id, name, png }) {
      requireExecutable(execPath);
      const file = fileFor(id);
      if (fs.existsSync(file) && !carries(file, id)) throw new Error("A launcher with this name already exists and is not Suparagent's.");
      const shown = launcherName(name);
      fs.mkdirSync(dir, { recursive: true });
      fs.mkdirSync(iconDir, { recursive: true });
      writeFileAtomic(fs, iconFor(id), png, 0o644);
      writeFileAtomic(fs, file, desktopEntry({ id, name: shown, execPath, link: appLink(id), iconPath: iconFor(id) }), 0o644);
      return { name: shown, files: [{ path: file, kind: "launcher" }, { path: iconFor(id), kind: "icon" }] };
    },

    owns(record, id) {
      if (record.kind === "launcher") return record.path === fileFor(id) && carries(record.path, id);
      return record.kind === "icon" && record.path === iconFor(id) && isPlainFile(fs, record.path);
    },

    remove(record) {
      fs.rmSync(record.path, { force: true });
    },
  };
}

/** The launcher for `platform`, or null where launchers are not supported. */
function createLauncher(context) {
  switch (context.platform) {
    case "darwin":
      return macLauncher(context);
    case "win32":
      return windowsLauncher(context);
    case "linux":
      return linuxLauncher(context);
    default:
      return null;
  }
}

module.exports = { createLauncher, infoPlist, launcherScript, desktopEntry, execArgument, shellQuote, APP_ID };
