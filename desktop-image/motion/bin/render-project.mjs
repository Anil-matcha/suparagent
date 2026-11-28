#!/usr/bin/env node
// render-project.mjs <project-dir> <out.mp4> [max-seconds=600] [max-mb=200]
//
// Same contract as the bash `render-project`, written in Node so it also runs on Windows and macOS
// hosts (HyperFrames already needs Node 22). Checks a HyperFrames project, then renders it DETACHED
// with a time limit and an output size cap. Returns at once; poll <project-dir>/.render.done (it
// appears when the render is over and holds the exit code); the render's output is in .render.log.
//
//   0   rendered, file kept          124  stopped at the time limit
//   90  output over the size cap     91   render ended 0 but no output file
//   other  the render failed (see .render.log)
// Exit codes of this launcher: 0 started, 2 bad arguments, 3 check failed (see .check.json),
// 4 a needed tool is missing, 5 a render is already running in that project (lock: .render.lock).
//
// HYPERFRAMES_BIN overrides the command name (a full path to the hyperframes binary or .cmd shim).
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const win = process.platform === "win32";
const self = fileURLToPath(import.meta.url);
const hf = process.env.HYPERFRAMES_BIN || "hyperframes";

function die(code, msg) {
  console.error(`render-project: ${msg}`);
  process.exit(code);
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
}

function killTree(child) {
  if (!child.pid) return;
  if (win) {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
  } else {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      try {
        child.kill("SIGKILL");
      } catch {}
    }
  }
}

function writeDone(dir, rc) {
  const tmp = path.join(dir, ".render.done.tmp");
  fs.writeFileSync(tmp, `${rc}\n`);
  fs.renameSync(tmp, path.join(dir, ".render.done"));
}

// ---- worker: runs detached from the launcher ----
if (process.argv[2] === "--worker") {
  const [dir, out, limitS, capMb] = process.argv.slice(3);
  const limit = Number(limitS);
  process.chdir(dir);
  fs.writeFileSync(path.join(dir, ".render.lock", "pid"), String(process.pid));
  const log = fs.openSync(path.join(dir, ".render.log"), "w");
  const child = spawn(hf, ["render", "--quality", "looks", "-o", out], {
    cwd: dir,
    stdio: ["ignore", log, log],
    shell: win, // .cmd shims need a shell on Windows
    windowsHide: true,
    detached: !win, // own process group so the whole tree can be stopped
    env: { ...process.env, HYPERFRAMES_RENDER_DETACHED: "1" },
  });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    killTree(child);
  }, limit * 1000);
  const finish = (code) => {
    clearTimeout(timer);
    let rc = timedOut ? 124 : code ?? 1;
    if (rc === 0) {
      let size = 0;
      try {
        size = fs.statSync(out).size;
      } catch {}
      if (size === 0) rc = 91;
      else if (size > Number(capMb) * 1024 * 1024) {
        fs.rmSync(out, { force: true });
        rc = 90;
      }
    }
    fs.rmSync(path.join(dir, ".render.lock"), { recursive: true, force: true });
    writeDone(dir, rc);
    process.exit(0);
  };
  child.on("error", () => finish(127));
  child.on("close", (code) => finish(code));
} else {
  // ---- launcher ----
  const args = process.argv.slice(2);
  if (args.length < 2 || args.length > 4) {
    die(2, "usage: render-project <project-dir> <out.mp4> [max-seconds=600] [max-mb=200]");
  }
  const [dirArg, outArg, limitS = "600", capS = "200"] = args;
  if (!/^\d+$/.test(limitS) || !/^\d+$/.test(capS)) die(2, "max-seconds and max-mb must be whole numbers");
  if (Number(limitS) < 1 || Number(capS) < 1) die(2, "max-seconds and max-mb must be at least 1");
  if (!fs.existsSync(dirArg) || !fs.statSync(dirArg).isDirectory()) die(2, `no such project folder: ${dirArg}`);
  const dir = fs.realpathSync(dirArg);
  if (!fs.existsSync(path.join(dir, "index.html"))) die(2, `no index.html in ${dir}`);
  const out = path.isAbsolute(outArg) ? outArg : path.join(dir, outArg);
  try {
    fs.mkdirSync(path.dirname(out), { recursive: true });
  } catch {
    die(2, "cannot create the output folder");
  }

  const probe = spawnSync(hf, ["--version"], { shell: win, windowsHide: true, stdio: "ignore" });
  if (probe.error || probe.status !== 0) die(4, "hyperframes is not installed");

  // One render per project. mkdir is atomic, so two launches close together cannot both pass. The lock
  // holds the pid of its owner (this launcher during the check, then the worker); a lock whose owner is
  // gone is stale and is cleared once.
  const lock = path.join(dir, ".render.lock");
  const pidFile = path.join(lock, "pid");
  const take = () => {
    try {
      fs.mkdirSync(lock);
      return true;
    } catch {
      return false;
    }
  };
  if (!take()) {
    let owner = NaN;
    try {
      owner = parseInt(fs.readFileSync(pidFile, "utf8"), 10);
    } catch {}
    if (Number.isInteger(owner) && alive(owner)) die(5, `a render is already running here (pid ${owner})`);
    fs.rmSync(lock, { recursive: true, force: true });
    if (!take()) die(5, "a render is already running here");
  }
  fs.writeFileSync(pidFile, String(process.pid));
  for (const f of [".render.done", ".render.done.tmp", ".render.log", ".check.json", ".check.err"]) {
    fs.rmSync(path.join(dir, f), { force: true });
  }
  fs.rmSync(out, { force: true });

  const checkOut = fs.openSync(path.join(dir, ".check.json"), "w");
  const checkErr = fs.openSync(path.join(dir, ".check.err"), "w");
  const chk = spawnSync(hf, ["check", "--json"], {
    cwd: dir,
    stdio: ["ignore", checkOut, checkErr],
    shell: win,
    windowsHide: true,
  });
  if (chk.error || chk.status !== 0) {
    fs.rmSync(lock, { recursive: true, force: true });
    console.log(`check failed: see ${path.join(dir, ".check.json")} and ${path.join(dir, ".check.err")}`);
    process.exit(3);
  }

  const w = spawn(process.execPath, [self, "--worker", dir, out, limitS, capS], {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  });
  w.unref();
  fs.writeFileSync(pidFile, String(w.pid));
  console.log(
    `started: poll ${path.join(dir, ".render.done")} (exit code inside); log ${path.join(dir, ".render.log")}; output ${out}`,
  );
}
